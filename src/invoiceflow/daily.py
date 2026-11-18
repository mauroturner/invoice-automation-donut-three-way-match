"""The daily run (RF09, RF13, RF14, RF18; D15, D17, D22, D28; plan sections 3, 14 and 17; risk item 1: what was issued is not redone).

``run_day`` takes the accepted and quarantined files of a loaded daily delivery, in the order of their ``invoice_key``, and for each invoice that was not issued yet:

1. reads the page with the extractor of the sealed model and normalizes it, reads the QR (``read_qr``), asks the ERP what it knew on the day (``erp_view``) and decides (``decide``): a
   quarantined file has no page and is review with its rule;
2. **posts right after its decision** when it is ``auto_post`` (``post_invoice``: one draft, idempotent, read back), so what an earlier invoice of the day posted is already invoiced when the
   next one is looked at (L99);
3. writes everything of the invoice in **one transaction** (the fields read, the QR, the decision, its reasons and the post): an invoice is issued whole or not at all.

A failed read of the ERP (``ErpUnreachable``, ``ErpRejected``, ``ErpAuthError`` out of ``erp_view``) is **not an invoice's verdict**: it stops the task, nothing of that invoice is written, the
invoices before it stay issued, and the task run again resumes with the rest and gives what an uninterrupted day gives (L100, D52). ``post_invoice`` is the one that catches those errors,
because a draft that was not created is a fact about that invoice (``post_failed``, which keeps the decision and puts it in the review queue).

A day uses its own date for what the ERP knew (``ErpView.day``), the sealed model and the warehouse as it was; it does not depend on how many days a delivery brought (AC21) nor on what
arrived after (AC11). What is issued is never changed: the tables are insert-only, and an invoice that is issued is found out and left alone. ``run_day`` refuses (``ScoringError``) a delivery
that is not a loaded daily one, a run whose instant is not the delivery's, a day after the delivery or not after the launch, and an invoice that is not the delivery's. The row of the day
(``daily_ops``) is written when the day is complete, and counts the whole day whoever issued each invoice.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from invoiceflow.config import SealedConfig
from invoiceflow.decide import Decision, decide
from invoiceflow.deliveries import DeliverySpec
from invoiceflow.erp.client import PostOutcome, invoice_doc
from invoiceflow.intake import load_page
from invoiceflow.match import ErpView, erp_view
from invoiceflow.normalize import InvoiceFields, normalize
from invoiceflow.qr import QrRead, read_qr
from invoiceflow.registry import RunMeta

logger = logging.getLogger(__name__)

HEADER_FIELDS = ("tipo", "pto_vta", "nro", "fecha", "cuit_emisor", "razon_social", "cuit_receptor", "orden_compra", "moneda", "cae")
LINE_FIELDS = (("code", "codigo", "codigo"), ("desc", "descripcion", "descripcion"), ("qty", "cantidad", "cantidad"), ("unit", "unidad", "unidad"),
               ("unit_price", "precio_unitario", "precio_unitario"), ("amount", "importe", "importe"))
TOTAL_FIELDS = ("neto", "iva_21", "iva_105", "total")


class ScoringError(RuntimeError):
    """The days of this delivery cannot be issued: it is not a loaded daily delivery, the run is not at its instant, a day is not one the delivery can have, or an invoice is not its own. The
    command line exits with 2; nothing was written."""

    EXIT_CODE = 2


@dataclass(frozen=True)
class DayResult:
    """What ``run_day`` did: the delivery and the day, whether anything was issued (``False``: every invoice was already issued), how many invoices were issued now and of what kind, and the seconds
    each took (reading, QR, match and post)."""

    delivery_id: str
    day: date
    issued: bool
    invoices: int = 0
    quarantined: int = 0
    auto_post: int = 0
    review: int = 0
    posted: int = 0
    post_failed: int = 0
    seconds: tuple[float, ...] = field(default_factory=tuple)


def _stamp(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _norm_header(fields: InvoiceFields, name: str) -> str | None:
    value = getattr(fields, name)
    return value.isoformat() if isinstance(value, date) else _text(value)


def field_rows(key: str, raw: Any, fields: InvoiceFields, model_version: str, seconds: float) -> list[tuple]:
    """One row per field the model wrote: its text as written (`value_raw`) and as normalized (`value_norm`, None when it could not be read)."""
    tree = raw if isinstance(raw, dict) else {}
    header = tree.get("header") if isinstance(tree.get("header"), dict) else {}
    totals = tree.get("totals") if isinstance(tree.get("totals"), dict) else {}
    rows = [(key, f"header.{name}", 0, _text(header.get(name)), _norm_header(fields, name), model_version, seconds) for name in HEADER_FIELDS if name in header]
    raw_lines = tree.get("lines") if isinstance(tree.get("lines"), (list, tuple)) else []
    for ordinal, (raw_line, line) in enumerate(zip(raw_lines, fields.lines, strict=False)):
        raw_line = raw_line if isinstance(raw_line, dict) else {}
        rows += [(key, f"line.{name}", ordinal, _text(raw_line.get(source)), _text(getattr(line, attribute)), model_version, seconds)
                 for name, source, attribute in LINE_FIELDS if source in raw_line]
    normal = {"neto": fields.neto, "iva_21": fields.iva if fields.iva_rate == 21 else None, "iva_105": fields.iva if fields.iva_rate is not None and fields.iva_rate != 21 else None, "total": fields.total}
    rows += [(key, f"totals.{name}", 0, _text(totals.get(name)), _text(normal[name]), model_version, seconds) for name in TOTAL_FIELDS if name in totals]
    return rows


def qr_row(key: str, qr: QrRead, seconds: float) -> tuple:
    p = qr.payload
    return (key, qr.reader, qr.status, qr.reason, *( (p.cuit, p.fecha.isoformat(), p.pto_vta, p.tipo_cmp, p.nro_cmp, str(p.importe), p.moneda, str(p.ctz), p.tipo_doc_rec, p.nro_doc_rec, p.tipo_cod_aut,
                                                      p.cod_aut) if p else (None,) * 12), seconds)


def _write_invoice(conn: sqlite3.Connection, run: RunMeta, key: str, decision: Decision, rows: list[tuple], qr: tuple | None, post: PostOutcome | None, post_seconds: float, total_seconds: float) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany("INSERT INTO extracted_fields (invoice_key, field, ordinal, value_raw, value_norm, model_version, seconds) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        if qr is not None:
            conn.execute("INSERT INTO qr_reads (invoice_key, reader, status, invalid_reason, cuit, fecha, pto_vta, tipo_cmp, nro_cmp, importe, moneda, ctz, doc_rec_tipo, doc_rec_nro, cod_aut_tipo, cod_aut, seconds) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", qr)
        conn.execute("INSERT INTO decisions (invoice_key, decision, first_reason, reasons, po_number, supplier_cuit, total, currency, run_key, seconds_total) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (key, decision.decision, decision.first_reason, len(decision.reasons), decision.po_number, decision.supplier_cuit, _text(decision.total), decision.currency, run.run_key, total_seconds))
        conn.executemany("INSERT INTO decision_reasons (invoice_key, reason, rank, detail_json) VALUES (?, ?, ?, ?)",
                         [(key, r.code, r.rank, json.dumps(r.detail, sort_keys=True, default=str)) for r in decision.reasons])
        if post is not None:
            conn.execute("INSERT INTO erp_posts (invoice_key, erp_name, status, readback_equal, seconds, run_key) VALUES (?, ?, ?, ?, ?, ?)",
                         (key, post.erp_name, post.status, None if post.readback_equal is None else int(post.readback_equal), post_seconds, run.run_key))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _check(conn: sqlite3.Connection, run: RunMeta, view: SealedConfig, spec: DeliverySpec, day: date) -> None:
    if spec.kind != "daily":
        raise ScoringError(f"{spec.delivery_id} no es una entrega diaria: solo una diaria emite facturas")
    if run.as_of != spec.as_of:
        raise ScoringError(f"el instante de la corrida ({_stamp(run.as_of)}) no es el de la entrega {spec.delivery_id} ({_stamp(spec.as_of)})")
    loaded = conn.execute("SELECT r.as_of FROM deliveries d JOIN pipeline_runs r ON r.run_key = d.run_key WHERE d.delivery_id = ? AND d.status = 'loaded' AND r.run_key = ?",
                          (spec.delivery_id, run.run_key)).fetchone()
    if loaded is None:
        raise ScoringError(f"la entrega {spec.delivery_id} no está cargada por la corrida {run.run_key}")
    if day > spec.day:
        raise ScoringError(f"el día {day.isoformat()} es un día después de la entrega {spec.delivery_id} ({spec.day.isoformat()}): lo que llega después no se decide hoy")
    if day <= view.calendar.launch_last_day:
        raise ScoringError(f"el día {day.isoformat()} no es posterior al lanzamiento ({view.calendar.launch_last_day.isoformat()})")


def run_day(conn: sqlite3.Connection, client: Any, extractor: Any, run: RunMeta, view: SealedConfig, spec: DeliverySpec, inbox: Path, *, day: date | None = None,
            invoice_keys: list[str] | None = None) -> DayResult:
    """Issue the invoices of `day` (the delivery's day by default) of the loaded daily delivery `spec`, in the order of their keys. `invoice_keys` is the whole day's (all of the delivery's invoices when
    it is None): the row of the day is written when they are all issued. See the module."""
    day = day or spec.day
    _check(conn, run, view, spec, day)
    rows = {key: (name, status, rule) for key, name, status, rule in conn.execute("SELECT invoice_key, file_name, status, rule_id FROM invoices WHERE delivery_id = ?", (spec.delivery_id,))}
    keys = sorted(rows if invoice_keys is None else invoice_keys)
    for key in keys:
        if key not in rows:
            raise ScoringError(f"la factura {key} no es de esta entrega ({spec.delivery_id})")
    done = {key for (key,) in conn.execute("SELECT invoice_key FROM decisions")}
    todo = [key for key in keys if key not in done]
    if todo and conn.execute("SELECT 1 FROM daily_ops WHERE date_key = ?", (int(day.strftime("%Y%m%d")),)).fetchone() is not None:
        raise ScoringError(f"el día {day.isoformat()} ya tiene su fila de operación con otras facturas: `invoice_keys` es el día completo y no se completa después")
    counts = {"quarantined": 0, "auto_post": 0, "review": 0, "posted": 0, "post_failed": 0}
    seconds: list[float] = []
    for key in todo:
        name, status, rule = rows[key]
        started = time.perf_counter()
        qr_values = post = None
        field_values: list[tuple] = []
        post_seconds = 0.0
        if status == "quarantined":
            decision = decide(None, None, None, view, file_rejected=rule)
            counts["quarantined"] += 1
        else:
            page = load_page(Path(inbox) / name, view)
            read = time.perf_counter()
            raw = extractor.extract(page)
            fields = normalize(raw)
            read_seconds = time.perf_counter() - read
            marked = time.perf_counter()
            qr = read_qr(page, view)
            qr_seconds = time.perf_counter() - marked
            erp = erp_view(client, fields, qr, day, conn, invoice_key=key)       # a failed read is raised: nothing of this invoice is written
            decision = decide(fields, qr, erp, view)
            field_values = field_rows(key, raw, fields, getattr(extractor, "model_version", "unknown"), read_seconds)
            qr_values = qr_row(key, qr, qr_seconds)
            if decision.decision == "auto_post":
                posted = time.perf_counter()
                document = invoice_doc(fields, qr.payload, erp, erp.supplier_by_cuit[qr.payload.cuit])
                post = client.post_invoice(document)
                post_seconds = time.perf_counter() - posted
        total = time.perf_counter() - started
        _write_invoice(conn, run, key, decision, field_values, qr_values, post, post_seconds, total)
        seconds.append(total)
        counts[decision.decision] += 1
        if post is not None:
            counts["posted" if post.status in ("posted", "already_posted") else "post_failed"] += 1
    if todo:
        _write_day(conn, run, day, spec, seconds_of=[r[0] for r in conn.execute("SELECT seconds_total FROM decisions WHERE invoice_key IN ({})".format(",".join("?" * len(keys))), keys)], keys=keys)
    result = DayResult(spec.delivery_id, day, bool(todo), len(todo), counts["quarantined"], counts["auto_post"], counts["review"], counts["posted"], counts["post_failed"], tuple(seconds))
    logger.info("daily %s %s: issued %d of %d invoices (%d auto_post, %d review, %d posted, %d post_failed)", spec.delivery_id, day.isoformat(), len(todo), len(keys),
                counts["auto_post"], counts["review"], counts["posted"], counts["post_failed"])
    return result


def _write_day(conn: sqlite3.Connection, run: RunMeta, day: date, spec: DeliverySpec, *, seconds_of: list[float], keys: list[str]) -> None:
    """The row of the day, counted over every invoice of the day whoever issued it (a run that resumed a day counts what the first one issued too). Written once."""
    marks = ",".join("?" * len(keys))
    date_key = int(day.strftime("%Y%m%d"))
    quarantined = conn.execute(f"SELECT count(*) FROM invoices WHERE status = 'quarantined' AND invoice_key IN ({marks})", keys).fetchone()[0]
    auto_post, review = (conn.execute(f"SELECT count(*) FROM decisions WHERE decision = ? AND invoice_key IN ({marks})", (kind, *keys)).fetchone()[0] for kind in ("auto_post", "review"))
    posted = conn.execute(f"SELECT count(*) FROM erp_posts WHERE status IN ('posted', 'already_posted') AND invoice_key IN ({marks})", keys).fetchone()[0]
    failed = conn.execute(f"SELECT count(*) FROM erp_posts WHERE status = 'post_failed' AND invoice_key IN ({marks})", keys).fetchone()[0]
    ordered = sorted(seconds_of)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))] if ordered else None
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO daily_ops (date_key, invoices, accepted, quarantined, auto_post, review, posted, post_failed, seconds_mean, seconds_p95, run_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (date_key, len(keys), len(keys) - quarantined, quarantined, auto_post, review, posted, failed, statistics.fmean(seconds_of) if seconds_of else None, p95, run.run_key))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


__all__ = ["DayResult", "ScoringError", "field_rows", "qr_row", "run_day"]
