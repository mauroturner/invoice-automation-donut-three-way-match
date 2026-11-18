"""The ``fit_model`` task: the launch freezes the model and every later run checks that it is still the frozen one (RF16, RF17; D12, D13, D21; M02-M06; plan sections 3, 9 and 13).

At the **launch** (the registry has no model), in this order and before anything is written:

1. **M05**: each partition has the size the configuration says (and, once the tokens exist, each target fits in ``max_length``);
2. **M06**: the oracle B3 (the true fields through the real rules) on ``validation_known`` and ``validation_new`` sends every planted invoice to review and posts every clean one. It does not
   depend on the model, so it runs *first*: a rule that is not what the generator planted would waste an hour of fine-tuning;
3. the checkpoint is measured on CORD's validation (TED), the tokens of the invoice task are added, and the model is fitted on CORD's training receipts and the synthetic training invoices,
   saving a folder per candidate epoch;
4. each candidate is sealed (its files hashed) and measured: TED on CORD's validation and the exact-line rate on the two synthetic validations, each figure with ``fit_splits``;
5. **M04**: ``choose`` picks the epoch by the rule of D12 or stops with ``ModelChoiceError``;
6. the seal's outcome is decided (``SealMismatchError`` when the configuration is the sealed one and the values are not), and only then the chosen folder becomes the artifact
   ``model-v1-<config12>``, the registry row is inserted and the launch part of the seal is written once.

In **every other run** nothing is fitted: the artifact is verified against the registered hash before a weight is read (M02) and the seal is read (M03: another configuration is ``post_seal``).

The launch never reads the synthetic truth file (D23): the training labels come from the generator itself, which is a pure function of the configuration, and the test partitions are never generated
here. CORD's test split is never opened. ``fit_model`` takes a ``LaunchContext`` instead of the pipeline's context (U19 wraps it in the task).
"""

from __future__ import annotations

import gc
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from invoiceflow.baselines import oracle
from invoiceflow.config import PipelineConfig, SealedConfig
from invoiceflow.cord import CordIndex, IndexEntry, read_split
from invoiceflow.intake import load_page
from invoiceflow.metrics import exact_lines, ted_accuracy
from invoiceflow.model import extractors
from invoiceflow.model.artifact import seal_folder, verify_artifact
from invoiceflow.model.choose import Candidate, Choice, choose
from invoiceflow.model.serialize import check_block_sizes, invoice_target, json2token
from invoiceflow.model.train import build_train_set, fine_tune, load_base, prepare_tokens
from invoiceflow.normalize import normalize
from invoiceflow.registry import (
    MODEL_VERSION,
    RunMeta,
    config_fingerprint,
    fingerprint_document,
    launch_seal_outcome,
    read_registry,
    register_model,
    sealed_view,
    seal_state,
    write_launch_seal,
)
from invoiceflow.synth.invoices import FIT_SPLITS, InvoiceTruth, invoices
from invoiceflow.synth.master import master, to_jsonable
from invoiceflow.synth.render import render
from invoiceflow.validate import BLOCKING, RuleResult

logger = logging.getLogger(__name__)

CHECKPOINT_SPLITS = "checkpoint"           # the fit_splits of the unmodified checkpoint's figure: it was not fitted here
ORACLE_SPLITS = "none"                     # the oracle has no model
EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "pdf": ".pdf"}
WARNING = "advertencia"
# rule: (name, severity, scope, aggregation), as in the plan's table (section 6)
MODEL_RULES: dict[str, tuple[str, str, str, str]] = {
    "M02": ("El artefacto y el tokenizador tienen la huella registrada antes de leerlos; solo safetensors", BLOCKING, "modelo", "per_run"),
    "M03": ("La configuración es la sellada", WARNING, "modelo", "latest"),
    "M04": ("Alguna candidata conserva CORD: no pierde más de cord_val_tolerance puntos de TED en la validación", BLOCKING, "modelo", "latest"),
    "M05": ("Tamaños de las particiones como en la configuración; cada objetivo cabe en max_length", BLOCKING, "modelo", "latest"),
    "M06": ("El oráculo manda a revisión el 100 % de las sembradas y carga el 100 % de las limpias", BLOCKING, "modelo", "latest"),
}


