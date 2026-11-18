"""Donut as an extractor (RF09; plan section 9.5; decisions D09, D11): greedy decoding of a page from the prompt of a task.

``DonutExtractor`` takes a model and a processor (any objects with the interface of Hugging Face's ``VisionEncoderDecoderModel`` and ``DonutProcessor``), the task token, ``max_length`` and
the device, so the default test session runs it on fakes with no torch and no checkpoint. ``load_donut`` is the only place that imports torch and transformers and builds the real ones from a
folder of ``safetensors`` (the artifact of the launch: ``artifact.load_artifact`` checks its hashes first, so nothing here reads a file that was not checked). ``parse_generation`` turns the
generated sequence into the tree: it cuts the prompt of the task and the end and padding tokens off and reads what is left with ``token2json``; what cannot be read is ``{}``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PIL import Image

from invoiceflow.config import INFER_DEVICES, SealedConfig
from invoiceflow.model.extractors import ExtractorError, RawFields
from invoiceflow.model.serialize import token2json


def resolve_device(setting: str, cuda_available: bool) -> str:
    """``auto`` is CUDA when there is one and the CPU otherwise; ``cuda`` without CUDA is refused (the reading would silently be ten times slower)."""
    if setting not in INFER_DEVICES:
        raise ValueError(f"[model] infer_device: {setting!r} no es un valor conocido ({', '.join(INFER_DEVICES)}); se esperaba uno de ellos, no gpu")
    if setting == "cuda" and not cuda_available:
        raise ExtractorError("[model] infer_device = cuda y CUDA no está disponible")
    return "cuda" if setting == "cuda" or (setting == "auto" and cuda_available) else "cpu"


def parse_generation(sequence: str, task_token: str, eos: str | None, pad: str | None) -> RawFields:
    """The tree of a generated `sequence`: without the prompt, the end and the padding tokens. A sequence with no structure is ``{}``; one with several trees at the top gives the first."""
    text = sequence
    for token in (eos, pad):
        if token:
            text = text.replace(token, "")
    text = text.replace(task_token, "", 1).strip()
    tree = token2json(text)
    if isinstance(tree, list):
        tree = tree[0] if tree else {}
    return tree if isinstance(tree, dict) else {}


class DonutExtractor:
    """Reads a page with `model` and `processor`: ``extract`` is one greedy generation (``num_beams = 1``) from the task's prompt, at most `max_length` tokens."""

    def __init__(self, *, model: Any, processor: Any, task_token: str, max_length: int, device: str, model_version: str, no_grad: Callable[[], Any] | None = None) -> None:
        self.model, self.processor, self.task_token = model, processor, task_token
        self.max_length, self.device, self.model_version = max_length, device, model_version
        self._no_grad = no_grad

    def extract(self, image: Image.Image) -> RawFields:
        tokenizer = self.processor.tokenizer
        pixel_values = self.processor(image.convert("RGB"), return_tensors="pt").pixel_values.to(self.device)
        prompt = tokenizer(self.task_token, add_special_tokens=False, return_tensors="pt").input_ids.to(self.device)
        no_grad = self._no_grad
        if no_grad is None:
            import torch

            no_grad = torch.no_grad
        with no_grad():
            output = self.model.generate(
                pixel_values, decoder_input_ids=prompt, max_length=self.max_length, early_stopping=True, pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id, use_cache=True, num_beams=1, bad_words_ids=[[tokenizer.unk_token_id]], return_dict_in_generate=True)
        sequence = tokenizer.batch_decode(output.sequences)[0]
        return parse_generation(sequence, self.task_token, tokenizer.eos_token, tokenizer.pad_token)


def load_donut(view: SealedConfig, registry_row: Mapping[str, Any], models_dir: Path) -> DonutExtractor:
    """The real extractor from the folder `models_dir`/`registry_row["artifact_name"]` (``safetensors`` only, no network): the page is resized to ``[model] input_size``."""
    import torch

    from invoiceflow.model.artifact import load_artifact

    folder = Path(models_dir) / str(registry_row["artifact_name"])
    device = resolve_device(view.model.infer_device, torch.cuda.is_available())
    processor, model = load_artifact(folder, registry_row.get("artifact_sha256"))   # the hashes are checked before a weight is read (D13, AC19)
    height, width = view.model.input_size
    processor.image_processor.size = {"height": height, "width": width}
    model.eval().to(device)
    return DonutExtractor(model=model, processor=processor, task_token=view.model.task_invoice, max_length=view.model.max_length, device=device,
                          model_version=str(registry_row["model_version"]), no_grad=torch.no_grad)


__all__ = ["DonutExtractor", "load_donut", "parse_generation", "resolve_device"]
