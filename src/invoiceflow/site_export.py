"""The five small JSON files that the personal site copies (RF23, AC28; D38; plan, section 21; risk item 3: another repository consumes their shape). Adapted from project 06's ``site_export.py``.

``export_site`` writes ``summary.json``, ``data.json``, ``algorithms.json``, ``operation.json`` and ``validation_rules.json`` to the site folder from the star model (the ``star_*`` tables that
``export.build_powerbi_marts`` built, the very rows the Parquet files hold, so a figure on the site is the figure in the report), from the summary of the run (``reporting.build_summary``, which takes
the sealed criteria from the seal when the warehouse has none), from the seal and from the validation of each run. **Until the test is sealed with this configuration every result of the test is
empty or ``null``**: the criteria are pending, the baselines, the reading by field, the QR, the review mix, CORD's test and the example invoice are empty, and ``days[]`` shows the days already run
(that is follow-up, not the test).

**Every figure that comes from the synthetic invoices says so**: the dictionary that holds it, or one that contains it, carries ``"synthetic": true`` (the figures of CORD, real receipts, carry
``false``); ``tests/contract/test_site_export.py`` walks every file for a number without a mark. No file holds an image of CORD or a path of this machine, and every CUIT in them is in the synthetic
range (``tests/integration/test_published_bytes.py``).

The files are a contract that another repository consumes: a change of shape is a change of ``tests/contract/test_site_export.py``. Floats are rounded to what the site shows; every file is
``json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)`` with a final newline, and the five together stay under ``max_total_bytes`` (``[site]``). The five files are one export: each is
replaced atomically and ``manifest.json``, written last, names each with its size and SHA-256. The old manifest is removed before the first file is replaced, so a failure between two replacements
leaves a folder with no manifest, which ``verify_site`` refuses; a reader takes the files through ``read_site``. A rule that counts something cumulative (``source`` and ``latest``) is read once, from
the latest loaded run that recorded it, and never summed over the runs (project 03, D94).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import re
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from invoiceflow import labels
from invoiceflow.files import write_atomic
from invoiceflow.match import REASONS
from invoiceflow.metrics import parse_money
from invoiceflow.registry import MODEL_VERSION, RunMeta, config_fingerprint, read_registry, read_seal, seal_state
from invoiceflow.reporting import build_summary
from invoiceflow.synth.invoices import PLANTED_CLASSES, calendar_days, invoices
from invoiceflow.synth.master import master as make_master
from invoiceflow.synth.qrpayload import FIELDS, OPTIONAL_FIELDS
from invoiceflow.synth.render import draw_page

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SITE_FILES = ("summary.json", "data.json", "algorithms.json", "operation.json", "validation_rules.json")
MANIFEST_FILE = "manifest.json"
ENVELOPE = ("schema_version", "run_key", "as_of", "artifact_sha256", "cord_commit", "attribution", "synthetic_note_es")
# Every table of the star model the export reads: the one list ``_require_star`` checks before anything is built (a test compares it with the tables the queries of this module name).
REQUIRED_STAR = ("star_fact_baseline", "star_fact_cord_eval", "star_fact_field_read", "star_fact_invoice", "star_fact_qr_read")
STATUS_WORST_FIRST = ("fail", "warn", "pass")
SMOKE_FILE = "erpnext_smoke.json"          # what the smoke test against ERPNext leaves in the reports folder (U22, AC36); ``operation.smoke_erpnext`` is its content
SMOKE_KEYS = ("synthetic", "image", "image_digest", "erpnext_version", "frappe_version", "tested_on", "documents", "readback_equal", "repost_creates_none", "duplicate_bill_no")
_SMOKE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_LOCAL_PATH = re.compile(r"[A-Za-z]:[\\/]|/(?:home|Users)/|\\Users\\")
CORD_NOTE_ES = ("La prueba de CORD (recibos reales) se abre una sola vez, en el cierre. El checkpoint publicado ya se había medido sobre ella en la investigación (D42): esa cifra no es una medida "
                "independiente del ajuste.")
CUIT_RANGE_ES = "30-0000xxxx-d: el rango del ejemplo de la especificación de ARCA; ninguna factura sintética lleva el CUIT de una empresa"
QR_TYPES = {"ver": "entero", "fecha": "fecha AAAA-MM-DD", "cuit": "entero de 11 dígitos", "ptoVta": "entero", "tipoCmp": "entero", "nroCmp": "entero", "importe": "decimal",
            "moneda": "texto de 3 letras", "ctz": "decimal", "tipoDocRec": "entero", "nroDocRec": "entero", "tipoCodAut": "texto (E o A)", "codAut": "entero de 14 dígitos"}
MISSING_FIELDS_ES = ["proveedor", "CUIT", "fecha", "punto de venta", "número", "moneda"]
TOLERANCES = {"price_out_of_tolerance": "price_tolerance_pct", "qty_over_receipt": "qty_over_receipt", "line_arithmetic": "rounding_per_line", "total_arithmetic": "rounding_per_line",
              "qr_print_mismatch": "total_qr_tolerance"}


class SiteExportError(RuntimeError):
    """The site files cannot be written from this warehouse; nothing was replaced."""


class SiteSetError(FileNotFoundError):
    """The files of a site folder are not one export: one is missing or is not the file ``manifest.json`` names, or the manifest is."""


@dataclass(frozen=True)
class SiteFiles:
    """The five documents of a verified site folder and its manifest, parsed from the bytes that were checked against it."""

    summary: dict[str, Any]
    data: dict[str, Any]
    algorithms: dict[str, Any]
    operation: dict[str, Any]
    validation_rules: dict[str, Any]
    manifest: dict[str, Any]


# --- values -------------------------------------------------------------------------------------------------------------------------


def _num(value: Any, decimals: int = 3) -> float | None:
    if value is None or pd.isna(value) or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return round(float(value), decimals)


def _int(value: Any) -> int | None:
    return None if value is None or pd.isna(value) else int(value)


def _query(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def _require_star(conn: sqlite3.Connection) -> None:
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = [t for t in REQUIRED_STAR if t not in present]
    if missing:
        raise SiteExportError(f"faltan las tablas del modelo estrella {missing}: la tarea export las construye antes de escribir el sitio")


def _header(conn: sqlite3.Connection, run: RunMeta, view: Any) -> dict[str, Any]:
    registered = read_registry(conn, MODEL_VERSION)
    return {"schema_version": SCHEMA_VERSION, "run_key": run.run_key, "as_of": run.as_of.strftime("%Y-%m-%d %H:%M:%S"), "artifact_sha256": registered["artifact_sha256"] if registered else "",
            "cord_commit": view.source.cord_commit, "attribution": labels.ATTRIBUTION, "synthetic_note_es": labels.SYNTHETIC_NOTE_ES}


def _iso(key: Any) -> str:
    text = str(int(key))
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _evaluation_status(summary: dict[str, Any], seal_path: Path, view: Any) -> str:
    if summary["source"] == "pending":
        return "pending"
    return "sealed" if seal_state(seal_path, config_fingerprint(view)) == "match" else "post_seal"


def _ops(conn: sqlite3.Connection) -> pd.DataFrame:
    return _query(conn, "SELECT date_key, invoices, auto_post, review, posted, post_failed, quarantined, seconds_mean, seconds_p95 FROM daily_ops ORDER BY date_key")


def _summary(conn: sqlite3.Connection, run: RunMeta, view: Any, summary: dict[str, Any], seal: dict[str, Any] | None, status: str, seal_path: Path) -> dict[str, Any]:
    ops = _ops(conn)
    test = (seal or {}).get("test") if status == "sealed" else None
    metrics = (test or {}).get("metrics") or {}
    weighted = (ops["seconds_mean"] * ops["invoices"]).sum() / ops["invoices"].sum() if len(ops) and ops["invoices"].sum() else None

    def at(*path: str) -> float | None:
        node: Any = metrics
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return _num(node)

    headline = {"invoices": _int(ops["invoices"].sum()) if len(ops) else 0, "auto_post": _int(ops["auto_post"].sum()) if len(ops) else 0, "review": _int(ops["review"].sum()) if len(ops) else 0,
                "straight_through_known": at("straight_through", "known", "rate"), "straight_through_new": at("straight_through", "new", "rate"),
                "bad_post_rate": {"rate": at("bad_post", "rate"), "ci_high": at("bad_post", "ci_high")} if metrics else None, "line_exact_known": at("lines", "known", "rate"),
                "line_exact_new": at("lines", "new", "rate"), "qr_read": at("qr", "rate"), "seconds_per_invoice": _num(weighted), "seconds_p95": _num(ops["seconds_p95"].max()) if len(ops) else None,
                "seconds_note_es": "segundos de procesamiento por factura, medidos en la corrida; no es el tiempo de una persona", "days": len(ops), "synthetic": True}
    row = conn.execute("SELECT status FROM pipeline_runs WHERE run_key = ?", (run.run_key,)).fetchone()
    launch = (seal or {}).get("launch") or {}
    sealed = {"launch": {"config_sha256": launch.get("config_sha256"), "artifact_sha256": launch.get("artifact_sha256"), "chosen_epoch": launch.get("chosen_epoch"), "as_of": launch.get("as_of")},
              "test": None if not (seal or {}).get("test") else {"run_key": seal["test"]["run_key"], "as_of": seal["test"]["as_of"]},
                                                                                                                                                                             "synthetic": False} if seal else None
    return {"status": row[0] if row else "running", "evaluation_status": status, "headline": headline, "criteria": [{**c, "synthetic": bool(c["synthetic"])} for c in summary["criteria"]],
            "seal": sealed, "sealed_from": summary["source"] if status != "pending" else None,
            "post_seal_analysis": summary.get("post_seal_analysis") if status == "sealed" else None}


# --- data.json -----------------------------------------------------------------------------------------------------------------------


def cord_facts(index: Any, expected_rows: dict[str, int] | None = None) -> dict[str, Any]:
    """What CORD is, from the canonical index of its training and validation receipts (the test split is never opened here): receipts per split (the configured counts), the images that repeat, the
    fields present, the two forms of ``menu``, the median of lines and the totals that can be read. `expected_rows` are the configured counts of the three splits (V05: the sealed view does not
    hold them); without an index the figures that need it are ``None``, and without the counts the receipts of a split the index does not hold are."""
    receipts = {"train": None, "validation": None, "test": None, **({} if expected_rows is None else expected_rows)}
    if index is not None:
        receipts.update({split: len(entries) for split, entries in index.entries.items() if receipts[split] is None})
    facts: dict[str, Any] = {"receipts": receipts, "unique_images": None, "fields_present": None, "menu_forms": {"list": None, "object": None}, "lines_median": None,
                             "totals": {"readable": None, "missing": None, "unreadable": None}, "missing_fields_es": MISSING_FIELDS_ES, "synthetic": False,
                             "scope_es": "los recibos de entrenamiento y de validación; la prueba no se abre hasta el cierre"}
    if index is None:
        return facts
    entries = [e for split in index.entries.values() for e in split]
    n = len(entries)
    totals = [(e.target.get("total") or {}) for e in entries]
    totals = [t[0] if isinstance(t, list) and t else t for t in totals]
    prices = [t.get("total_price") if isinstance(t, dict) else None for t in totals]
    present = {name: sum(1 for e in entries if e.target.get(name)) / n for name in ("menu", "sub_total", "total")} if n else {}
    facts.update(unique_images=sum(1 for e in entries if e.duplicate_of is None), fields_present={k: round(v, 3) for k, v in present.items()},
                 menu_forms={"list": sum(1 for e in entries if not e.menu_was_object), "object": sum(1 for e in entries if e.menu_was_object)},
                 lines_median=statistics.median([len(e.target.get("menu") or []) for e in entries]) if n else None,
                 totals={"readable": sum(1 for p in prices if p is not None and parse_money(p) is not None), "missing": sum(1 for p in prices if p is None),
                         "unreadable": sum(1 for p in prices if p is not None and parse_money(p) is None)})
    return facts


def _synthetic_facts(conn: sqlite3.Connection, view: Any, status: str) -> dict[str, Any]:
    s = view.synth
    days = len(calendar_days(view))
    planted = []
    if status == "sealed":
        counts = _query(conn, "SELECT planted_key, COUNT(*) AS n FROM star_fact_invoice WHERE seal_state = 'sealed' AND planted_key != 'none' GROUP BY planted_key")
        by_class = dict(zip(counts["planted_key"], counts["n"], strict=True))
        planted = [{"class": c, "label_es": labels.PLANTED_LABELS_ES[c], "count": int(by_class[c])} for c in PLANTED_CLASSES if c in by_class]
    layouts = {"known": list(s.layouts_known), "validation_new": s.layout_validation_new}
    if status == "sealed":
        layouts["test_new"] = s.layout_test_new
    return {"suppliers": {"known": s.suppliers_known, "new_validation": s.suppliers_new_validation, "new_test": s.suppliers_new_test},
            "invoices": {"train": s.n_train, "validation_known": s.n_validation_known, "validation_new": s.n_validation_new, "test_known": days * s.test_per_day_known,
                         "test_new": days * s.test_per_day_new}, "layouts": layouts, "scan_profiles": dataclasses.asdict(s.scan_profiles), "formats": dataclasses.asdict(s.file_formats),
            "planted": planted, "cuit_range_es": CUIT_RANGE_ES, "synthetic": True}


def _qr_spec() -> list[dict[str, Any]]:
    return [{"field": name, "type": QR_TYPES[name], "required": name not in OPTIONAL_FIELDS} for name in FIELDS]


def _data(conn: sqlite3.Connection, view: Any, status: str, cord_index: Any, expected_rows: dict[str, int] | None) -> dict[str, Any]:
    return {"cord": cord_facts(cord_index, expected_rows), "synthetic": _synthetic_facts(conn, view, status), "qr_spec": _qr_spec()}


# --- algorithms.json -----------------------------------------------------------------------------------------------------------------


def _example_invoice(view: Any, seal_path: Path) -> dict[str, Any] | None:
    """The first clean ``test_known`` invoice with three lines (the plan's fixed rule): the texts the page says and where, as fractions of the page, and no image. The generator is regenerated
    from the configuration after the launch seal; the truth file is not read."""
    chosen = sorted((t for t in invoices(view, make_master(view), "test_known", None, seal_path=seal_path) if t.planted == "none" and len(t.lines) == 3), key=lambda t: t.invoice_key)
    if not chosen:
        return None
    truth = chosen[0]
    page = draw_page(truth, view)
    width, height = page.image.size
    texts = [{"text": text, "box": [round(min(max(v, 0), limit) / limit, 4) for v, limit in zip(box, (width, height, width, height), strict=True)]} for text, box in page.texts]
    return {"invoice_key": truth.invoice_key, "split": truth.split, "planted": truth.planted, "layout": truth.layout, "lines": len(truth.lines), "texts": texts, "synthetic": True}


def _choice(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    registered = read_registry(conn, MODEL_VERSION)
    if registered is None:
        return []
    choice = json.loads(registered["choice_json"])["choice"]
    return [{"epoch": c["epoch"], "cord_val_ted": _num(c["cord_ted"], 2), "exact_known": _num(c["exact_known"]), "exact_new": _num(c["exact_new"]), "status": c["status"],
             "chosen": c["epoch"] == choice["chosen_epoch"], "fit_splits": c["fit_splits"], "synthetic": True} for c in choice["candidates"]]


def _sealed(conn: sqlite3.Connection, table: str, status: str, order: str) -> pd.DataFrame:
    if status != "sealed":
        return pd.DataFrame()
    return _query(conn, f"SELECT * FROM {table} WHERE seal_state = 'sealed' ORDER BY {order}")


def _algorithms(conn: sqlite3.Connection, view: Any, seal: dict[str, Any] | None, status: str, seal_path: Path) -> dict[str, Any]:
    base = _sealed(conn, "star_fact_baseline", status, "baseline_key, cohort_key")
    baselines = [{"baseline": r.baseline_key, "label_es": labels.BASELINE_LABELS_ES[r.baseline_key], "cohort": r.cohort_key, "auto_post": int(r.auto_post), "bad_post": int(r.bad_post),
                  "bad_post_rate": _num(r.bad_post_rate), "ci_low": _num(r.ci_low), "ci_high": _num(r.ci_high), "straight_through": _num(r.straight_through),
                  "review_recall": _num(r.review_recall), "synthetic": True} for r in base.itertuples()]
    b2 = [b for b in baselines if b["baseline"] == "b2"]
    fields = _sealed(conn, "star_fact_field_read", status, "cohort_key, field_key, scan_key")
    grouped = fields.groupby(["cohort_key", "field_key"])[["leaves", "correct"]].sum().reset_index() if len(fields) else fields
    field_rows = [{"cohort": r.cohort_key, "field": r.field_key, "label_es": labels.FIELD_LABELS_ES.get(r.field_key, r.field_key), "leaves": int(r.leaves), "correct": int(r.correct),
                   "recall": _num(r.correct / r.leaves) if r.leaves else None, "synthetic": True} for r in grouped.itertuples()]
    reads = _sealed(conn, "star_fact_qr_read", status, "cohort_key, scan_key, qr_cm, reader")
    qr_rows = [{"cohort": r.cohort_key, "scan": r.scan_key, "qr_cm": _num(r.qr_cm, 1), "reader": r.reader, "printed": int(r.printed), "read_ok": int(r.read_ok), "synthetic": True} for r in reads.itertuples()]
    cord = None
    test = (seal or {}).get("test") if status == "sealed" else None
    if test:
        rows = _sealed(conn, "star_fact_cord_eval", status, "model_key")
        per_model = {r.model_key: {"ted": _num(r.ted_mean, 2), "leaf_recall": _num(r.leaf_recall), "receipts": int(r.receipts), "fit_splits": r.fit_splits} for r in rows.itertuples()}
        figures = test["cord"]
        cord = {"receipts": figures["receipts"], "ted_chosen": _num(figures["ted_chosen"], 2), "ted_checkpoint": _num(figures["ted_checkpoint"], 2), "models": per_model,
                "diff": {k: _num(v, 2) for k, v in figures["diff"].items()}, "note_es": CORD_NOTE_ES, "synthetic": False}
    tolerances = dataclasses.asdict(view.match)
    rules = [{"reason": reason, "rank": i, "label_es": labels.REASON_TEXTS[reason][0], "rule_es": labels.REASON_TEXTS[reason][1],
              "tolerance": {TOLERANCES[reason]: tolerances[TOLERANCES[reason]]} if reason in TOLERANCES else None, "synthetic": False} for i, reason in enumerate(REASONS, start=1)]
    return {"checkpoint_on_invoices": {"cohorts": b2, "synthetic": True} if b2 else None, "choice": _choice(conn), "fields": field_rows, "qr": qr_rows, "match_rules": rules,
            "baselines": baselines, "cord": cord, "example_invoice": _example_invoice(view, seal_path) if status == "sealed" else None}


# --- operation.json ------------------------------------------------------------------------------------------------------------------


def _operation(conn: sqlite3.Connection, status: str, smoke: dict[str, Any] | None) -> dict[str, Any]:
    ops = _ops(conn)
    reasons = _query(conn, "SELECT dl.day AS day, d.first_reason AS reason, COUNT(*) AS n FROM decisions d JOIN invoices i ON i.invoice_key = d.invoice_key "
                           "JOIN deliveries dl ON dl.delivery_id = i.delivery_id WHERE d.decision = 'review' GROUP BY dl.day, d.first_reason")
    by_day: dict[str, dict[str, int]] = {}
    for r in reasons.itertuples():
        by_day.setdefault(r.day, {})[r.reason] = int(r.n)
    days = [{"date": _iso(r.date_key), "invoices": int(r.invoices), "auto_post": int(r.auto_post), "review": int(r.review), "by_reason": dict(sorted(by_day.get(_iso(r.date_key), {}).items())),
             "seconds_mean": _num(r.seconds_mean), "seconds_p95": _num(r.seconds_p95), "synthetic": True} for r in ops.itertuples()]
    mix = []
    if status == "sealed":
        counts = _query(conn, "SELECT planted_key, first_reason_key, COUNT(*) AS n FROM star_fact_invoice WHERE seal_state = 'sealed' AND decision = 'review' GROUP BY planted_key, first_reason_key")
        for planted, part in counts.groupby("planted_key"):
            mix.append({"planted": planted, "label_es": labels.PLANTED_LABELS_ES[planted], "by_reason": {r.first_reason_key: int(r.n) for r in part.sort_values("first_reason_key").itertuples()}, "synthetic": True})
    posts = {s: n for s, n in conn.execute("SELECT status, COUNT(*) FROM erp_posts GROUP BY status")}
    equal = conn.execute("SELECT COALESCE(SUM(readback_equal = 1), 0) FROM erp_posts WHERE status = 'posted'").fetchone()[0]
    erp = {"posted": posts.get("posted", 0), "already_posted": posts.get("already_posted", 0), "post_failed": posts.get("post_failed", 0), "readback_equal": int(equal), "synthetic": True}
    return {"days": days, "review_mix": mix, "erp": erp, "smoke_erpnext": smoke}


# --- validation_rules.json -----------------------------------------------------------------------------------------------------------


def _loaded_rule_rows(conn: sqlite3.Connection) -> pd.DataFrame:
    """The rule results of the runs whose delivery is loaded, with each run's instant."""
    return _query(conn, "SELECT v.rule_id, v.name_es, v.severity, v.scope, v.aggregation, v.passed, v.files_affected, d.as_of FROM validation_results AS v "
                        "JOIN deliveries AS d ON d.run_key = v.run_key AND d.status = 'loaded' ORDER BY d.as_of, v.rule_id")


def _aggregated(rows: pd.DataFrame, rule_id: str) -> pd.DataFrame:
    """The rows of `rule_id` that its aggregation counts (plan, section 6): ``source`` and ``latest`` read once, from the latest loaded run that recorded the rule (a cumulative count summed over the
    runs would count the earlier files again; project 03's D94), ``per_run`` every loaded run."""
    mine = rows[rows["rule_id"] == rule_id]
    if len(mine) and mine["aggregation"].iloc[0] in ("source", "latest"):
        mine = mine[mine["as_of"] == mine["as_of"].max()]
    return mine


def _rule_order(rule_id: str) -> tuple[int, int]:
    return "VIM".index(rule_id[0]) if rule_id[0] in "VIM" else 9, int(rule_id[1:])


def _validation(conn: sqlite3.Connection) -> dict[str, Any]:
    from invoiceflow.validate import RULES

    rows = _loaded_rule_rows(conn)
    known = {rule_id: (name, severity, scope, aggregation) for rule_id, (name, severity, scope, aggregation) in RULES.items()}
    for r in rows.drop_duplicates("rule_id").itertuples():
        known.setdefault(r.rule_id, (r.name_es, r.severity, r.scope, r.aggregation))
    rules = []
    for rule_id in sorted(known, key=_rule_order):
        name, severity, scope, aggregation = known[rule_id]
        mine = _aggregated(rows, rule_id)
        found = ["pass" if bool(p) else ("fail" if s == "bloqueante" else "warn") for p, s in zip(mine["passed"], mine["severity"], strict=True)]
        status = "not_evaluated" if mine.empty else next(s for s in STATUS_WORST_FIRST if s in found)
        rules.append({"rule_id": rule_id, "name_es": name, "severity": severity, "scope": scope, "aggregation": aggregation, "status": status, "files": int(mine["files_affected"].sum()), "synthetic": True})
    loaded, rejected = conn.execute("SELECT COALESCE(SUM(status = 'loaded'), 0), COALESCE(SUM(status = 'rejected'), 0) FROM deliveries").fetchone()
    return {"rules": rules, "totals": {"invoices": int(conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]), "deliveries_loaded": int(loaded), "deliveries_rejected": int(rejected), "synthetic": True}}