class LaunchError(RuntimeError):
    """The launch cannot run with what it was given, or the registry, the seal and the files do not agree (nothing was written). The command line exits with 2."""

    EXIT_CODE = 2


class OracleMismatchError(LaunchError):
    """M06: with the true fields the rules did not give the decision the generator planted for some validation invoice (runbook R5)."""


@dataclass(frozen=True)
class LaunchContext:
    """What the launch is given: the configuration, the warehouse, the run, the hashes the sources had when they were checked, the CORD index and, to be replaced by a test, where the pages
    and the base model come from (``cord_pages(receipt_key)`` and ``synth_page(truth)`` give an image; ``base(view)`` gives ``(processor, model)``)."""

    cfg: PipelineConfig
    conn: sqlite3.Connection
    run: RunMeta
    observed: Mapping[str, str]
    cord_index: CordIndex
    cord_pages: Callable[[str], Image.Image] | None = None
    synth_page: Callable[[InvoiceTruth], Image.Image] | None = None
    base: Callable[[SealedConfig], tuple[Any, Any]] | None = None


@dataclass(frozen=True)
class LaunchResult:
    """What the task reports: ``mode`` (``launch`` or ``verify``), ``seal`` (``written``, ``unchanged`` or ``post_seal`` after a launch; ``match`` or ``post_seal`` after a check), the model,
    its hashes, the choice with every figure, the results of the model rules, the rows written and the launch part of the seal."""

    mode: str
    seal: str
    model_version: str
    config_sha256: str
    artifact_sha256: str
    choice: dict[str, Any]
    rules: list[RuleResult]
    rows: dict[str, int] = field(default_factory=dict)
    document: dict[str, Any] | None = None


class CordPages:
    """The pages of CORD's training and validation receipts by key. They are read from the files the first time one is asked for, once (the test split is not one of them)."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._bytes: dict[str, bytes] | None = None

    def __call__(self, key: str) -> Image.Image:
        import io

        if self._bytes is None:
            self._bytes = {r.receipt_key: r.image_bytes for split in ("train", "validation") for r in read_split(self.cfg, split)}
        with Image.open(io.BytesIO(self._bytes[key])) as image:
            return image.convert("RGB")


def _synth_pages(view: SealedConfig, folder: Path) -> Callable[[InvoiceTruth], Image.Image]:
    """The page of a synthetic invoice as the flow reads it: the file is rendered once into `folder` and loaded through the intake each time."""

    def page(truth: InvoiceTruth) -> Image.Image:
        path = folder / f"{truth.invoice_key}{EXTENSIONS[truth.file_format]}"
        if not path.exists():
            path.write_bytes(render(truth, view))
        return load_page(path, view)

    return page


def _result(rule_id: str, passed: bool, **detail: Any) -> RuleResult:
    name, severity, scope, aggregation = MODEL_RULES[rule_id]
    return RuleResult(rule_id, name, severity, scope, aggregation, passed, 0, (), detail)


def config_sha256_of(ctx: LaunchContext) -> str:
    """The ``config_sha256`` of the configuration this context runs under."""
    return config_fingerprint(sealed_view(ctx.cfg, ctx.observed))


def cord_ted(extractor: Any, entries: Sequence[IndexEntry], pages: Callable[[str], Image.Image]) -> float:
    """The mean TED accuracy, in points (0-100), of `extractor` over CORD receipts `entries`, with the CORD task's prompt."""
    scores = [ted_accuracy(extractor.extract(pages(entry.receipt_key)), entry.target) for entry in entries]
    return 100.0 * sum(scores) / len(scores)


