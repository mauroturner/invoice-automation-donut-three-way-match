"""The fine-tune of the document model (RF16, AC17; plan sections 9.1 and 9.2; decisions D09, D11, D13). Nothing here imports torch when the package is imported.

* ``build_train_set`` mixes CORD's training receipts (task ``[model] task_cord``) and the synthetic training invoices (task ``[model] task_invoice``) in an order fixed by ``[model] seed``;
  the images are loaders called when the page is needed, so building the set decodes nothing. CORD's validation and test never enter it.
* ``prepare_tokens`` adds to the tokenizer the ``<s_key>``/``</s_key>`` of every key of the targets, ``<sep/>`` and the two task tokens that it lacks, and grows the decoder's embeddings only
  as far as the tokenizer needs (a decoder with spare rows keeps them).
* ``fine_tune`` runs ``epochs`` epochs from the model it is given: every epoch visits every item (CORD's too, or the model forgets it: C01), in an order fixed by the seed and the epoch;
  AdamW with a linear warm-up and a linear decay to 0 (``lr_at``), batches of ``batch_size`` and ``grad_accumulation`` batches per optimizer step, bf16 autocast on CUDA, the gradient clipped to
  1.0, and the seeds of Python, NumPy and torch set for the run and put back after it. After each candidate epoch the model is saved as ``out_dir/epoch-<n>`` in ``safetensors``
  with its processor (the artifact is sealed from that folder later: ``artifact.seal_folder``). The real kind needs CUDA and is refused without it; the tiny kind trains on the CPU.
* ``load_base`` reads the pinned checkpoint from the local cache only. The pinned revision ships ``pytorch_model.bin`` and no ``safetensors`` file (it was checked, decision D47): that
  one read is made with ``weights_only=True``, which refuses a pickle that is anything but tensors, and the launch saves the result as ``safetensors`` so nothing else reads a pickle.
"""

from __future__ import annotations

import math
import os
import random
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from invoiceflow.config import SealedConfig
from invoiceflow.model.serialize import invoice_target, json2token, new_special_tokens

SOURCES = ("cord", "synth")
MAX_NORM = 1.0
IGNORE = -100
PREFIX = "epoch-"


class TrainingRefusedError(RuntimeError):
    """The fine-tune cannot run here (no CUDA for the real kind) or its input is not what it needs. The command line exits with 2 (runbook R5)."""

    EXIT_CODE = 2


@dataclass(frozen=True)
class TrainItem:
    image: Callable[[], Image.Image]      # called when the page is needed: building the set decodes nothing
    target: str                           # the task token and the target's sequence; the end token is added when it is tokenized
    task: str
    source: str                           # "cord" or "synth"


@dataclass(frozen=True)
class EpochReport:
    epoch: int
    steps: int
    mean_loss: float
    items: int
    cord_items: int
    synth_items: int
    step_losses: tuple[float, ...] = field(default_factory=tuple)
    folder: Path | None = None            # set when the epoch is a candidate


def build_train_set(cord_index: Any, synth_train: Iterable[Any], view: SealedConfig, *, cord_image: Callable[[str], Image.Image],
                    synth_image: Callable[[Any], Image.Image]) -> list[TrainItem]:
    """The training items: every receipt of ``cord_index.entries["train"]`` and every invoice of `synth_train`, in an order that is a function of ``[model] seed``. `cord_image(receipt_key)` and
    `synth_image(truth)` give each page when it is asked for."""
    items = [TrainItem((lambda key=entry.receipt_key: cord_image(key)), view.model.task_cord + json2token(entry.target), view.model.task_cord, "cord")
             for entry in cord_index.entries["train"]]
    items += [TrainItem((lambda truth=truth: synth_image(truth)), view.model.task_invoice + json2token(invoice_target(truth)), view.model.task_invoice, "synth")
              for truth in synth_train]
    random.Random(f"invoiceflow.trainset:{view.model.seed}").shuffle(items)
    return items


