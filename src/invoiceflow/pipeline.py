"""The tasks of a run, their state, the ordered close and the refusals (RF06, RF25; RNF03, RNF08; AC29, AC30, AC31; plan, sections 3 and 22; D26, D28, D29, D56). Adapted from project 06's ``pipeline.py``
(its D28 and the corrections of its evaluations), from the design of this project.

``TASKS`` is the one list the command line, the entry API and the Airflow DAG run, in this order, with these functions. Every task of a run works on the state file
``<interim>/runs/<run_key>.json`` (the start, the delivery, the status and the result of each task with its seconds and its memory peaks with their counter, or the whole traceback if it failed)
and, from ``load`` on, on the run's row of ``pipeline_runs``. The bodies of the model's tasks stay where they were written (``launch.fit_model``, ``daily.run_day``, ``reporting.evaluate``,
``export.export_star_schema``, ``site_export.export_site``): a task here reads what the earlier ones left, builds the ``RunMeta`` and the sealed view they take and calls them.

* **One writer.** ``run_task``, ``run_all`` and ``replay`` hold ``lock.sole_writer`` from before the state file is touched until they return (D29): a second writer is ``RunLockedError`` (exit 2)
  and writes nothing. A ``replay`` with no pending delivery returns at once and does not take it.
* **Refusals before writing** (``RunRefusedError``, exit 2, no retry), in this order: any task of a run that ended (``ok`` or ``no_delivery``); ``deliver`` in a run that already holds another
  delivery; ``load`` when another run loaded the delivery, and every task after ``load`` unless this run loaded its own delivery; ``load`` without this run's validation or with a blocking rule
  failing in it; the tasks that issue or evaluate (``fit_model`` to ``evaluate``) when the run's delivery is not the latest loaded one; while the seal has no test part, every task of the run
  of the close under a configuration that is not the sealed one (``registry.EvaluationRefusedError``); and every task from ``load`` on of a run that holds no delivery (``task load`` without
  ``--run-key``). A new run's state file is written only once its first task passed them all. A real fit without CUDA is refused too (``TrainingRefusedError`` is a ``RunRefusedError`` here).
* **The close.** The run stays ``running`` until ``report`` closes it in this order: SQLite ``ok``, ``dim_pipeline_run.parquet`` alone, ``summary.json`` and the site manifest, the HTML, and only
  then the delivery ``loaded`` in the manifest of deliveries. Closing never creates a run's row: it is ``load``'s.
* **A failure** after ``load`` marks the run ``failed`` in the state file and in SQLite and rewrites the run table, the summary and the HTML if they exist; each of those writes is tried on its own
  and logged if it fails, and the task's error is raised again (AC29). A run with no delivery records ``no_delivery``. The message of a run's row is published in the run table, so it never
  carries a path of this machine.
* **Four tasks, one issue** (D56). The day's invoices are read, decoded, matched, decided and posted one at a time, each in one transaction (``daily.run_day``, D52), so the plan's ``extract``,
  ``read_qr``, ``match`` and ``post`` are four checkpoints of it: ``extract`` loads the sealed model, ``read_qr`` checks the readers, ``match`` issues the day and ``post`` checks what the ERP holds.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import tempfile
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from invoiceflow import cord, daily, evaluation, reporting
from invoiceflow.config import PipelineConfig, SealedConfig, load_config
from invoiceflow.dashboard.render import render_design
from invoiceflow.dashboard.semantic_model import MODEL
from invoiceflow.deliveries import DeliverySpec, delivery_plan, next_pending, read_manifest, write_day_truth, write_manifest
from invoiceflow.erp.client import events_of, make_client
from invoiceflow.export import export_run_table, export_star_schema
from invoiceflow.files import write_atomic
from invoiceflow.intake import FileCheck, load_page
from invoiceflow.lock import RunLockedError, RunRefusedError, RunStateError, sole_writer
from invoiceflow.registry import EvaluationRefusedError, RunMeta, SealError, check_test_evaluation, config_fingerprint, read_registry, read_seal, sealed_view
from invoiceflow.site_export import SMOKE_FILE, export_site, export_summary, read_smoke
from invoiceflow.sources import ARCA_KEY, fetch_source as check_and_index_sources
from invoiceflow.synth.invoices import invoices
from invoiceflow.synth.master import master as make_master
from invoiceflow.synth.render import render
from invoiceflow.sysinfo import MemoryPeaks, peak_memory
from invoiceflow.validate import RuleResult, ValidationError, ValidationReport, validate_delivery
from invoiceflow.warehouse import apply_schema, connect, load_delivery

logger = logging.getLogger(__name__)

CLOSED_STATUSES = ("ok", "no_delivery")
CLOSING_STEPS = ("sqlite", "run_table", "summary", "html", "manifest")
RUN_COLUMNS = ("run_key", "started_at", "finished_at", "status", "delivery_id", "as_of", "config_sha256", "artifact_sha256", "invoices", "peak_working_set_mb", "peak_gpu_mb", "memory_counter", "message")
STAMP = "%Y-%m-%d %H:%M:%S"
MESSAGE_LIMIT = 500
EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "pdf": ".pdf"}
SKIPPED_TASKS = ("extract", "read_qr", "match", "post")      # the tasks of the issue of a day: a launch and a close issue nothing

__all__ = ["TASKS", "TASK_IDS", "RunContext", "RunLockedError", "RunRefusedError", "RunStateError", "TaskSpec", "UsageError", "replay", "run_all", "run_task"]


class UsageError(ValueError):
    """A command asked for something the flow does not do (an unknown task, two selections together): nothing was written."""


@dataclass(frozen=True)
class RunContext:
    cfg: PipelineConfig
    run_key: str
    started_at: datetime
    state_path: Path


@dataclass(frozen=True)
class TaskSpec:
    """One task: its id, its function and the limits the Airflow DAG gives it (retries, minutes between them, timeout in minutes; Airflow enforces them, ``run_task`` runs a task once).
    ``short_circuit`` marks ``deliver``: with no delivery left the run ends there."""

    task_id: str
    func: Callable[[RunContext], dict]
    retries: int = 0
    retry_delay_min: int = 0
    timeout_min: int = 30
    short_circuit: bool = False


# --- state ------------------------------------------------------------------------------------------------------------------


def make_run_key(source: str | datetime) -> str:
    if isinstance(source, datetime):
        return source.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    key = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(source)).strip("_")
    if not key:
        raise UsageError("una clave de corrida no puede estar vacía")
    return key


def _state_path(cfg: PipelineConfig, run_key: str) -> Path:
    return Path(cfg.paths.interim_dir) / "runs" / f"{run_key}.json"


def _read_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_state(path: Path, state: dict) -> None:
    write_atomic(path, json.dumps(state, indent=2, ensure_ascii=False, default=str) + "\n")


def _context(cfg: PipelineConfig, run_id: str | None, when: datetime) -> RunContext:
    """The run `run_id` (a new one at `when` if it has no state file yet). Nothing is written here: a new run's state file is created by its first task once that task passed every refusal
    (``_open``), so a refused run leaves nothing behind."""
    run_key = make_run_key(run_id if run_id is not None else when)
    path = _state_path(cfg, run_key)
    state = _read_state(path)
    started = datetime.fromisoformat(state["started_at"]) if state else when.replace(microsecond=0)
    return RunContext(cfg, run_key, started, path)


def _open(ctx: RunContext) -> None:
    if not ctx.state_path.exists():
        _write_state(ctx.state_path, {"run_key": ctx.run_key, "started_at": ctx.started_at.isoformat(timespec="seconds"), "status": "running", "tasks": {}})


def _task_result(ctx: RunContext, task_id: str) -> dict:
    result = _read_state(ctx.state_path).get("tasks", {}).get(task_id)
    if result is None or result.get("status") in ("failed", "rejected"):
        raise RunStateError(f"la corrida {ctx.run_key} no tiene el resultado de la tarea {task_id}: corra esa tarea antes (python -m invoiceflow task {task_id} --run-key {ctx.run_key})")
    return result


def _save_task(ctx: RunContext, task_id: str, result: dict, status: str | None = None) -> None:
    state = _read_state(ctx.state_path)
    state.setdefault("tasks", {})[task_id] = result
    if status:
        state["status"] = status
    _write_state(ctx.state_path, state)


def _mark_rejected(ctx: RunContext, task_id: str, report: ValidationReport) -> None:
    state = _read_state(ctx.state_path)
    state["status"] = "rejected"
    state.setdefault("tasks", {})[task_id] = {"status": "rejected", "rules": list(report.blocking_failed)}
    _write_state(ctx.state_path, state)


def _now_text() -> str:
    return datetime.now(UTC).strftime(STAMP)


def _stamp(value: datetime) -> str:
    return value.strftime(STAMP)


def _peak() -> MemoryPeaks | None:
    """The process's memory peaks, or None: telemetry never discards the result of a task that finished."""
    try:
        return peak_memory()
    except Exception:
        logger.warning("could not measure the peak memory", exc_info=True)
        return None