def exact_rate(extractor: Any, truths: Sequence[InvoiceTruth], page: Callable[[InvoiceTruth], Image.Image]) -> float:
    """The exact-line rate (0-1) of `extractor` over invoices `truths`: lines read exactly over lines of the truth."""
    exact = total = 0
    for truth in truths:
        got, lines = exact_lines(normalize(extractor.extract(page(truth))), truth.lines)
        exact, total = exact + got, total + lines
    return exact / total if total else 0.0


def _cord_twin(extractor: Any, view: SealedConfig) -> Any:
    """The same model and processor asked with CORD's task token."""
    from invoiceflow.model.donut import DonutExtractor

    return DonutExtractor(model=extractor.model, processor=extractor.processor, task_token=view.model.task_cord, max_length=extractor.max_length, device=extractor.device,
                          model_version=extractor.model_version, no_grad=extractor._no_grad)


def _checkpoint_extractor(view: SealedConfig, processor: Any, model: Any) -> Any:
    import torch

    from invoiceflow.model.donut import DonutExtractor, resolve_device

    device = "cpu" if view.model.kind == "tiny" else resolve_device(view.model.infer_device, torch.cuda.is_available())
    model.eval().to(device)
    max_length = view.model.max_length
    if view.model.kind == "tiny":                       # the tiny double never emits the end token: its four reads stop at its own short length (D31)
        from invoiceflow.model.tiny import read_length

        max_length = read_length(view)
    return DonutExtractor(model=model, processor=processor, task_token=view.model.task_cord, max_length=max_length, device=device, model_version="checkpoint",
                          no_grad=torch.no_grad)