def prepare_tokens(processor: Any, model: Any, targets: Iterable[Any], view: SealedConfig) -> list[str]:
    """Add to ``processor.tokenizer`` the special tokens the `targets` and the two task tokens need and it lacks, and give the decoder rows for them; returns the tokens added (none the
    second time). The decoder is resized only when the tokenizer outgrows it."""
    tokenizer = processor.tokenizer
    new = new_special_tokens(targets, existing=tokenizer.get_vocab(), extra=(view.model.task_cord, view.model.task_invoice))
    new = [token for token in new if token not in tokenizer.get_vocab()]
    if not new:
        return []
    tokenizer.add_tokens(new, special_tokens=True)
    weight = model.decoder.get_input_embeddings().weight
    if len(tokenizer) > weight.shape[0]:
        import torch

        devices = [weight.device.index or 0] if weight.device.type == "cuda" else []
        with torch.random.fork_rng(devices=devices):   # the new rows are drawn at random: from the seed, and the caller's generator is left as it was
            torch.manual_seed(view.model.seed)
            model.decoder.resize_token_embeddings(len(tokenizer))
        model.config.decoder.vocab_size = len(tokenizer)
    return new


def lr_at(step: int, total_steps: int, warmup_steps: int, base_lr: float) -> float:
    """The learning rate of optimizer step `step` (counting from 0): a linear ramp that reaches `base_lr` at the last warm-up step, then a linear decay that reaches 0 at `total_steps`."""
    if step < warmup_steps:
        return base_lr * (step + 1) / warmup_steps
    return max(0.0, base_lr * (total_steps - step) / max(total_steps - warmup_steps, 1))


def _device(view: SealedConfig) -> str:
    import torch

    if view.model.kind == "tiny":
        return "cpu"
    if not torch.cuda.is_available():
        raise TrainingRefusedError("el ajuste real necesita CUDA y no está disponible; sin ella no corre (salida 2, docs/runbook.md R5)")
    return "cuda"


def _encode(items: Sequence[TrainItem], tokenizer: Any, max_length: int) -> list[list[int]]:
    ids = []
    for position, item in enumerate(items):
        if item.source not in SOURCES:
            raise ValueError(f"el elemento {position} del conjunto de ajuste tiene la fuente {item.source!r}; se esperaba una de {', '.join(SOURCES)}")
        sequence = tokenizer(item.target + tokenizer.eos_token, add_special_tokens=False).input_ids
        if len(sequence) > max_length:
            raise ValueError(f"el objetivo del elemento {position} ({item.source}) tiene {len(sequence)} fichas y max_length es {max_length}: no se corta (M05)")
        ids.append(sequence)
    return ids


def _batch(chunk: Sequence[int], items: Sequence[TrainItem], ids: Sequence[list[int]], processor: Any, pad: int, device: str) -> dict[str, Any]:
    import torch

    pixel_values = torch.cat([processor(items[i].image().convert("RGB"), return_tensors="pt").pixel_values for i in chunk])
    width = max(len(ids[i]) for i in chunk) - 1
    decoder_input = torch.full((len(chunk), width), pad, dtype=torch.long)
    labels = torch.full((len(chunk), width), IGNORE, dtype=torch.long)
    for row, i in enumerate(chunk):
        sequence = torch.tensor(ids[i], dtype=torch.long)
        decoder_input[row, :len(sequence) - 1] = sequence[:-1]
        labels[row, :len(sequence) - 1] = sequence[1:]
    return {"pixel_values": pixel_values.to(device), "decoder_input_ids": decoder_input.to(device), "labels": labels.to(device)}


def _save_candidate(model: Any, processor: Any, out_dir: Path, epoch: int) -> Path:
    final = out_dir / f"{PREFIX}{epoch}"
    scratch = out_dir / f"{PREFIX}{epoch}.partial"
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        model.save_pretrained(scratch, safe_serialization=True)
        processor.save_pretrained(scratch)
        os.replace(scratch, final)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return final


