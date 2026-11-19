"""The star model for Power BI: the marts in SQLite and one Parquet file per table (RF21, AC26; D37; plan, section 19). Adapted from project 06's ``export.py``.

``build_powerbi_marts`` loads the reference tables that come from the code, the registry, the seal and the master (``ref_*``: the labels in Spanish, the suppliers, the calendar, the two models and
the candidate epochs, and the texts the summary publishes for each criterion, from ``reporting.criteria_texts``: never a second copy of those sentences), rebuilds the tables of ``sql/marts/powerbi`` (``star_<name>``) and runs the controls of ``sql/checks/model``, all in one transaction: a control with rows undoes it. Everything
is **as of the run's instant**: a day that had not closed is not in the marts.

``export_star_schema`` writes every table of the semantic model with its Arrow schema and the run, the instant, the artifact, the synthetic mark and the attribution as file metadata, to the
output folder. A file over ``MAX_PARQUET_BYTES`` is an error before anything is written; each file is replaced atomically. The folder's ``manifest.json`` is removed before its first file is written
and written again after its last one, with the run and each file's name, bytes and SHA-256 (RNF06): ``verify_star_schema`` checks a folder against it, so a set left half written by a failure has no
manifest and is refused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from invoiceflow import labels
from invoiceflow.dashboard.semantic_model import SemanticModel, Table, arrow_schema, validate_model
from invoiceflow.files import write_atomic
from invoiceflow.registry import MODEL_VERSION, RunMeta, read_registry, read_seal
from invoiceflow.reporting import criteria_texts
from invoiceflow.synth.invoices import FIRST_REASON, PLANTED_CLASSES
from invoiceflow.synth.master import master as make_master
from invoiceflow.warehouse import SQL_DIR, _fail_on, apply_schema, rebuild_marts, run_checks, run_sql_file

logger = logging.getLogger(__name__)

MAX_PARQUET_BYTES = 25 * 1024 * 1024        # a versioned file stays under GitHub's limits (AC37)
STAR_PREFIX = "star_"
METADATA_KEYS = ("invoiceflow.run_key", "invoiceflow.as_of", "invoiceflow.artifact_sha256", "invoiceflow.synthetic", "invoiceflow.attribution")
STAR_MANIFEST = "manifest.json"
RUN_TABLE = "dim_pipeline_run"
RUN_TABLE_MART = "mart_10_dim_pipeline_run.sql"
MANIFEST_VERSION = 1
STAMP = "%Y-%m-%d %H:%M:%S"
CHECKPOINT_SPLITS = "checkpoint"         # the fit_splits of the unmodified checkpoint (as ``launch.CHECKPOINT_SPLITS``; importing ``launch`` would import the model code)


def metadata(run: RunMeta, artifact_sha256: str | None) -> dict[str, str]:
    """What every Parquet file says about the run that wrote it (RNF08, RNF10, RNF12): the figures of the invoices are synthetic, the receipts of CORD are real and the attribution says both."""
    return dict(zip(METADATA_KEYS, (run.run_key, run.as_of.strftime(STAMP), artifact_sha256 or "", "true", labels.ATTRIBUTION), strict=True))


def _replace(conn: sqlite3.Connection, name: str, columns: str, rows: list[tuple]) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.execute(f"CREATE TABLE {name} ({columns})")
    if rows:
        conn.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in rows[0])})", rows)


def _registered(conn: sqlite3.Connection) -> dict[str, Any]:
    row = read_registry(conn, MODEL_VERSION)
    if row is None:
        raise RuntimeError(f"no hay un modelo registrado con la versión {MODEL_VERSION}: el modelo estrella se exporta después del lanzamiento")
    return row


def _launch_seal_state(registered: dict[str, Any], seal_path: Path) -> str:
    """``sealed`` if the registered model was fitted with the configuration of the seal, ``post_seal`` if with another one."""
    seal = read_seal(seal_path)
    return "sealed" if seal is not None and registered["config_sha256"] == seal["launch"]["config_sha256"] else "post_seal"


def _calendar(view: Any, as_of: datetime) -> list[tuple[int, str, str, str]]:
    """The days of the simulated calendar that had closed at `as_of`: ``(date_key, date, weekday_es, period)``."""
    days, day = [], view.calendar.history_start
    while day < as_of.date() and day <= view.calendar.test_last_day:
        if not view.calendar.weekdays_only or day.weekday() < 5:
            days.append((int(day.strftime("%Y%m%d")), day.isoformat(), labels.weekday_es(day), "history" if day <= view.calendar.launch_last_day else "test"))
        day += timedelta(days=1)
    return days


def _load_references(conn: sqlite3.Connection, view: Any, seal_path: Path, as_of: datetime | None = None) -> None:
    """The ``ref_*`` tables the marts read."""
    registered = _registered(conn)
    choice = json.loads(registered["choice_json"])["choice"]
    suppliers = [(s.supplier_key, s.name_es, s.cuit, s.cohort, s.layout) for s in make_master(view).suppliers]
    reasons = [(r, i, *labels.REASON_TEXTS[r]) for i, r in enumerate(labels.REASON_KEYS, start=1)] + [(labels.NO_REASON, 0, *labels.NONE_REASON_TEXT)]
    fields = [(key, labels.FIELD_GROUPS_ES[key.split(".")[0]], labels.FIELD_LABELS_ES[key]) for key in labels.FIELD_KEYS]
    planted = [("none", labels.PLANTED_LABELS_ES["none"], None)] + [(c, labels.PLANTED_LABELS_ES[c], FIRST_REASON[c][0]) for c in PLANTED_CLASSES]
    chosen_fit = next((c["fit_splits"] for c in choice["candidates"] if c["epoch"] == choice["chosen_epoch"]), "")
    models = [("chosen", registered["model_version"], registered["chosen_epoch"], registered["artifact_sha256"], registered["config_sha256"], chosen_fit),
              ("checkpoint", view.model.base_revision, None, None, None, CHECKPOINT_SPLITS)]
    candidates = [(c["epoch"], c["cord_ted"], c["exact_known"], c["exact_new"], int(c["status"] == "kept"), int(c["epoch"] == choice["chosen_epoch"]), c["fit_splits"]) for c in choice["candidates"]]
    _replace(conn, "ref_date", "date_key INTEGER PRIMARY KEY, date TEXT, weekday_es TEXT, period TEXT", _calendar(view, as_of or datetime.max))
    _replace(conn, "ref_supplier", "supplier_key TEXT PRIMARY KEY, name_es TEXT, cuit TEXT, cohort TEXT, layout TEXT", [*suppliers, (labels.UNIDENTIFIED_SUPPLIER, "Sin proveedor identificado", None, "none", "none")])
    _replace(conn, "ref_reason", "reason_key TEXT PRIMARY KEY, rank INTEGER, label_es TEXT, rule_es TEXT", reasons)
    _replace(conn, "ref_field", "field_key TEXT PRIMARY KEY, group_es TEXT, label_es TEXT", fields)
    _replace(conn, "ref_cohort", "cohort_key TEXT PRIMARY KEY, label_es TEXT", list(labels.COHORT_LABELS_ES.items()))
    _replace(conn, "ref_scan", "scan_key TEXT PRIMARY KEY, label_es TEXT", list(labels.SCAN_LABELS_ES.items()))
    _replace(conn, "ref_planted", "planted_key TEXT PRIMARY KEY, label_es TEXT, expected_reason_key TEXT", planted)
    _replace(conn, "ref_baseline", "baseline_key TEXT PRIMARY KEY, label_es TEXT", list(labels.BASELINE_LABELS_ES.items()))
    _replace(conn, "ref_model", "model_key TEXT PRIMARY KEY, model_version TEXT, chosen_epoch INTEGER, artifact_sha256 TEXT, config_sha256 TEXT, fit_splits TEXT", models)
    _replace(conn, "ref_choice", "epoch INTEGER PRIMARY KEY, cord_val_ted REAL, val_known_line_exact REAL, val_new_line_exact REAL, kept INTEGER, chosen INTEGER, fit_splits TEXT", candidates)
    _replace(conn, "ref_seal", "launch_state TEXT", [(_launch_seal_state(registered, seal_path),)])
    published = [(cid, state, t["verdict_es"], t["sealed_verdict_es"], t["threshold_figure_es"]) for (cid, state), t in sorted(criteria_texts(conn, seal_path=seal_path).items())]
    _replace(conn, "ref_criterion_text", "criterion_id TEXT, seal_state TEXT, verdict_es TEXT, sealed_verdict_es TEXT, threshold_figure_es TEXT, PRIMARY KEY (criterion_id, seal_state)", published)


def build_powerbi_marts(conn: sqlite3.Connection, run: RunMeta, view: Any, *, seal_path: Path) -> None:
    """Rebuild the reference tables and the star model as of ``run.as_of`` and run its controls in one transaction; a control with rows undoes it all (``CheckFailedError``) and the marts keep
    what they had."""
    apply_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _load_references(conn, view, seal_path, run.as_of)
        rebuild_marts(conn, "powerbi")
        _fail_on(run_checks(conn, "model"), f"star model at {run.as_of}", "La exportación del modelo estrella")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        logger.warning("star model at %s rolled back: the marts keep what they had", run.as_of)
        raise


_CONVERT = {
    "int8": int, "int16": int, "int32": int, "int64": int, "float64": float, "bool": bool, "string": str, "date": lambda v: date.fromisoformat(str(v)[:10]),
    "datetime": lambda v: datetime.fromisoformat(str(v)),
}


def _arrow_table(columns: list[tuple], table: Table) -> pa.Table:
    """The values of the columns (one tuple of values per column) as an Arrow table with the model's schema; a key with a missing value is an error."""
    arrays = []
    for column, values in zip(table.columns, columns, strict=True):
        if column.name in table.key and any(v is None for v in values):
            raise ValueError(f"{table.name}[{column.name}] tiene valores vacíos: una clave no puede")
        convert = _CONVERT[column.dtype]
        arrays.append(pa.array([None if v is None else convert(v) for v in values], type=arrow_schema(table).field(column.name).type))
    return pa.Table.from_arrays(arrays, schema=arrow_schema(table))


