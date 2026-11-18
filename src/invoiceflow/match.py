"""The two- and three-way match: why an invoice may not be posted on its own (RF12; AC13; plan section 11; decisions D14, D15, D45, D48).

``reasons`` returns, in the order of the plan's table, every reason that applies to an invoice; ``decide.decide`` posts it only when there is none. The inputs are three plain objects:

* ``fields`` (``normalize.InvoiceFields``), what the model read from the page;
* ``qr`` (``qr.QrRead``), what the QR said, validated; with ``[match] require_qr`` (the default) a page whose QR was not read or does not validate is a reason of its own and nothing but the
  reasons already listed can send it to ``auto_post``; without it, the match runs on what is printed;
* ``view`` (``ErpView``), what the ERP knew at the day of the delivery: the order the page cites, the receipts of that order dated on or before the day, what was invoiced against it before
  and the vouchers already known. ``reasons`` does not trust the builder of the view for the future: a receipt dated after ``view.day`` is not received stock, whoever handed it over (AC11).

Everything is ``Decimal``. «Inside the tolerance» includes the border. The details of a reason are keys, numbers and fixed codes, never a text of the document (they become
``decision_reasons.detail_json``). A reason whose rule needs the order is not evaluated when there is no order (``no_po`` says why); the arithmetic of the page and the QR do not need one.

``erp_view`` (the part that asks the ERP) is built where the client is (``erp/client.py``'s user, U13); this module is pure.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from invoiceflow.config import SealedConfig
from invoiceflow.normalize import InvoiceFields, LineFields
from invoiceflow.qr import QrRead

REASONS = ("file_rejected", "qr_not_read", "qr_invalid", "extraction_incomplete", "unsupported_type", "qr_print_mismatch", "duplicate", "no_po", "supplier_mismatch",
           "currency_mismatch", "date_out_of_range", "line_not_on_po", "line_arithmetic", "total_arithmetic", "price_out_of_tolerance", "qty_over_receipt", "qty_over_order",
           "post_failed")
POST_STAGES = ("rejected", "read_back", "unreachable")
ISO_CURRENCY = {"PES": "ARS", "DOL": "USD"}      # ARCA's codes in the QR; an ERP says ISO


@dataclass(frozen=True)
class Reason:
    code: str
    rank: int                                    # 1-based position in ``REASONS``
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PoLine:
    item_code: str
    description: str
    qty: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class PurchaseOrder:
    number: str
    supplier: str                                # the ERP's id of the supplier
    currency: str
    date: date
    lines: tuple[PoLine, ...]


@dataclass(frozen=True)
class ReceiptLine:
    item_code: str
    qty: Decimal


@dataclass(frozen=True)
class Receipt:
    number: str
    order: str
    date: date
    lines: tuple[ReceiptLine, ...]


@dataclass(frozen=True)
class ErpView:
    po: PurchaseOrder | None                     # the order the page cites, as the ERP has it
    receipts_until_day: tuple[Receipt, ...]      # the receipts of that order dated on or before ``day``
    invoiced_before: Mapping[str, Decimal]       # item code -> quantity already invoiced (posted) against the order
    known_fiscal_keys: Set[tuple[str, int, int, int]]   # (CUIT, point of sale, type, number) of every voucher already in the warehouse or the ERP
    supplier_by_cuit: Mapping[str, str]          # CUIT -> the ERP's id of that supplier
    day: date                                    # the day of the delivery: what the match may know ends here


def _reason(code: str, **detail: Any) -> Reason:
    return Reason(code, REASONS.index(code) + 1, detail)


def post_failed(stage: str) -> Reason:
    """The last reason, added by ``post`` when the ERP refuses the load or the read-back differs (the match does not know it)."""
    if stage not in POST_STAGES:
        raise ValueError(f"post_failed: la etapa {stage!r} no es una de {', '.join(POST_STAGES)}")
    return _reason("post_failed", stage=stage)


def _d(value: float) -> Decimal:
    return Decimal(repr(float(value)))


def _iso(code: str | None) -> str | None:
    return ISO_CURRENCY.get(code, code) if code is not None else None


def _words(text: str | None) -> str:
    return " ".join(text.split()).casefold() if text else ""


def _po_line(po: PurchaseOrder, line: LineFields) -> PoLine | None:
    """The line of the order a line of the invoice is: by code when it has one, by normalized description when it has none."""
    if line.codigo:
        wanted = line.codigo.strip().casefold()
        return next((candidate for candidate in po.lines if candidate.item_code.casefold() == wanted), None)
    wanted = _words(line.descripcion)
    return next((candidate for candidate in po.lines if wanted and _words(candidate.description) == wanted), None) if wanted else None


def _mismatches(fields: InvoiceFields, payload: Any, tolerance: Decimal) -> list[str]:
    names = []
    if fields.cuit_emisor != payload.cuit:
        names.append("cuit")
    if fields.pto_vta != payload.pto_vta:
        names.append("pto_vta")
    if fields.nro != payload.nro_cmp:
        names.append("nro")
    if fields.fecha != payload.fecha:
        names.append("fecha")
    if fields.total is None or abs(fields.total - payload.importe) > tolerance:
        names.append("total")
    return names


def _total_parts(fields: InvoiceFields, rounding: Decimal) -> list[str]:
    parts = []
    amounts = [line.importe for line in fields.lines]
    if fields.lines and fields.neto is not None and all(amount is not None for amount in amounts):
        if abs(sum(amounts, Decimal(0)) - fields.neto) > rounding * len(fields.lines):
            parts.append("sum_vs_net")
    if fields.neto is not None and fields.total is not None:
        if fields.iva is None:
            parts.append("iva_unreadable")
        elif abs(fields.neto + fields.iva - fields.total) > rounding:
            parts.append("net_plus_iva_vs_total")
    return parts


def _received(view: ErpView, po: PurchaseOrder) -> dict[str, Decimal]:
    stock: dict[str, Decimal] = defaultdict(Decimal)
    for receipt in view.receipts_until_day:
        if receipt.order == po.number and receipt.date <= view.day:
            for received in receipt.lines:
                stock[received.item_code] += received.qty
    return stock


def _by_item(fields: InvoiceFields, po: PurchaseOrder) -> tuple[dict[str, Decimal], dict[str, list[int]]]:
    """The quantity of each item of the order this invoice bills, and the lines that bill it."""
    quantities: dict[str, Decimal] = defaultdict(Decimal)
    lines: dict[str, list[int]] = defaultdict(list)
    for index, line in enumerate(fields.lines):
        match = _po_line(po, line)
        if match is not None and line.cantidad is not None:
            quantities[match.item_code] += line.cantidad
            lines[match.item_code].append(index)
    return quantities, lines


def reasons(fields: InvoiceFields | None, qr: QrRead | None, view: ErpView | None, cfg: SealedConfig, *, file_rejected: str | None = None) -> list[Reason]:
    """Every reason that applies, in the order of ``REASONS``. A file the intake quarantined (`file_rejected` is its rule, V12 to V15) has no page to read: its only reason is
    ``file_rejected``."""
    if file_rejected is not None:
        return [_reason("file_rejected", rule=file_rejected)]
    match = cfg.match
    out: list[Reason] = []
    payload = qr.payload if qr.status == "ok" else None
    if match.require_qr:
        if qr.status == "not_read":
            out.append(_reason("qr_not_read"))
        elif qr.status == "invalid":
            out.append(_reason("qr_invalid", reason=qr.reason))
    if fields.missing:
        out.append(_reason("extraction_incomplete", missing=list(fields.missing)))
    tipo = payload.tipo_cmp if payload else fields.tipo
    if tipo not in match.supported_types:     # a type nobody read (None) is not a supported one
        out.append(_reason("unsupported_type", tipo=tipo))
    if payload is not None:
        names = _mismatches(fields, payload, _d(match.total_qr_tolerance))
        if names:
            out.append(_reason("qr_print_mismatch", fields=names))
    key = ((payload.cuit, payload.pto_vta, payload.tipo_cmp, payload.nro_cmp) if payload else (fields.cuit_emisor, fields.pto_vta, fields.tipo, fields.nro))
    if key in view.known_fiscal_keys:
        out.append(_reason("duplicate"))
    cited = bool(fields.orden_compra)
    po = view.po if cited else None
    if po is None:
        out.append(_reason("no_po", cited=cited))
    rounding = _d(match.rounding_per_line)
    cuit = payload.cuit if payload else fields.cuit_emisor
    if po is not None:
        if view.supplier_by_cuit.get(cuit) != po.supplier:      # a CUIT the ERP does not know (or none) is nobody's
            out.append(_reason("supplier_mismatch"))
        currency = payload.moneda if payload else fields.moneda
        if currency is None or _iso(currency) != _iso(po.currency):
            out.append(_reason("currency_mismatch", invoice=currency, order=po.currency))
        fecha = payload.fecha if payload else fields.fecha
        if fecha is None:
            out.append(_reason("date_out_of_range", side="unreadable"))
        elif fecha < po.date:
            out.append(_reason("date_out_of_range", side="before_order"))
        elif fecha > view.day:
            out.append(_reason("date_out_of_range", side="after_day"))
        absent = [index for index, line in enumerate(fields.lines) if _po_line(po, line) is None]
        if absent:
            out.append(_reason("line_not_on_po", lines=absent))
    off = [index for index, line in enumerate(fields.lines)
           if None not in (line.cantidad, line.precio_unitario, line.importe) and abs(line.cantidad * line.precio_unitario - line.importe) > rounding]
    if off:
        out.append(_reason("line_arithmetic", lines=off))
    parts = _total_parts(fields, rounding)
    if parts:
        out.append(_reason("total_arithmetic", parts=parts))
    if po is None:
        return out
    tolerance = _d(match.price_tolerance_pct)
    dear = []
    for index, line in enumerate(fields.lines):
        ordered = _po_line(po, line)
        if ordered is not None and line.precio_unitario is not None and abs(line.precio_unitario - ordered.unit_price) * 100 > tolerance * ordered.unit_price:
            dear.append(index)
    if dear:
        out.append(_reason("price_out_of_tolerance", lines=dear))
    billed, rows = _by_item(fields, po)
    stock = _received(view, po)
    ordered_quantity: dict[str, Decimal] = defaultdict(Decimal)
    for ordered in po.lines:
        ordered_quantity[ordered.item_code] += ordered.qty
    allowance = 1 + _d(match.qty_over_receipt)
    over_receipt = sorted(i for item, qty in billed.items() if view.invoiced_before.get(item, Decimal(0)) + qty > stock.get(item, Decimal(0)) * allowance for i in rows[item])
    if over_receipt:
        out.append(_reason("qty_over_receipt", lines=over_receipt))
    over_order = sorted(i for item, qty in billed.items() if view.invoiced_before.get(item, Decimal(0)) + qty > ordered_quantity[item] for i in rows[item])
    if over_order:
        out.append(_reason("qty_over_order", lines=over_order))
    return out