_FOLDER = "<folder>"
_ABSOLUTE_PATH = re.compile(r"(?:<folder>|[A-Za-z]:[\\/]|\\\\|/(?:home|Users|tmp|var|private|mnt|opt|root|usr|etc|srv|run|media)/)[^\s'\"<>|]*")


def _without_paths(text: str, cfg: PipelineConfig) -> str:
    """`text` with every path of this machine taken out: each path that starts at a folder this machine is known by (the project's, the user's, the temporary one: they may hold spaces) or looks
    absolute becomes ``<path>``, whatever follows it. The message of a run's row is published in the run table."""
    known = {str(cfg.project_root), Path(cfg.project_root).as_posix(), str(Path.home()), Path.home().as_posix(), tempfile.gettempdir(), Path(tempfile.gettempdir()).as_posix()}
    for folder in sorted((k for k in known if len(k) > 3), key=len, reverse=True):
        text = text.replace(folder, _FOLDER)
    return _ABSOLUTE_PATH.sub("<path>", text)


# --- the warehouse, as the tasks need it ----------------------------------------------------------------------------------------


def _read_only(cfg: PipelineConfig) -> sqlite3.Connection:
    """The warehouse for reading; before the first load there is none, and asking must not create it."""
    path = Path(cfg.paths.warehouse)
    return sqlite3.connect(":memory:") if not path.exists() else sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _has_record(ctx: RunContext) -> bool:
    conn = _read_only(ctx.cfg)
    try:
        return _has_table(conn, "pipeline_runs") and conn.execute("SELECT 1 FROM pipeline_runs WHERE run_key = ?", (ctx.run_key,)).fetchone() is not None
    finally:
        conn.close()


def record_run(conn: sqlite3.Connection, fields: dict) -> None:
    """Create or update the run's row of ``pipeline_runs`` with `fields` (only the columns given change)."""
    unknown = set(fields) - set(RUN_COLUMNS)
    if unknown:
        raise ValueError(f"columnas desconocidas de pipeline_runs: {sorted(unknown)}")
    columns = [c for c in fields if c != "run_key"]
    updated = conn.execute(f"UPDATE pipeline_runs SET {', '.join(f'{c} = ?' for c in columns)} WHERE run_key = ?", [*(fields[c] for c in columns), fields["run_key"]]).rowcount if columns else 0
    if not updated:
        names = list(fields)
        conn.execute(f"INSERT INTO pipeline_runs ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})", [fields[c] for c in names])


def _record(ctx: RunContext, **fields) -> None:
    if "message" in fields:
        fields["message"] = _without_paths(str(fields["message"]), ctx.cfg)[:MESSAGE_LIMIT]
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        apply_schema(conn)
        record_run(conn, {"run_key": ctx.run_key, **fields})
    finally:
        conn.close()


def _loaded_by(conn: sqlite3.Connection, delivery_id: str) -> str | None:
    if not _has_table(conn, "deliveries"):
        return None
    row = conn.execute("SELECT run_key FROM deliveries WHERE delivery_id = ? AND status = 'loaded'", (delivery_id,)).fetchone()
    return row[0] if row else None


def _latest_loaded(conn: sqlite3.Connection) -> tuple[str, str, datetime] | None:
    if not _has_table(conn, "deliveries"):
        return None
    row = conn.execute("SELECT delivery_id, run_key, as_of FROM deliveries WHERE status = 'loaded' ORDER BY as_of DESC, rowid DESC LIMIT 1").fetchone()
    return None if row is None else (row[0], row[1], datetime.fromisoformat(row[2]))


# --- folders and records of the deliveries ---------------------------------------------------------------------------------------


def _deliveries_dir(cfg: PipelineConfig) -> Path:
    return Path(cfg.paths.raw_dir) / "deliveries"


def _manifest_path(cfg: PipelineConfig) -> Path:
    return _deliveries_dir(cfg) / "manifest.json"


def _inbox(cfg: PipelineConfig, delivery_id: str) -> Path:
    return _deliveries_dir(cfg) / delivery_id / "inbox"


def _truth_path(cfg: PipelineConfig, delivery_id: str) -> Path:
    return Path(cfg.paths.interim_dir) / "truth" / f"{delivery_id}.jsonl"


def _validation_dir(cfg: PipelineConfig) -> Path:
    return Path(cfg.paths.reports_dir) / "validation"


def _report_path(cfg: PipelineConfig, run_key: str) -> Path:
    return _validation_dir(cfg) / "history" / f"{run_key}.json"


def _mark_delivery(cfg: PipelineConfig, delivery_id: str, status: str, run_key: str) -> None:
    path = _manifest_path(cfg)
    manifest = read_manifest(path)
    entry = manifest["deliveries"].get(delivery_id)
    if entry is None:
        raise RunStateError(f"la entrega {delivery_id} no está en el manifiesto: no se puede marcar {status}")
    manifest["deliveries"][delivery_id] = {**entry, "status": status, "run_key": run_key}
    write_manifest(path, manifest)