# --- reading, verifying and writing the set -------------------------------------------------------------------------------------------------


def _text(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False) + "\n"


def _read_checked(out_dir: Path) -> tuple[dict[str, bytes], dict[str, Any], list[str]]:
    out_dir = Path(out_dir)
    try:
        manifest = json.loads((out_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
        files = manifest["files"]
        if not isinstance(files, dict) or not all(isinstance(e, dict) for e in files.values()):
            raise TypeError(MANIFEST_FILE)
    except FileNotFoundError:
        return {}, {}, [f"falta {MANIFEST_FILE} en la carpeta del sitio: el conjunto no es una exportación completa"]
    except (ValueError, KeyError, TypeError):
        return {}, {}, [f"{MANIFEST_FILE} de la carpeta del sitio no se puede leer"]
    problems = [] if sorted(files) == sorted(SITE_FILES) else [f"{MANIFEST_FILE} nombra {sorted(files)} y no los cinco archivos del sitio"]
    out: dict[str, bytes] = {}
    for name in SITE_FILES:
        path = out_dir / name
        if not path.is_file():
            problems.append(f"falta {name} en la carpeta del sitio")
            continue
        data = path.read_bytes()
        entry = files.get(name, {})
        if len(data) != entry.get("bytes") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            problems.append(f"{name} no es el que nombra {MANIFEST_FILE} (otro tamaño o SHA-256): los archivos no son de una misma exportación")
            continue
        try:
            document = json.loads(data.decode("utf-8"))
        except ValueError:
            problems.append(f"{name} no es un JSON legible")
            continue
        if any(document.get(key) != manifest.get(key) for key in ENVELOPE):
            problems.append(f"{name} no lleva el envoltorio de {MANIFEST_FILE} (la corrida, el instante o la fuente son otros)")
        out[name] = data
    return out, manifest, problems


def read_smoke(path: Path) -> dict[str, Any] | None:
    """The result the smoke test left at `path`, or None when there is no file (the test was not run: ``smoke_erpnext`` is ``null``). A file that is not exactly that object, whose figures are not
    labelled synthetic, whose digest is not a SHA-256 or that names a path of this machine is a ``SiteExportError``: a result is published whole or not at all."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
    except (OSError, ValueError) as error:
        raise SiteExportError(f"{path.name} no se puede leer como JSON ({type(error).__name__})") from None
    if not (isinstance(document, dict) and set(document) == set(SMOKE_KEYS) and document["synthetic"] is True and isinstance(document["image_digest"], str)
            and _SMOKE_DIGEST.fullmatch(document["image_digest"]) and not _LOCAL_PATH.search(text)):
        raise SiteExportError(f"{path.name} no es el resultado de la prueba de humo de ERPNext: se esperan exactamente las claves {list(SMOKE_KEYS)}, con synthetic verdadero, la huella de la imagen y sin rutas")
    return document


def read_site(out_dir: Path) -> SiteFiles:
    """The five documents of `out_dir`, each read once and checked against ``manifest.json``; ``SiteSetError`` if they are not one export."""
    data, manifest, problems = _read_checked(out_dir)
    if problems:
        raise SiteSetError("; ".join(problems))
    documents = {name.removesuffix(".json"): json.loads(data[name].decode("utf-8")) for name in SITE_FILES}
    return SiteFiles(**documents, manifest=manifest)


def verify_site(out_dir: Path) -> list[str]:
    """What keeps the files of `out_dir` from being one export (``[]`` when the manifest names the five with their size and SHA-256 and each carries its envelope)."""
    return _read_checked(out_dir)[2]


def _write(texts_by_name: dict[str, str], out_dir: Path, cap: int, header: dict[str, Any]) -> list[Path]:
    out_dir = Path(out_dir)
    payloads = {name: texts_by_name[name].encode("utf-8") for name in SITE_FILES}
    total = sum(len(p) for p in payloads.values())
    if total > cap:
        raise SiteExportError(f"los archivos del sitio pesan {total} bytes y el tope es {cap}: no se escribió ninguno")
    manifest = _text({**header, "files": {n: {"bytes": len(p), "sha256": hashlib.sha256(p).hexdigest()} for n, p in payloads.items()}})
    (out_dir / MANIFEST_FILE).unlink(missing_ok=True)         # a set being rewritten has no manifest until its last file is written
    paths = [write_atomic(out_dir / name, texts_by_name[name]) for name in SITE_FILES]
    write_atomic(out_dir / MANIFEST_FILE, manifest)
    logger.info("site export %s: %d files, %d bytes", header["run_key"], len(paths), total)
    return paths


def _documents(conn: sqlite3.Connection, run: RunMeta, view: Any, seal_path: Path, cord_index: Any, expected_rows: dict[str, int] | None, smoke_erpnext: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    summary, seal = build_summary(conn, view, seal_path=seal_path), read_seal(seal_path)
    status = _evaluation_status(summary, seal_path, view)
    return {"summary.json": _summary(conn, run, view, summary, seal, status, seal_path), "data.json": _data(conn, view, status, cord_index, expected_rows),
            "algorithms.json": _algorithms(conn, view, seal, status, seal_path), "operation.json": _operation(conn, status, smoke_erpnext), "validation_rules.json": _validation(conn)}


def export_site(conn: sqlite3.Connection, out_dir: Path, run: RunMeta, view: Any, *, seal_path: Path, max_total_bytes: int, cord_index: Any = None,
                expected_rows: dict[str, int] | None = None, smoke_erpnext: dict[str, Any] | None = None) -> list[Path]:
    """Write the five site files from the star model of `conn`, then ``manifest.json``; returns the five paths (``SITE_FILES`` order). Every document is built before the first file is replaced, and
    nothing is written if together they would pass `max_total_bytes` (``[site]``). `view` is the sealed view, as for ``export_star_schema``; `cord_index` is the canonical index of CORD's
    training and validation receipts (what ``data.json`` tells about CORD), `expected_rows` the configured receipts of each of its splits, `smoke_erpnext` the result of the ERPNext smoke test, or None."""
    _require_star(conn)
    header = _header(conn, run, view)
    documents = _documents(conn, run, view, Path(seal_path), cord_index, expected_rows, smoke_erpnext)
    return _write({name: _text({**header, **documents[name]}) for name in SITE_FILES}, out_dir, max_total_bytes, header)


def export_summary(conn: sqlite3.Connection, out_dir: Path, run: RunMeta, view: Any, *, seal_path: Path, max_total_bytes: int) -> Path:
    """Rewrite only ``summary.json`` (what closing a run changes: its status) and the manifest with it, the manifest removed before and written again last. The other four must be one export of this
    same run: over a folder that is not one, or is of another run, nothing is written (``SiteExportError``)."""
    _require_star(conn)
    header = _header(conn, run, view)
    out_dir = Path(out_dir)
    problems = verify_site(out_dir)
    if not problems and json.loads((out_dir / MANIFEST_FILE).read_text(encoding="utf-8"))["run_key"] != header["run_key"]:
        problems = ["el conjunto es de otra corrida"]
    if problems:
        raise SiteExportError(f"no se reescribe el resumen: {'; '.join(problems)}; la tarea export escribe los cinco archivos de nuevo")
    summary, seal = build_summary(conn, view, seal_path=seal_path), read_seal(seal_path)
    status = _evaluation_status(summary, Path(seal_path), view)
    payloads = {name: (out_dir / name).read_bytes() for name in SITE_FILES if name != "summary.json"}
    payloads["summary.json"] = _text({**header, **_summary(conn, run, view, summary, seal, status, Path(seal_path))}).encode("utf-8")
    total = sum(len(p) for p in payloads.values())
    if total > max_total_bytes:
        raise SiteExportError(f"los archivos del sitio pesan {total} bytes y el tope es {max_total_bytes}: no se escribió ninguno")
    manifest = _text({**header, "files": {n: {"bytes": len(payloads[n]), "sha256": hashlib.sha256(payloads[n]).hexdigest()} for n in SITE_FILES}})
    (out_dir / MANIFEST_FILE).unlink(missing_ok=True)
    path = write_atomic(out_dir / "summary.json", payloads["summary.json"])
    write_atomic(out_dir / MANIFEST_FILE, manifest)
    logger.info("site summary %s rewritten: %d bytes", header["run_key"], len(payloads["summary.json"]))
    return path


__all__ = ["MANIFEST_FILE", "SITE_FILES", "SiteExportError", "SiteFiles", "SiteSetError", "cord_facts", "export_site", "export_summary", "read_site", "verify_site"]