def _free() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _rounded_digest(document: Any) -> str:
    from invoiceflow.registry import _rounded

    return hashlib.sha256(json.dumps(_rounded(document), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _stamp(run: RunMeta) -> str:
    return run.as_of.strftime("%Y-%m-%d %H:%M:%S")


def fit_model(ctx: LaunchContext) -> LaunchResult:
    """The task of the launch and of the runs after it (see the module)."""
    view = sealed_view(ctx.cfg, ctx.observed)
    existing = read_registry(ctx.conn, MODEL_VERSION)
    if existing is not None:
        return _verify(ctx, view, existing)
    return _launch(ctx, view)


def _verify(ctx: LaunchContext, view: SealedConfig, row: Mapping[str, Any]) -> LaunchResult:
    folder = Path(ctx.cfg.paths.models_dir) / row["artifact_name"]
    verify_artifact(folder, row["artifact_sha256"])                                                   # M02: the hashes, before a weight is read
    state = seal_state(ctx.cfg.paths.seal, config_fingerprint(view))
    if state == "none":
        raise LaunchError("no hay sello: el lanzamiento lo escribe y esta corrida no es el lanzamiento (docs/runbook.md, R7)")
    rules = [_result("M02", True, artifact=row["artifact_name"]), _result("M03", state == "match", state=state)]
    logger.info("fit_model: model %s verified, artifact %s, seal %s", row["model_version"], row["artifact_sha256"][:12], state)
    choice = json.loads(row["choice_json"]).get("choice", {})
    return LaunchResult("verify", state, row["model_version"], row["config_sha256"], row["artifact_sha256"], choice, rules)


def _oracle_check(view: SealedConfig, master_: Any, truths: Sequence[InvoiceTruth]) -> dict[str, Any]:
    """M06: the oracle's decision is the one the generator planted for every validation invoice."""
    decisions = oracle(view, master_, truths)
    wrong = sorted(t.invoice_key for t in truths if decisions[t.invoice_key].decision != t.expected_decision)
    if wrong:
        raise OracleMismatchError(f"M06: con los campos verdaderos, {len(wrong)} de {len(truths)} facturas de validación no tienen la decisión que el generador sembró, por ejemplo {wrong[:3]}: "
                                  "las reglas no son las que el generador supone y no se ajusta ningún modelo (docs/runbook.md, R5)")
    planted = sum(1 for t in truths if t.planted != "none")
    return {"fit_splits": ORACLE_SPLITS, "invoices": len(truths), "planted": planted, "posted": len(truths) - planted, "wrong": 0}


def _launch(ctx: LaunchContext, view: SealedConfig) -> LaunchResult:
    cfg = ctx.cfg
    seal_path, models_dir = Path(cfg.paths.seal), Path(cfg.paths.models_dir)
    config_sha256 = config_fingerprint(view)
    artifact_name = f"model-{MODEL_VERSION}-{config_sha256[:12]}"
    final = models_dir / artifact_name
    if final.exists():
        raise LaunchError(f"{artifact_name} existe en {models_dir.name} y el registro no lo tiene: un artefacto que nadie registró no se adopta ni se borra; quítelo a mano (docs/runbook.md, R6)")
    master_ = master(view)
    truths = {split: invoices(view, master_, split, None, seal_path=seal_path) for split in FIT_SPLITS}
    check_block_sizes(view, {split: len(items) for split, items in truths.items()}, {})                  # M05, the sizes
    validation = [*truths["validation_known"], *truths["validation_new"]]
    oracle_figures = _oracle_check(view, master_, validation)                                            # M06, before any fit
    cord_pages = ctx.cord_pages or CordPages(cfg)
    validation_receipts = ctx.cord_index.entries["validation"]
    train_receipts = ctx.cord_index.entries["train"]
    models_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".launch-", dir=models_dir))
    try:
        (work / "pages").mkdir()
        synth_page = ctx.synth_page or _synth_pages(view, work / "pages")
        invoice_targets = [invoice_target(t) for items in truths.values() for t in items]
        cord_targets = [e.target for e in (*train_receipts, *validation_receipts)]
        targets = invoice_targets + cord_targets
        if ctx.base is not None:
            processor, model = ctx.base(view)
        elif view.model.kind == "tiny":
            from invoiceflow.model.tiny import tiny_model, vocab_of

            sequences = [view.model.task_invoice + json2token(t) for t in invoice_targets] + [view.model.task_cord + json2token(t) for t in cord_targets]
            processor, model = tiny_model(vocab_of(sequences), view)
        else:
            processor, model = load_base(view)
        height, width = view.model.input_size
        processor.image_processor.size = {"height": height, "width": width}
        if view.model.kind != "tiny":                       # the checkpoint as it is published: before a token is added to it
            checkpoint_ted = cord_ted(_checkpoint_extractor(view, processor, model), validation_receipts, cord_pages)
        prepare_tokens(processor, model, targets, view)
        if view.model.kind == "tiny":                       # the tiny model has no published checkpoint: its starting point, with the task tokens it needs to be asked
            checkpoint_ted = cord_ted(_checkpoint_extractor(view, processor, model), validation_receipts, cord_pages)
        items = build_train_set(ctx.cord_index, truths["train"], view, cord_image=cord_pages, synth_image=synth_page)
        tokenizer = processor.tokenizer
        lengths = {f"item-{n}": len(tokenizer(item.target + tokenizer.eos_token, add_special_tokens=False).input_ids) for n, item in enumerate(items)}
        check_block_sizes(view, {}, lengths)                                                           # M05, the lengths
        folders = fine_tune(items, processor, model, view, out_dir=work / "candidates", on_epoch=lambda report: logger.info(
            "fit_model: epoch %d, %d steps, mean loss %.4f", report.epoch, report.steps, report.mean_loss))
        del model
        _free()
        candidates = []
        for folder in folders:
            epoch = int(folder.name.removeprefix("epoch-"))
            record = seal_folder(folder)
            extractor = extractors.make_extractor(view, {"artifact_name": folder.name, "artifact_sha256": record.sha256, "model_version": f"{MODEL_VERSION}-epoch{epoch}"}, folder.parent) \
                if view.model.kind != "tiny" else _tiny_extractor(view, folder, record.sha256, epoch)
            ted = cord_ted(_cord_twin(extractor, view), validation_receipts, cord_pages)
            candidates.append(Candidate(epoch, ted, exact_rate(extractor, truths["validation_known"], synth_page), exact_rate(extractor, truths["validation_new"], synth_page)))
            del extractor
            _free()
        choice = choose(candidates, checkpoint_ted, view)                                              # M04
        chosen = work / "candidates" / f"epoch-{choice.epoch}"
        sealed = verify_artifact(chosen, seal_folder_hash(chosen))
        figures = {"choice": choice.to_jsonable(), "checkpoint_cord_val": {"ted": checkpoint_ted, "receipts": len(validation_receipts), "fit_splits": CHECKPOINT_SPLITS},
                   "oracle_validation": oracle_figures}
        digest = _rounded_digest(figures)
        document = _document(ctx, view, config_sha256, artifact_name, sealed.sha256, sealed.tokenizer_sha256, master_, choice, figures, digest)
        outcome = launch_seal_outcome(seal_path, document)                                              # decided before anything is written
        os.replace(chosen, final)
        try:
            register_model(ctx.conn, {"model_version": MODEL_VERSION, "trained_as_of": _stamp(ctx.run), "artifact_name": artifact_name, "artifact_sha256": sealed.sha256,
                                      "tokenizer_sha256": sealed.tokenizer_sha256, "config_sha256": config_sha256, "chosen_epoch": choice.epoch,
                                      "choice_json": json.dumps(figures, sort_keys=True, ensure_ascii=False), "validation_digest": digest})
            if outcome == "written":
                outcome = write_launch_seal(seal_path, document)
            elif outcome == "post_seal":
                outcome = write_launch_seal(seal_path, document)      # logs the keys that differ and leaves the seal as it is
        except BaseException:
            shutil.rmtree(final, ignore_errors=True)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rules = [_result("M04", True, chosen_epoch=choice.epoch, floor=choice.floor), _result("M05", True), _result("M06", True, **oracle_figures)]
    logger.info("fit_model: launched model %s, epoch %d, artifact %s, seal %s", MODEL_VERSION, choice.epoch, sealed.sha256[:12], outcome)
    return LaunchResult("launch", outcome, MODEL_VERSION, config_sha256, sealed.sha256, figures["choice"], rules, {"model_registry": 1}, document)