def _report_to_text(report: ValidationReport) -> str:
    """The validation report with what ``load`` needs of each file (its hash and pixels), to read it back in another process."""
    document = report.to_jsonable()
    document["files"] = [{**f, "pixels": c.pixels, "sha256": c.sha256} for f, c in zip(document["files"], report.files, strict=True)]
    return json.dumps(document, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def _read_report(path: Path, inbox: Path) -> ValidationReport:
    document = json.loads(path.read_text(encoding="utf-8"))
    files = tuple(FileCheck(f["name"], inbox / f["name"], f["status"], f["rule_id"], f["reason"], f["format"], f["bytes"], f["pixels"], f["sha256"]) for f in document["files"])
    results = tuple(RuleResult(r["rule_id"], r["name_es"], r["severity"], r["scope"], r["aggregation"], r["passed"], r["files_affected"], tuple(r["examples"]), r["detail"]) for r in document["results"])
    return ValidationReport(document["delivery_id"], document["run_key"], results, files)


def _write_report(cfg: PipelineConfig, report: ValidationReport) -> None:
    write_atomic(_report_path(cfg, report.run_key), _report_to_text(report))


# --- the meta of a run -------------------------------------------------------------------------------------------------------------


def _held_delivery(ctx: RunContext) -> dict:
    return _read_state(ctx.state_path).get("tasks", {}).get("deliver", {})


def _observed(ctx: RunContext) -> dict[str, str]:
    return _task_result(ctx, "fetch_source")["source_sha256"]


def _view(ctx: RunContext) -> SealedConfig:
    return sealed_view(ctx.cfg, _observed(ctx))


def _spec_of(view: SealedConfig, delivery_id: str) -> DeliverySpec:
    return next(spec for spec in delivery_plan(view) if spec.delivery_id == delivery_id)


def _spec(ctx: RunContext) -> DeliverySpec:
    return _spec_of(_view(ctx), _held_delivery(ctx)["delivery_id"])


def _meta(ctx: RunContext) -> RunMeta:
    return RunMeta(ctx.run_key, _spec(ctx).as_of)


def _add_rules(ctx: RunContext, results: list[RuleResult]) -> None:
    """The results of the model rules (M02 to M06) of this run next to its validation: ``validation_results`` is insert-only, a rule that was already reported is left."""
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for r in results:
            if conn.execute("SELECT 1 FROM validation_results WHERE run_key = ? AND rule_id = ?", (ctx.run_key, r.rule_id)).fetchone() is None:
                conn.execute("INSERT INTO validation_results (run_key, rule_id, name_es, severity, scope, aggregation, passed, files_affected, examples_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (ctx.run_key, r.rule_id, r.name_es, r.severity, r.scope, r.aggregation, int(r.passed), r.files_affected, json.dumps(list(r.examples))))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# --- the tasks ---------------------------------------------------------------------------------------------------------------------


def fetch_source(ctx: RunContext) -> dict:
    """V01 to V06 over the sources, their footers, the canonical index of train and validation (``sources.fetch_source``); the test split is never opened."""
    return check_and_index_sources(ctx.cfg)


def _entry_of(files: dict[str, bytes], truth: Path | None, delivery_id: str, kind: str) -> dict:
    listed = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(files.items())}
    truth_entry = None if truth is None else {"bytes": truth.stat().st_size, "sha256": hashlib.sha256(truth.read_bytes()).hexdigest()}
    fingerprint = hashlib.sha256(json.dumps({"id": delivery_id, "kind": kind, "files": listed, "truth": truth_entry}, sort_keys=True).encode("utf-8")).hexdigest()
    return {"status": "published", "kind": kind, "files": listed, "truth": truth_entry, "sha256": fingerprint}


def _publish(cfg: PipelineConfig, view: SealedConfig, spec: DeliverySpec, client: Any) -> dict:
    """Make the next delivery: the invoices of a day (their files, their truth), the movements of the master the ERP learns that day, and its entry in the manifest."""
    master = make_master(view)
    plan = delivery_plan(view)
    previous = plan[plan.index(spec) - 1] if plan.index(spec) > 0 else None
    inbox = _inbox(cfg, spec.delivery_id)
    shutil.rmtree(inbox.parent, ignore_errors=True)       # a delivery is made whole each time (the generator is deterministic): nothing of an earlier attempt stays
    inbox.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    truth_path = None
    if spec.kind == "daily":
        truths = sorted([*invoices(view, master, "test_known", spec.day, seal_path=Path(cfg.paths.seal)), *invoices(view, master, "test_new", spec.day, seal_path=Path(cfg.paths.seal))],
                        key=lambda t: t.invoice_key)
        for truth in truths:
            data = render(truth, view)
            name = f"{truth.invoice_key}{EXTENSIONS[truth.file_format]}"
            write_atomic(inbox / name, data)
            files[name] = data
        truth_path = _truth_path(cfg, spec.delivery_id)
        write_day_truth(truth_path, truths)
    until = spec.day if spec.kind in ("launch", "daily") else view.calendar.test_last_day
    after = len(events_of(master, until=previous.day)) if previous is not None and previous.kind in ("launch", "daily") else 0
    events = events_of(master, until=until)[after:]
    for event in events:
        client.apply_master_event(event)
    entry = _entry_of(files, truth_path, spec.delivery_id, spec.kind)
    manifest = read_manifest(_manifest_path(cfg))
    manifest["deliveries"][spec.delivery_id] = entry
    write_manifest(_manifest_path(cfg), manifest)
    return {"files": len(files), "events": len(events)}


def deliver(ctx: RunContext) -> dict:
    """Publish the next delivery of the plan (D22): the launch, a day, the close; without one left the run ends here as ``no_delivery``."""
    cfg = ctx.cfg
    view = _view(ctx)
    spec = next_pending(read_manifest(_manifest_path(cfg)), delivery_plan(view))
    if spec is None:
        _record(ctx, started_at=_stamp(ctx.started_at.replace(tzinfo=None)), finished_at=_now_text(), status="no_delivery", message="no hay entregas pendientes")
        return {"delivered": False, "run_status": "no_delivery", "message": "no hay entregas pendientes: las del plan ya están cargadas"}
    published = _publish(cfg, view, spec, make_client(cfg.erp, cfg.paths))
    return {"delivered": True, "delivery_id": spec.delivery_id, "kind": spec.kind, "day": spec.day.isoformat(), "as_of": _stamp(spec.as_of), **published}


def validate(ctx: RunContext) -> dict:
    """V10 to V16 and I01 over the delivery's files; a blocking rule rejects the delivery (exit 3), the files that fail go to quarantine."""
    cfg, view = ctx.cfg, _view(ctx)
    spec = _spec(ctx)
    conn = connect(cfg.paths.warehouse) if Path(cfg.paths.warehouse).exists() else None
    try:
        report = validate_delivery(spec, _inbox(cfg, spec.delivery_id), conn, view, run_key=ctx.run_key, manifest=read_manifest(_manifest_path(cfg)), truth_path=_truth_path(cfg, spec.delivery_id)
                                   if spec.kind == "daily" else None)
    except ValidationError as error:
        _write_report(cfg, error.report)
        _mark_rejected(ctx, "validate", error.report)
        raise
    finally:
        if conn is not None:
            conn.close()
    _write_report(cfg, report)
    return {"accepted": sum(1 for c in report.files if c.status == "accepted"), "quarantined": sum(1 for c in report.files if c.status == "quarantined"), "rules": [r.rule_id for r in report.results]}


def load(ctx: RunContext) -> dict:
    """Load the delivery, the validation of this run and the run itself in one transaction (``warehouse.load_delivery``)."""
    cfg, view = ctx.cfg, _view(ctx)
    spec = _spec(ctx)
    report = _read_report(_report_path(cfg, ctx.run_key), _inbox(cfg, spec.delivery_id))
    entry = read_manifest(_manifest_path(cfg))["deliveries"][spec.delivery_id]
    conn = connect(cfg.paths.warehouse)
    try:
        result = load_delivery(conn, spec, report, ctx.run_key, started_at=ctx.started_at.replace(tzinfo=None), config_sha256=config_fingerprint(view), sha256=entry["sha256"])
    finally:
        conn.close()
    return {"delivery_id": spec.delivery_id, "files": len(report.files), "tables": {k: v for k, v in dataclasses.asdict(result).items() if isinstance(v, int)}}


def fit_model(ctx: RunContext) -> dict:
    """The launch fits, chooses and seals; every other run checks that the model is still the sealed one (M02, M03)."""
    from invoiceflow.launch import LaunchContext, LaunchError, fit_model as fit_frozen_model
    from invoiceflow.model.train import TrainingRefusedError

    cfg, view = ctx.cfg, _view(ctx)
    conn = connect(cfg.paths.warehouse)
    try:
        context = LaunchContext(cfg, conn, _meta(ctx), _observed(ctx), cord.read_index(cfg))
        try:
            result = fit_frozen_model(context)
        except (TrainingRefusedError, LaunchError) as error:
            raise RunRefusedError(f"{error} No se escribió un modelo ni un sello.") from error
    finally:
        conn.close()
    _add_rules(ctx, result.rules)
    _record(ctx, artifact_sha256=result.artifact_sha256)
    return {"mode": result.mode, "seal": result.seal, "model_version": result.model_version, "artifact_sha256": result.artifact_sha256, "config_sha256": result.config_sha256,
            "chosen_epoch": result.choice.get("chosen_epoch"), "rules": {r.rule_id: r.passed for r in result.rules}}


def _extractor(ctx: RunContext, view: SealedConfig):
    from invoiceflow.model import extractors

    conn = _read_only(ctx.cfg)
    try:
        row = read_registry(conn)
    finally:
        conn.close()
    if row is None:
        raise RunStateError("no hay un modelo registrado: el lanzamiento lo ajusta (python -m invoiceflow run)")
    if view.model.kind == "tiny":       # the quick mode's model: the same loader as the real one (hashes first), on the CPU, which no registry of extractors names (``kind = "tiny"`` exists only under data/quick/)
        from invoiceflow.model.donut import load_donut

        return load_donut(dataclasses.replace(view, model=dataclasses.replace(view.model, infer_device="cpu")), row, Path(ctx.cfg.paths.models_dir)), row
    return extractors.make_extractor(view, row, Path(ctx.cfg.paths.models_dir)), row


def _issues(ctx: RunContext) -> DeliverySpec | None:
    spec = _spec(ctx)
    return spec if spec.kind == "daily" else None


def extract(ctx: RunContext) -> dict:
    """Load the sealed model (its hashes are checked before a weight is read) and say which it is: the day's pages are read by ``match`` (D56)."""
    spec = _issues(ctx)
    if spec is None:
        return {"skipped": _spec(ctx).kind}
    _extractor_, row = _extractor(ctx, _view(ctx))
    return {"model_version": row["model_version"], "artifact_sha256": row["artifact_sha256"], "device": str(getattr(_extractor_, "device", "cpu"))}


def _reader_available(name: str) -> bool:
    """Whether the reader `name` can run here: pyzbar needs its native library (it raises when importing without it), Aruco is part of OpenCV."""
    try:
        if name == "pyzbar":
            from pyzbar import pyzbar  # noqa: F401
        else:
            import cv2

            return hasattr(cv2, "QRCodeDetectorAruco")
    except (ImportError, OSError):
        return False
    return True


def read_qr(ctx: RunContext) -> dict:
    """Check that the configured readers can be used here (pyzbar needs its native library); the day's QR are read by ``match`` (D56)."""
    if _issues(ctx) is None:
        return {"skipped": _spec(ctx).kind}
    view = _view(ctx)
    available = [name for name in view.qr.readers if _reader_available(name)]
    if not available:
        raise RunStateError(f"ningún lector de QR está disponible aquí ({', '.join(view.qr.readers)}): en Windows hace falta la biblioteca de pyzbar, en Linux libzbar0 (docs/runbook.md)")
    return {"readers": available}


def match(ctx: RunContext) -> dict:
    """Issue the day: each invoice is read, decoded, matched, decided and, if ``auto_post``, posted, in one transaction of its own (``daily.run_day``, D52)."""
    spec = _issues(ctx)
    if spec is None:
        return {"skipped": _spec(ctx).kind}
    cfg, view = ctx.cfg, _view(ctx)
    extractor, _row = _extractor(ctx, view)
    conn = connect(cfg.paths.warehouse)
    try:
        done = daily.run_day(conn, make_client(cfg.erp, cfg.paths), extractor, _meta(ctx), view, spec, _inbox(cfg, spec.delivery_id))
    finally:
        conn.close()
    return {"issued": done.invoices, "quarantined": done.quarantined, "auto_post": done.auto_post, "review": done.review, "posted": done.posted, "post_failed": done.post_failed}


def post(ctx: RunContext) -> dict:
    """What the ERP holds for the day: every ``auto_post`` has its post, read back equal to what was decided."""
    spec = _issues(ctx)
    if spec is None:
        return {"skipped": _spec(ctx).kind}
    conn = _read_only(ctx.cfg)
    try:
        auto = conn.execute("SELECT COUNT(*) FROM decisions d JOIN invoices i ON i.invoice_key = d.invoice_key WHERE i.delivery_id = ? AND d.decision = 'auto_post'", (spec.delivery_id,)).fetchone()[0]
        statuses = dict(conn.execute("SELECT p.status, COUNT(*) FROM erp_posts p JOIN invoices i ON i.invoice_key = p.invoice_key WHERE i.delivery_id = ? GROUP BY p.status", (spec.delivery_id,)).fetchall())
        different = conn.execute("SELECT COUNT(*) FROM erp_posts p JOIN invoices i ON i.invoice_key = p.invoice_key WHERE i.delivery_id = ? AND p.status != 'post_failed' AND p.readback_equal != 1",
                                 (spec.delivery_id,)).fetchone()[0]
    finally:
        conn.close()
    if sum(statuses.values()) != auto or different:
        raise RunStateError(f"la entrega {spec.delivery_id} tiene {auto} facturas cargadas solas, {sum(statuses.values())} cargas registradas y {different} que no se leen de vuelta iguales")
    return {"auto_post": auto, "posted": statuses.get("posted", 0), "already_posted": statuses.get("already_posted", 0), "post_failed": statuses.get("post_failed", 0)}


class _ReadsNothing:
    """The unadjusted starting point of the tiny model of the quick mode: it has no published checkpoint, and a model that has not learned the task reads nothing."""

    def extract(self, image: Any) -> dict:
        return {}


def _closing_models(ctx: RunContext, view: SealedConfig):
    """The chosen model and the unadjusted checkpoint, each asked with CORD's task, for the one opening of CORD's test split."""
    from invoiceflow.launch import _cord_twin

    chosen, _row = _extractor(ctx, view)
    if view.model.kind == "tiny":
        return _cord_twin(chosen, view), _ReadsNothing()
    from invoiceflow.launch import _checkpoint_extractor
    from invoiceflow.model.train import load_base

    processor, model = load_base(view)
    height, width = view.model.input_size
    processor.image_processor.size = {"height": height, "width": width}
    return _cord_twin(chosen, view), _checkpoint_extractor(view, processor, model)


CHECKPOINT_BATCH = 8       # pages per generation in B2's pass of the checkpoint (D61): a constant of the code, outside the seal; the trees are the ones of one page at a time


def checkpoint_trees(cfg: PipelineConfig, checkpoint: Any, truths: list, view: SealedConfig) -> dict[str, Any]:
    """B2 needs the tree the unadjusted checkpoint reads on each test page (D53): one pass over the inbox files, asked with the CORD task, by invoice key in the order of `truths`. A checkpoint
    that reads batches (``extract_many``, the real one) is asked ``CHECKPOINT_BATCH`` pages at a time, which takes the pass of the close from about 1,030 s to about 250 s on this machine with the
    same trees (D61); one that reads a page at a time is asked page by page. A page without its file has no tree (``{}``)."""
    paths = {}
    for truth in truths:
        folder = _inbox(cfg, f"D{truth.received_on.isoformat()}")
        paths[truth.invoice_key] = next((p for p in (folder / f"{truth.invoice_key}{ext}" for ext in EXTENSIONS.values()) if p.is_file()), None)
    found = [key for key, path in paths.items() if path is not None]
    many = getattr(checkpoint, "extract_many", None)
    read: dict[str, Any] = {}
    for start in range(0, len(found), CHECKPOINT_BATCH):
        keys = found[start:start + CHECKPOINT_BATCH]
        pages = [load_page(paths[key], view) for key in keys]
        read.update(zip(keys, many(pages, CHECKPOINT_BATCH) if callable(many) else [checkpoint.extract(page) for page in pages], strict=True))
    return {key: read.get(key, {}) for key in paths}


def _repeat_last_day(ctx: RunContext, view: SealedConfig, last: DeliverySpec) -> int | None:
    """C09: the day is run again over a copy of the warehouse and a copy of the ERP in which only the day's issue was taken back; what the ERP holds has to be the same afterwards. The number of
    invoices that the repetition created (``None`` when the ERP is not the local stand-in, which cannot be copied)."""
    cfg = ctx.cfg
    if cfg.erp.mode != "standin":
        return None
    extractor, _row = _extractor(ctx, view)
    with tempfile.TemporaryDirectory(prefix="invoiceflow-repeat-") as folder:
        work = Path(folder)
        shutil.copy(cfg.paths.warehouse, work / "warehouse.sqlite")
        shutil.copy(cfg.paths.erp_db, work / "standin.sqlite")
        conn = connect(work / "warehouse.sqlite")
        try:
            keys = [r[0] for r in conn.execute("SELECT invoice_key FROM invoices WHERE delivery_id = ?", (last.delivery_id,))]
            for trigger in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name GLOB '*_no_delete'")]:
                conn.execute(f"DROP TRIGGER {trigger}")
            marks = ",".join("?" * len(keys))
            for table in ("erp_posts", "decision_reasons", "decisions", "qr_reads", "extracted_fields"):
                conn.execute(f"DELETE FROM {table} WHERE invoice_key IN ({marks})", keys)
            conn.execute("DELETE FROM daily_ops WHERE date_key = ?", (int(last.day.strftime("%Y%m%d")),))
            paths = dataclasses.replace(cfg.paths, erp_db=work / "standin.sqlite")
            client = make_client(cfg.erp, paths)
            before = len(client._list("Purchase Invoice", [], ["name"]))
            daily.run_day(conn, client, extractor, RunMeta(_loaded_by(conn, last.delivery_id) or ctx.run_key, last.as_of), view, last, _inbox(cfg, last.delivery_id))
            return len(client._list("Purchase Invoice", [], ["name"])) - before
        finally:
            conn.close()


def evaluate(ctx: RunContext) -> dict:
    """Only at the close: the test is evaluated once (``reporting.evaluate``), with the repetition of the last day (C09) and the checkpoint's trees (B2) it is given."""
    spec = _spec(ctx)
    if spec.kind != "close":
        return {"skipped": spec.kind}
    cfg, view = ctx.cfg, _view(ctx)
    plan = delivery_plan(view)
    days = [s for s in plan if s.kind == "daily"]
    truths = evaluation.load_test_truths(cfg.paths.interim_dir, [s.delivery_id for s in days])
    chosen, checkpoint = _closing_models(ctx, view)
    repeat_new = _repeat_last_day(ctx, view, days[-1])
    trees = checkpoint_trees(cfg, checkpoint, truths, view)
    conn = connect(cfg.paths.warehouse)
    try:
        try:
            done = reporting.evaluate(conn, cfg, view, _meta(ctx), make_master(view), truths, seal_path=Path(cfg.paths.seal), chosen=chosen, checkpoint=checkpoint, closing_run=True,
                                      repeat_new=repeat_new, checkpoint_trees=trees)
        except EvaluationRefusedError as error:
            raise RunRefusedError(str(error)) from error
    finally:
        conn.close()
    return {"state": done["state"], "seal": done["seal"], "rows": done["rows"], "repeat_new": repeat_new, "b2": bool(trees)}


def _cord_index(cfg: PipelineConfig):
    try:
        return cord.read_index(cfg)
    except cord.CordIndexError:
        return None


def export(ctx: RunContext) -> dict:
    """The star model for Power BI and the five files of the site, as of the instant of the run's delivery."""
    cfg, view, run = ctx.cfg, _view(ctx), _meta(ctx)
    smoke = read_smoke(Path(cfg.paths.reports_dir) / SMOKE_FILE)        # what the smoke test against ERPNext left (U22), or None
    conn = connect(cfg.paths.warehouse)
    try:
        parquet = export_star_schema(conn, cfg.paths.outputs_dir, MODEL, run, view, seal_path=Path(cfg.paths.seal))
        site = export_site(conn, cfg.paths.site_dir, run, view, seal_path=Path(cfg.paths.seal), max_total_bytes=cfg.site.max_total_bytes, cord_index=_cord_index(cfg),
                           expected_rows=dataclasses.asdict(cfg.source.expected_rows), smoke_erpnext=smoke)
    finally:
        conn.close()
    return {"parquet": len(parquet), "site": len(site)}


def render_dashboard(ctx: RunContext) -> dict:
    """The HTML design of the report, from the specification and the Parquet files just exported."""
    path = render_design(MODEL, Path(ctx.cfg.paths.outputs_dir), Path(ctx.cfg.paths.dashboard_html), _meta(ctx))
    return {"html": path.name, "bytes": path.stat().st_size}


# --- the close ---------------------------------------------------------------------------------------------------------------


def _run_peaks(ctx: RunContext) -> tuple[float | None, float | None, str | None]:
    tasks = _read_state(ctx.state_path).get("tasks", {}).values()
    now = _peak()
    working = [p for p in [*(t.get("peak_working_set_mb") for t in tasks), now.working_set_mb if now else None] if p is not None]
    gpu = [p for p in [*(t.get("peak_gpu_mb") for t in tasks), now.gpu_mb if now else None] if p is not None]
    return (max(working) if working else None, max(gpu) if gpu else None, now.counter if now else None)


def _close_sqlite(ctx: RunContext) -> None:
    """Close the run's row as ``ok``; the row is ``load``'s, so a run without one is an error and never gets a row here."""
    working, gpu, counter = _run_peaks(ctx)
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        apply_schema(conn)
        if conn.execute("SELECT 1 FROM pipeline_runs WHERE run_key = ?", (ctx.run_key,)).fetchone() is None:
            raise RunStateError(f"la corrida {ctx.run_key} no tiene su fila en pipeline_runs: no cargó su entrega, así que no se cierra (python -m invoiceflow run --run-key {ctx.run_key} --from validate)")
        record_run(conn, {"run_key": ctx.run_key, "status": "ok", "finished_at": _now_text(), "message": "ok", "peak_working_set_mb": None if working is None else round(working, 1),
                          "peak_gpu_mb": None if gpu is None else round(gpu, 1), "memory_counter": counter})
    finally:
        conn.close()


def _close_run_table(ctx: RunContext) -> None:
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        export_run_table(conn, Path(ctx.cfg.paths.outputs_dir), MODEL, _meta(ctx), _view(ctx))
    finally:
        conn.close()


def _close_summary(ctx: RunContext) -> None:
    cfg = ctx.cfg
    conn = connect(cfg.paths.warehouse)
    try:
        export_summary(conn, Path(cfg.paths.site_dir), _meta(ctx), _view(ctx), seal_path=Path(cfg.paths.seal), max_total_bytes=cfg.site.max_total_bytes)
    finally:
        conn.close()


def _close_html(ctx: RunContext) -> None:
    render_dashboard(ctx)


def _close_manifest(ctx: RunContext) -> None:
    _mark_delivery(ctx.cfg, _held_delivery(ctx)["delivery_id"], "loaded", ctx.run_key)


_CLOSERS = {"sqlite": _close_sqlite, "run_table": _close_run_table, "summary": _close_summary, "html": _close_html, "manifest": _close_manifest}


def report(ctx: RunContext) -> dict:
    """Write the evaluation summary and this run's validation report as the latest, and close the run in ``CLOSING_STEPS`` order (plan, section 3, step 13)."""
    cfg, view = ctx.cfg, _view(ctx)
    conn = connect(cfg.paths.warehouse)
    try:
        summary = reporting.build_summary(conn, view, seal_path=Path(cfg.paths.seal))
    finally:
        conn.close()
    reports = Path(cfg.paths.reports_dir)
    write_atomic(reports / "evaluation_summary.json", json.dumps(summary, indent=1, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    reporting.write_summary(summary, reports)
    validation = _read_report(_report_path(cfg, ctx.run_key), _inbox(cfg, _held_delivery(ctx)["delivery_id"]))
    write_atomic(_validation_dir(cfg) / "latest.json", _report_to_text(validation))
    write_atomic(_validation_dir(cfg) / "latest.md", validation.to_markdown())
    for step in CLOSING_STEPS:
        _CLOSERS[step](ctx)
    return {"summary": "evaluation_summary.json", "evaluation_source": summary["source"], "run_status": "ok"}


def _refresh_exports(ctx: RunContext, *, summary: bool, html: bool, when: str) -> None:
    """Rewrite what was exported before so it says what SQLite says now: the run table, this run's summary and the HTML; nothing is created. Each write is tried on its own and logged if it fails
    (the caller's outcome stands)."""
    cfg = ctx.cfg
    try:
        _meta(ctx), _view(ctx)
    except (RunStateError, KeyError, TypeError, ValueError, StopIteration) as error:
        logger.warning("could not read the metadata of run %s %s, so no exported file was rewritten: %s", ctx.run_key, when, error)
        return
    steps = [("run table", _close_run_table)]
    if summary and (Path(cfg.paths.site_dir) / "summary.json").exists():
        steps.append(("summary", _close_summary))
    if html and Path(cfg.paths.dashboard_html).exists():
        steps.append(("html", _close_html))
    for name, step in steps:
        try:
            step(ctx)
        except Exception as error:
            logger.warning("could not rewrite the %s %s (run %s): %s", name, when, ctx.run_key, error, exc_info=error)


# --- running tasks -----------------------------------------------------------------------------------------------------------------


TASKS: tuple[TaskSpec, ...] = (
    TaskSpec("fetch_source", fetch_source, 0, 0, 20),
    TaskSpec("deliver", deliver, 0, 0, 30, short_circuit=True),
    TaskSpec("validate", validate, 0, 0, 10),
    TaskSpec("load", load, 1, 5, 20),
    TaskSpec("fit_model", fit_model, 1, 5, 240),
    TaskSpec("extract", extract, 1, 5, 20),
    TaskSpec("read_qr", read_qr, 1, 5, 10),
    TaskSpec("match", match, 1, 5, 30),
    TaskSpec("post", post, 1, 5, 10),
    TaskSpec("evaluate", evaluate, 1, 5, 30),
    TaskSpec("export", export, 1, 5, 10),
    TaskSpec("render_dashboard", render_dashboard, 1, 5, 10),
    TaskSpec("report", report, 1, 5, 5),
)
TASK_IDS = tuple(t.task_id for t in TASKS)
LATEST_ONLY = TASK_IDS[TASK_IDS.index("fit_model"):TASK_IDS.index("export")]      # the tasks that read what the latest delivery left


def _task_spec(task_id: str) -> TaskSpec:
    for spec in TASKS:
        if spec.task_id == task_id:
            return spec
    raise UsageError(f"tarea desconocida {task_id!r}; las tareas son {', '.join(TASK_IDS)}")


def _refuse_a_closed_run(spec: TaskSpec, ctx: RunContext) -> None:
    status = _read_state(ctx.state_path).get("status")
    if status in CLOSED_STATUSES:
        raise RunRefusedError(f"la corrida {ctx.run_key} está cerrada (estado {status}): no se vuelve a abrir para correr {spec.task_id}; otra entrega es otra corrida (python -m invoiceflow run)")


def _refuse_a_second_delivery(spec: TaskSpec, ctx: RunContext) -> None:
    held = _held_delivery(ctx)
    if spec.task_id != "deliver" or not held.get("delivered"):
        return
    conn = _read_only(ctx.cfg)
    try:
        view = _view(ctx)
        pending = next_pending(read_manifest(_manifest_path(ctx.cfg)), delivery_plan(view))
    finally:
        conn.close()
    if pending is None or pending.delivery_id != held["delivery_id"]:
        raise RunRefusedError(f"la corrida {ctx.run_key} ya tiene la entrega {held['delivery_id']} y la siguiente pendiente es {'ninguna' if pending is None else pending.delivery_id}: "
                              "una corrida no cambia de entrega (python -m invoiceflow run)")


def _refuse_a_delivery_this_run_did_not_load(spec: TaskSpec, ctx: RunContext) -> None:
    if TASK_IDS.index(spec.task_id) < TASK_IDS.index("load"):
        return
    held = _held_delivery(ctx)
    if not held.get("delivered"):
        return
    conn = _read_only(ctx.cfg)
    try:
        owner = _loaded_by(conn, held["delivery_id"])
    finally:
        conn.close()
    if owner == ctx.run_key or (owner is None and spec.task_id == "load"):
        return
    if owner is None:
        raise RunRefusedError(f"la corrida {ctx.run_key} tiene la entrega {held['delivery_id']} pero no la cargó: {spec.task_id} trabaja sobre la entrega que cargó su corrida "
                              f"(python -m invoiceflow run --run-key {ctx.run_key} --from validate)")
    raise RunRefusedError(f"la entrega {held['delivery_id']} la cargó la corrida {owner}: {spec.task_id} no corre en la corrida {ctx.run_key} (python -m invoiceflow run --run-key {owner} --from <tarea>)")


def _refuse_a_load_its_validation_did_not_pass(spec: TaskSpec, ctx: RunContext) -> None:
    if spec.task_id != "load" or not _held_delivery(ctx).get("delivered"):
        return
    path = _report_path(ctx.cfg, ctx.run_key)
    if not path.is_file():
        raise RunRefusedError(f"la corrida {ctx.run_key} no validó su entrega: load carga solo una entrega que la validación de su corrida aprobó (python -m invoiceflow run --run-key {ctx.run_key} --from validate)")
    failed = [r["rule_id"] for r in json.loads(path.read_text(encoding="utf-8"))["results"] if not r["passed"] and r["severity"] == "bloqueante"]
    if failed:
        raise RunRefusedError(f"la validación de la corrida {ctx.run_key} rechazó su entrega ({', '.join(failed)}): load no carga una entrega rechazada (docs/runbook.md)")


def _refuse_an_older_delivery(spec: TaskSpec, ctx: RunContext) -> None:
    held = _held_delivery(ctx)
    if spec.task_id not in LATEST_ONLY or not held.get("delivered"):
        return
    conn = _read_only(ctx.cfg)
    try:
        latest = _latest_loaded(conn)
    finally:
        conn.close()
    if latest is not None and latest[0] != held["delivery_id"]:
        raise RunRefusedError(f"la corrida {ctx.run_key} es de la entrega {held['delivery_id']} (cálculo al {held['as_of']}) y la última cargada es la {latest[0]} (cálculo al {latest[2].isoformat()}): "
                              f"{spec.task_id} calcula la última entrega y no corre en esta corrida")


def _refuse_a_test_evaluation_under_another_configuration(spec: TaskSpec, ctx: RunContext) -> None:
    """While the seal has its launch part and no test part, every task of the run of the close is refused when its configuration is not the sealed one, before anything is written: the test is
    read once, with the sealed configuration (``registry.EvaluationRefusedError`` turned into a refused run). After the test part exists, another configuration is post-seal, as before."""
    cfg = ctx.cfg
    try:
        seal = read_seal(Path(cfg.paths.seal))
    except SealError:
        return
    if seal is None or seal.get("test") is not None:
        return
    held = _held_delivery(ctx)
    state = _read_state(ctx.state_path)
    observed = (state.get("tasks", {}).get("fetch_source") or {}).get("source_sha256") or seal["launch"]["source_sha256"]
    try:
        view = sealed_view(cfg, observed)
    except KeyError:
        return
    if held:
        if not held.get("delivered") or held["kind"] != "close":
            return
        delivery_id = held["delivery_id"]
    else:
        pending = next_pending(read_manifest(_manifest_path(cfg)), delivery_plan(view))
        if pending is None or pending.kind != "close":
            return
        delivery_id = pending.delivery_id
    try:
        check_test_evaluation(Path(cfg.paths.seal), view)
    except EvaluationRefusedError as error:
        raise RunRefusedError(f"la corrida {ctx.run_key} evaluaría la prueba (entrega {delivery_id}) con una configuración que no es la sellada: {error} {spec.task_id} no corre.") from error


def _refuse_a_task_of_a_run_without_a_delivery(spec: TaskSpec, ctx: RunContext) -> None:
    """A task from ``load`` on of a run that holds no delivery (a new run, as ``task <tarea>`` without ``--run-key`` is, or one whose ``deliver`` did not finish) has no delivery of its own to work on:
    it is refused before ``_open`` creates the state file, so nothing is written (B2 of project 05's evaluation). It is the last refusal, so every refusal before it keeps its message."""
    if TASK_IDS.index(spec.task_id) < TASK_IDS.index("load") or _held_delivery(ctx).get("delivered"):
        return
    raise RunRefusedError(f"la corrida {ctx.run_key} no tiene una entrega: {spec.task_id} trabaja sobre la entrega que cargó su corrida; una corrida nueva empieza por fetch_source "
                          "(python -m invoiceflow run) y una que ya empezó se continúa con su clave (python -m invoiceflow run --run-key <clave> --from <tarea>). No se escribió nada.")


def _reopen(ctx: RunContext) -> None:
    state = _read_state(ctx.state_path)
    if state.get("status") != "failed":
        return
    state["status"] = "running"
    _write_state(ctx.state_path, state)
    if _has_record(ctx):
        _record(ctx, status="running", finished_at=None, message="")


def _record_failure(ctx: RunContext, spec: TaskSpec, error: BaseException) -> None:
    """The state file, the run's row and the exported files say the task failed; each write on its own, logged if it fails, so the task's error is what the caller sees (AC29)."""
    message = f"{spec.task_id}: {type(error).__name__}: {error}"
    try:
        state = _read_state(ctx.state_path)
        state["status"] = "failed"
        state.setdefault("tasks", {})[spec.task_id] = {"status": "failed", "error": message[:MESSAGE_LIMIT], "traceback": "".join(traceback.format_exception(error))}
        _write_state(ctx.state_path, state)
    except Exception as failure:
        logger.warning("could not record the failure of %s in the state file of run %s", spec.task_id, ctx.run_key, exc_info=failure)
    try:
        if _has_record(ctx):
            _record(ctx, status="failed", finished_at=_now_text(), message=message)
    except Exception as failure:
        logger.warning("could not record the failure of %s in the warehouse for run %s", spec.task_id, ctx.run_key, exc_info=failure)
    try:
        if _has_record(ctx):
            _refresh_exports(ctx, summary=True, html=True, when=f"after {spec.task_id} failed")
    except Exception as failure:
        logger.warning("could not rewrite the exported files of run %s after %s failed", ctx.run_key, spec.task_id, exc_info=failure)


def _run_spec(spec: TaskSpec, ctx: RunContext) -> dict:
    """The refusals (nothing written), the task, and its result saved with its seconds and its memory peaks; a failure is recorded and raised."""
    _refuse_a_closed_run(spec, ctx)
    _refuse_a_second_delivery(spec, ctx)
    _refuse_a_delivery_this_run_did_not_load(spec, ctx)
    _refuse_a_load_its_validation_did_not_pass(spec, ctx)
    _refuse_an_older_delivery(spec, ctx)
    _refuse_a_test_evaluation_under_another_configuration(spec, ctx)
    _refuse_a_task_of_a_run_without_a_delivery(spec, ctx)
    started = time.perf_counter()
    _open(ctx)
    _reopen(ctx)
    try:
        result = spec.func(ctx)
    except (ValidationError, RunRefusedError):      # the first marked the run rejected itself; the second refused before writing: neither is a failure of the task
        raise
    except BaseException as error:
        _record_failure(ctx, spec, error)
        logger.error("task %s of run %s failed after %.1f s: %s", spec.task_id, ctx.run_key, time.perf_counter() - started, error)
        raise
    peaks = _peak()
    result = {**result, "seconds": round(time.perf_counter() - started, 2), "peak_working_set_mb": None if peaks is None else round(peaks.working_set_mb, 1),
              "peak_commit_mb": None if peaks is None or peaks.commit_mb is None else round(peaks.commit_mb, 1), "peak_gpu_mb": None if peaks is None or peaks.gpu_mb is None else round(peaks.gpu_mb, 1),
              "memory_counter": None if peaks is None else peaks.counter}
    try:
        _save_task(ctx, spec.task_id, result, status=result.get("run_status"))
    except BaseException as error:
        if result.get("run_status") in CLOSED_STATUSES:
            logger.error("task %s closed run %s as %s but its result could not be saved: run it again", spec.task_id, ctx.run_key, result["run_status"], exc_info=error)
            raise
        _record_failure(ctx, spec, error)
        raise
    logger.info("task %s of run %s: %.1f s", spec.task_id, ctx.run_key, result["seconds"])
    return result


def run_task(task_id: str, run_id: str | None = None, cfg: PipelineConfig | None = None, *, now: datetime | None = None) -> dict:
    """Run one task of the run `run_id` (a new run if None) holding the writer lock: what the Airflow DAG calls."""
    spec = _task_spec(task_id)
    cfg = cfg or load_config()
    when = (now or datetime.now(UTC)).astimezone(UTC)
    with sole_writer(cfg.paths.interim_dir, f"task {task_id}", make_run_key(run_id if run_id is not None else when)):
        return _run_spec(spec, _context(cfg, run_id, when))


def _select(only: list[str] | None, start: str | None) -> list[str]:
    if only is not None and not only:
        raise UsageError("`only` está vacío: indique al menos una tarea")
    if only and start is not None:
        raise UsageError("`only` y `start` no se combinan")
    ids = list(TASK_IDS)
    if start is not None:
        _task_spec(start)
        ids = ids[ids.index(start):]
    if only:
        for task_id in only:
            _task_spec(task_id)
        ids = [i for i in ids if i in only]
    return ids


def run_all(cfg: PipelineConfig, only: list[str] | None = None, start: str | None = None, run_key: str | None = None, *, now: datetime | None = None) -> list[dict]:
    """Run the tasks in order (all, the listed ones or from `start` on) holding the writer lock from before the first to after the last; a run with no delivery stops after ``deliver``."""
    ids = _select(only, start)
    when = (now or datetime.now(UTC)).astimezone(UTC)
    outcome = []
    with sole_writer(cfg.paths.interim_dir, "run", make_run_key(run_key if run_key is not None else when)):
        ctx = _context(cfg, run_key, when)
        for task_id in ids:
            spec = _task_spec(task_id)
            result = _run_spec(spec, ctx)
            outcome.append({"task": task_id, "result": result})
            if spec.short_circuit and not result.get("delivered", True):
                break
    return outcome


def _plan_of(cfg: PipelineConfig) -> list[DeliverySpec]:
    """The plan of deliveries of the configuration (it needs no hash of a source: dates and identifiers only)."""
    return delivery_plan(sealed_view(cfg, {**{f.name: "0" * 64 for f in cfg.source.cord_files}, ARCA_KEY: "0" * 64}))


def replay(cfg: PipelineConfig, until: str | None = None) -> list[list[dict]]:
    """Run the pending deliveries one run after another up to `until`; with nothing pending nothing runs and the lock is not taken."""
    plan = _plan_of(cfg)
    ids = [s.delivery_id for s in plan]
    if until is not None and until not in ids:
        raise UsageError(f"--until {until}: no es una entrega del plan ({', '.join(ids)})")
    last = len(plan) - 1 if until is None else ids.index(until)
    runs: list[list[dict]] = []
    manifest = _manifest_path(cfg)
    pending = next_pending(read_manifest(manifest), plan)
    if pending is None and until is not None:      # every delivery is loaded, `until` too: the same refusal as an `until` before the next pending one
        raise UsageError(f"--until {until}: no hay entregas pendientes (las {len(plan)} del plan ya están cargadas); no hay nada que correr hasta ella. No se escribió nada.")
    if pending is None:
        return runs
    if plan.index(pending) > last:      # nothing up to `until` is pending: the option would be ignored in silence, so it is refused
        raise UsageError(f"--until {until}: es anterior a la siguiente entrega pendiente, {pending.delivery_id}; no hay nada que correr hasta ella. No se escribió nada.")
    with sole_writer(cfg.paths.interim_dir, "replay", None):
        while (pending := next_pending(read_manifest(manifest), plan)) is not None and plan.index(pending) <= last:
            runs.append(run_all(cfg))
            if next_pending(read_manifest(manifest), plan) == pending:
                raise RunStateError(f"la corrida de {pending.delivery_id} terminó sin cargar la entrega: el replay se detiene")
    return runs