def _serialize(conn: sqlite3.Connection, table: Table, meta: dict[str, str]) -> bytes:
    names, order = (", ".join(f'"{name}"' for name in names) for names in (table.column_names, table.key))
    rows = conn.execute(f"SELECT {names} FROM {STAR_PREFIX}{table.name} ORDER BY {order}").fetchall()
    arrow = _arrow_table(list(zip(*rows, strict=True)) if rows else [()] * len(table.columns), table)
    arrow = arrow.replace_schema_metadata({**(arrow.schema.metadata or {}), **{k.encode(): v.encode("utf-8") for k, v in meta.items()}})
    sink = pa.BufferOutputStream()
    pq.write_table(arrow, sink, compression="snappy")
    return sink.getvalue().to_pybytes()


def _manifest_text(meta: dict[str, str], files: list[dict]) -> str:
    document = {"version": MANIFEST_VERSION, **{key.removeprefix("invoiceflow."): value for key, value in meta.items()}, "files": files}
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def verify_star_schema(folder: Path) -> list[str]:
    """What does not match in `folder` against its ``manifest.json`` (empty when everything does): a missing or unreadable manifest, a listed file that is missing or whose bytes or SHA-256
    differ, and a Parquet file the manifest does not list."""
    folder = Path(folder)
    try:
        manifest = json.loads((folder / STAR_MANIFEST).read_text(encoding="utf-8"))
        listed = {entry["name"]: entry for entry in manifest["files"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f"{STAR_MANIFEST}: no hay un manifiesto legible ({type(error).__name__}); el conjunto no se usa"]
    problems = []
    for name, entry in listed.items():
        path = folder / name
        if not path.is_file():
            problems.append(f"{name}: falta el archivo que lista el manifiesto")
            continue
        data = path.read_bytes()
        if len(data) != entry.get("bytes") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            problems.append(f"{name}: los bytes no son los del manifiesto")
    problems += [f"{path.name}: el manifiesto no lo lista" for path in sorted(folder.glob("*.parquet")) if path.name not in listed]
    return problems


def export_star_schema(conn: sqlite3.Connection, out_dir: Path, model: SemanticModel, run: RunMeta, view: Any, *, seal_path: Path) -> list[Path]:
    """Rebuild the marts as of the run and write one Parquet file per table of `model` to `out_dir`, with the manifest last. Returns the paths in the model's order. The model is checked first and
    every file is serialized and measured before any is written."""
    problems = validate_model(model)
    if problems:
        raise ValueError(f"el modelo semántico es incoherente: {problems[0]}")
    build_powerbi_marts(conn, run, view, seal_path=seal_path)
    meta = metadata(run, _registered(conn)["artifact_sha256"])
    payloads = {}
    for table in model.tables:
        data = _serialize(conn, table, meta)
        if len(data) > MAX_PARQUET_BYTES:
            raise ValueError(f"{table.name}.parquet pesa {len(data)} bytes, más de {MAX_PARQUET_BYTES}: no se escribe nada")
        payloads[table.name] = data
    out_dir = Path(out_dir)
    (out_dir / STAR_MANIFEST).unlink(missing_ok=True)         # a set being rewritten has no manifest until its last file is written
    paths = [write_atomic(out_dir / f"{table.name}.parquet", payloads[table.name]) for table in model.tables]
    files = [{"name": f"{t.name}.parquet", "bytes": len(payloads[t.name]), "sha256": hashlib.sha256(payloads[t.name]).hexdigest()} for t in model.tables]
    write_atomic(out_dir / STAR_MANIFEST, _manifest_text(meta, files))
    logger.info("star model at %s: %d files in %s", run.as_of, len(paths), out_dir.name)
    return paths


def export_run_table(conn: sqlite3.Connection, out_dir: Path, model: SemanticModel, run: RunMeta, view: Any) -> Path | None:
    """Rewrite only ``dim_pipeline_run.parquet`` from ``pipeline_runs`` and its line of the folder's manifest: what closing a run, a failure or a run with no delivery changes (plan, sections 3 and
    22). Nothing is created that was not there: without an exported set that verifies against its manifest it logs a warning naming the run and the reason and returns None, and every file keeps its
    bytes (a set with a file of another export would otherwise look whole). The other files, and the manifest's own header, are not touched; the manifest is removed before the file is replaced and
    written again last, as in ``export_star_schema``."""
    out_dir = Path(out_dir)
    manifest_path = out_dir / STAR_MANIFEST
    problems = ["there is no manifest.json: nothing was exported here"] if not manifest_path.is_file() else verify_star_schema(out_dir)
    if problems:
        logger.warning("run table of run %s not rewritten: the exported set does not match its manifest, so every file is kept as it is: %s", run.run_key, "; ".join(problems))
        return None
    table = model.table(RUN_TABLE)
    conn.execute("BEGIN IMMEDIATE")
    try:
        run_sql_file(conn, SQL_DIR / "marts" / "powerbi" / RUN_TABLE_MART)
        data = _serialize(conn, table, metadata(run, _registered(conn)["artifact_sha256"]))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["name"] == f"{RUN_TABLE}.parquet":
            entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    manifest_path.unlink(missing_ok=True)
    path = write_atomic(out_dir / f"{RUN_TABLE}.parquet", data)
    write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    logger.info("run table rewritten: %s (%d bytes)", path.name, len(data))
    return path