def seal_folder_hash(folder: Path) -> str:
    """The hash of the manifest ``seal_folder`` wrote into `folder`."""
    from invoiceflow.files import sha256_file
    from invoiceflow.model.artifact import MANIFEST

    return sha256_file(folder / MANIFEST)


def _tiny_extractor(view: SealedConfig, folder: Path, artifact_sha256: str, epoch: int) -> Any:
    """The extractor of a tiny candidate: the real loader (the hashes first) on the CPU."""
    import dataclasses

    from invoiceflow.model.donut import load_donut

    cpu = dataclasses.replace(view, model=dataclasses.replace(view.model, infer_device="cpu"))
    return load_donut(cpu, {"artifact_name": folder.name, "artifact_sha256": artifact_sha256, "model_version": f"{MODEL_VERSION}-epoch{epoch}"}, folder.parent)


def _document(ctx: LaunchContext, view: SealedConfig, config_sha256: str, artifact_name: str, artifact_sha256: str, tokenizer_sha256: str, master_: Any, choice: Choice,
              figures: Mapping[str, Any], digest: str) -> dict[str, Any]:
    fingerprints = fingerprint_document(view)
    return {"run_key": ctx.run.run_key, "as_of": _stamp(ctx.run), "config_sha256": config_sha256, "sealed_config": to_jsonable(view), "source_sha256": dict(ctx.observed),
            "master_sha256": fingerprints["master"], "fonts_sha256": fingerprints["fonts"], "model_version": MODEL_VERSION, "artifact_name": artifact_name,
            "artifact_sha256": artifact_sha256, "tokenizer_sha256": tokenizer_sha256, "chosen_epoch": choice.epoch, "choice": figures["choice"],
            "checkpoint_cord_val": figures["checkpoint_cord_val"], "oracle_validation": figures["oracle_validation"], "validation_digest": digest}


__all__ = ["CordPages", "LaunchContext", "LaunchError", "LaunchResult", "OracleMismatchError", "config_sha256_of", "cord_ted", "exact_rate", "fit_model"]