def fine_tune(items: Sequence[TrainItem], processor: Any, model: Any, view: SealedConfig, *, out_dir: Path,
              on_epoch: Callable[[EpochReport], None] | None = None) -> list[Path]:
    """Fine-tune `model` on `items` for ``[model] epochs`` epochs (see the module's text); returns the folder of each candidate epoch, in order. Refused with ``TrainingRefusedError``
    without CUDA when ``[model] kind`` is not the tiny one, and with ``ValueError`` on an empty set, an unknown source or a target that does not fit in ``max_length``; in each case
    nothing is written and the model is untouched."""
    import numpy as np
    import torch

    m = view.model
    if not items:
        raise ValueError("el conjunto de ajuste está vacío")
    out_dir = Path(out_dir)
    device = _device(view)
    tokenizer = processor.tokenizer
    ids = _encode(items, tokenizer, m.max_length)
    if out_dir.exists() and any(out_dir.glob(f"{PREFIX}*")):
        raise TrainingRefusedError(f"{out_dir.name} ya tiene épocas candidatas guardadas: un ajuste no escribe sobre otro (docs/runbook.md R5)")
    out_dir.mkdir(parents=True, exist_ok=True)
    per_step = m.batch_size * m.grad_accumulation
    steps_per_epoch = math.ceil(len(items) / per_step)
    total_steps = steps_per_epoch * m.epochs
    states = (random.getstate(), np.random.get_state())
    folders: list[Path] = []
    try:
        random.seed(m.seed)
        np.random.seed(m.seed)
        with torch.random.fork_rng(devices=list(range(torch.cuda.device_count())) if device == "cuda" else []):
            torch.manual_seed(m.seed)
            model.to(device).train()
            optimizer = torch.optim.AdamW(model.parameters(), lr=m.learning_rate)
            autocast = torch.autocast("cuda", dtype=torch.bfloat16, enabled=m.bf16 and device == "cuda")
            step = 0
            for epoch in range(1, m.epochs + 1):
                order = list(range(len(items)))
                random.Random(f"invoiceflow.train:{m.seed}:{epoch}").shuffle(order)
                losses: list[float] = []
                for start in range(0, len(order), per_step):
                    group = order[start:start + per_step]
                    chunks = [group[i:i + m.batch_size] for i in range(0, len(group), m.batch_size)]
                    group_loss = 0.0
                    for chunk in chunks:
                        batch = _batch(chunk, items, ids, processor, tokenizer.pad_token_id, device)
                        with autocast:
                            loss = model(**batch).loss
                        (loss / len(chunks)).backward()
                        group_loss += float(loss.detach().float()) / len(chunks)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), MAX_NORM)
                    for parameters in optimizer.param_groups:
                        parameters["lr"] = lr_at(step, total_steps, m.warmup_steps, m.learning_rate)
                    optimizer.step()
                    optimizer.zero_grad(set_to_none=True)
                    losses.append(group_loss)
                    step += 1
                folder = None
                if epoch in m.candidate_epochs:
                    model.eval()
                    folder = _save_candidate(model, processor, out_dir, epoch)
                    model.train()
                    folders.append(folder)
                cord = sum(1 for item in items if item.source == "cord")
                report = EpochReport(epoch, len(losses), sum(losses) / len(losses), len(items), cord, len(items) - cord, tuple(losses), folder)
                if on_epoch is not None:
                    on_epoch(report)
            model.eval()
    finally:
        random.setstate(states[0])
        np.random.set_state(states[1])
    return folders


def load_base(view: SealedConfig, *, source: Path | str | None = None) -> tuple[Any, Any]:
    """``(processor, model)`` of the base checkpoint, from the local cache only (``local_files_only``, no network, no remote code): the pinned repository at the pinned revision, or a local
    folder `source` (a stand-in in the tests). ``safetensors`` is asked for first; a repository that ships only ``pytorch_model.bin``, as the pinned revision does, is read once with
    ``weights_only=True``, which refuses a pickle that holds anything but tensors."""
    from transformers import DonutProcessor, VisionEncoderDecoderModel

    where = view.model.base if source is None else str(source)
    common: dict[str, Any] = {"local_files_only": True, "trust_remote_code": False}
    if source is None:
        common["revision"] = view.model.base_revision
    try:
        model = VisionEncoderDecoderModel.from_pretrained(where, use_safetensors=True, **common)
    except OSError:
        model = VisionEncoderDecoderModel.from_pretrained(where, use_safetensors=False, weights_only=True, **common)
    processor = DonutProcessor.from_pretrained(where, **common)
    return processor, model
