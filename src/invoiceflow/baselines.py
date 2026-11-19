"""The baselines (RF19; AC25; plan section 15; decisions D18, D48): what the flow is compared with, all through ``decide.decide``.

* **B0** sends everything to review: what not automating costs.
* **B1** reads the QR and the order and nothing else: the page's lines, its totals and its printed header are never looked at (only the order number the model read), and the receipts are not
  there. Its fields are the QR's own, so the checks that need the page agree by construction; what is left of ``decide`` is the QR (read, valid, a supported type), the voucher not known, the
  order's supplier and currency and the date. Its one rule of its own is the two-way match by the total: the QR total must be what is left of the order (its quantities less what was invoiced
  before, at its prices) at either VAT rate, to ``total_qr_tolerance``. That rule can only send to review what ``decide`` would have posted.
* **B2** is the unadjusted CORD checkpoint's tree taken to the invoice schema (``menu.nm``, ``cnt``, ``unitprice``, ``price`` to the lines, ``total.total_price`` to the total) and
  decided with the full rules; the checkpoint writes no order number, no code and no net, so little is posted (research: 0 of 30).
* **B3**, the oracle, is the true fields of each synthetic invoice with the true QR and an ERP view made from the master, in the order the invoices reach accounts payable. It is the proof that the rules say what the
  generator meant (M06). It takes the truth as a parameter: reading the truth file is ``evaluation``'s and this module's (``truth.read_truth`` allows both).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from invoiceflow.config import SealedConfig
from invoiceflow.decide import AUTO_POST, REVIEW, Decision, decide
from invoiceflow.match import ErpView, PoLine, PurchaseOrder, Reason, Receipt, ReceiptLine
from invoiceflow.normalize import InvoiceFields, LineFields, normalize
from invoiceflow.qr import QrInvalid, QrRead, parse_qr_text
from invoiceflow.synth.invoices import SPLITS, InvoiceTruth
from invoiceflow.synth.master import Master

CENT = Decimal("0.01")
IVA_RATES = (Decimal("21"), Decimal("10.5"))
TOTAL_VS_ORDER = "total_vs_order"           # B1's own reason, outside the plan's eighteen (rank 0)


def b0() -> Decision:
    """Everything to review."""
    return Decision(REVIEW, (), None, None, None, None)


def _left_of_the_order(po: PurchaseOrder, invoiced_before: Mapping[str, Decimal]) -> Decimal:
    """What is left to invoice of the order, net: each line's remaining quantity at its price, each rounded to the cent as an invoice line is."""
    total = Decimal(0)
    for line in po.lines:
        remaining = max(line.qty - invoiced_before.get(line.item_code, Decimal(0)), Decimal(0))
        total += (remaining * line.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
    return total


def _total_matches(importe: Decimal, po: PurchaseOrder, invoiced_before: Mapping[str, Decimal], tolerance: Decimal) -> bool:
    net = _left_of_the_order(po, invoiced_before)
    return any(abs(importe - (net + (net * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP))) <= tolerance for rate in IVA_RATES)


def b1(fields: InvoiceFields, qr: QrRead, view: ErpView, cfg: SealedConfig) -> Decision:
    """The decision of the baseline that sees the QR and the order (see the module's text). From `fields` it reads the order number only, and from `view` neither the receipts nor anything
    of the page."""
    payload = qr.payload if qr.status == "ok" else None
    order_number = fields.orden_compra
    if payload is None:
        reduced = InvoiceFields(orden_compra=order_number, missing=())
    else:
        reduced = InvoiceFields(tipo=payload.tipo_cmp, pto_vta=payload.pto_vta, nro=payload.nro_cmp, fecha=payload.fecha, cuit_emisor=payload.cuit, moneda=payload.moneda,
                                orden_compra=order_number, neto=payload.importe, iva=Decimal(0), total=payload.importe, lines=(), missing=())
    seen = ErpView(po=view.po, receipts_until_day=(), invoiced_before=view.invoiced_before, known_fiscal_keys=view.known_fiscal_keys, supplier_by_cuit=view.supplier_by_cuit,
                   day=view.day)
    decision = decide(reduced, qr, seen, cfg)
    if decision.decision == REVIEW or payload is None or seen.po is None:
        return decision
    if _total_matches(payload.importe, seen.po, seen.invoiced_before, Decimal(repr(float(cfg.match.total_qr_tolerance)))):
        return decision
    return Decision(REVIEW, (Reason(TOTAL_VS_ORDER, 0, {}),), decision.po_number, decision.supplier_cuit, decision.total, decision.currency)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def cord_tree_to_raw(tree: Any) -> dict[str, Any]:
    """The tree of CORD's schema as the invoice schema's raw tree: ``menu`` (a list, or one object) to ``lines`` (``nm`` to ``descripcion``, ``cnt`` to ``cantidad``, ``unitprice`` to
    ``precio_unitario``, ``price`` to ``importe``) and ``total.total_price`` to ``totals.total``; only texts are taken, and nothing else of CORD's tree is."""
    tree = tree if isinstance(tree, dict) else {}
    menu = tree.get("menu")
    items = [menu] if isinstance(menu, dict) else (menu if isinstance(menu, list) else [])
    names = (("nm", "descripcion"), ("cnt", "cantidad"), ("unitprice", "precio_unitario"), ("price", "importe"))
    lines = [{target: item[source] for source, target in names if isinstance(item, dict) and _text(item.get(source)) is not None} for item in items]
    total = tree.get("total")
    price = _text(total.get("total_price")) if isinstance(total, dict) else None
    return {"header": {}, "lines": lines, "totals": {"total": price} if price is not None else {}}


def b2(cord_tree: Any, qr: QrRead, view: ErpView, cfg: SealedConfig) -> Decision:
    """The unadjusted checkpoint's reading of the page, taken to the invoice schema, decided with the full rules."""
    return decide(normalize(cord_tree_to_raw(cord_tree)), qr, view, cfg)


# --- B3: the oracle -------------------------------------------------------------------------------------------------------------------------


def truth_fields(truth: InvoiceTruth) -> InvoiceFields:
    """The true fields of an invoice as the match reads them: typed, complete, the order as the page cites it."""
    h, t = truth.header, truth.totals
    lines = tuple(LineFields(line.code, line.description, line.qty, line.unit, line.unit_price, line.amount) for line in truth.lines)
    return InvoiceFields(tipo=h.tipo, pto_vta=h.pto_vta, nro=h.nro, fecha=h.fecha, cuit_emisor=h.cuit_emisor, razon_social=h.razon_social, cuit_receptor=h.cuit_receptor,
                         orden_compra=h.orden_compra, moneda=h.moneda, cae=h.cae, lines=lines, neto=t.neto, iva=t.iva, iva_rate=t.iva_rate, total=t.total, missing=())


def truth_qr(truth: InvoiceTruth, view: SealedConfig) -> QrRead:
    """What a reader that never fails would return for the page: its QR text, validated, or nothing when the page has none."""
    if truth.qr_text is None:
        return QrRead(None, "not_read", None, None)
    try:
        return QrRead("truth", "ok", parse_qr_text(truth.qr_text, view), None)
    except QrInvalid as error:
        return QrRead("truth", "invalid", None, error.reason)


def _orders(master: Master) -> dict[str, PurchaseOrder]:
    return {po.po_number: PurchaseOrder(po.po_number, po.supplier_key, po.currency, po.po_date,
                                        tuple(PoLine(line.item_code, line.description_es, line.qty, line.unit_price) for line in po.lines)) for po in master.orders}


def _receipts(master: Master) -> dict[str, list[Receipt]]:
    by_order: dict[str, list[Receipt]] = defaultdict(list)
    for receipt in master.receipts:
        by_order[receipt.po_number].append(Receipt(receipt.receipt_number, receipt.po_number, receipt.receipt_date,
                                                   tuple(ReceiptLine(line.item_code, line.qty) for line in receipt.lines)))
    return by_order


def _arrival(truth: InvoiceTruth) -> tuple:
    return truth.received_on, SPLITS.index(truth.split), int(truth.invoice_key.rsplit("-", 1)[1])


def replay(view: SealedConfig, master: Master, truths: Iterable[InvoiceTruth], decide_one: Callable[[InvoiceTruth, ErpView], Decision], *,
           order_of: Callable[[InvoiceTruth], str | None] | None = None) -> dict[str, Decision]:
    """The decision of every invoice in `truths` (key -> ``Decision``) given by ``decide_one(truth, erp_view)`` over an ERP view made from `master` for the day it reached accounts payable. The
    invoices are processed in the order they arrived (``received_on``, partition, ordinal), so what an earlier one posted (the quantity invoiced, the voucher) is what a later one finds: each
    baseline has its own world, in which only what it posted counts.

    `order_of(truth)` is the order number the baseline cites, the one whose order, receipts and invoiced quantities the view holds and on which what it posts is counted. By default it is the
    page's (the truth's), which is what the oracle reads; B1 cites what the model read (D48, D60)."""
    cite = order_of if order_of is not None else (lambda truth: truth.header.orden_compra)
    orders, receipts = _orders(master), _receipts(master)
    supplier_by_cuit = {supplier.cuit: supplier.supplier_key for supplier in master.suppliers}
    invoiced: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    known: set[tuple[str, int, int, int]] = set()
    decisions: dict[str, Decision] = {}
    for truth in sorted(truths, key=_arrival):
        fields, qr = truth_fields(truth), truth_qr(truth, view)
        cited = cite(truth)
        po = orders.get(cited) if cited else None
        day = truth.received_on
        erp = ErpView(po=po, receipts_until_day=tuple(r for r in receipts.get(cited, ()) if r.date <= day) if po else (), invoiced_before=dict(invoiced[cited]) if po else {},
                      known_fiscal_keys=known, supplier_by_cuit=supplier_by_cuit, day=day)
        decision = decide_one(truth, erp)
        decisions[truth.invoice_key] = decision
        payload = qr.payload
        key = (payload.cuit, payload.pto_vta, payload.tipo_cmp, payload.nro_cmp) if payload else (fields.cuit_emisor, fields.pto_vta, fields.tipo, fields.nro)
        known.add(key)
        if decision.decision == AUTO_POST and po is not None:
            for line in truth.lines:
                invoiced[cited][line.code] += line.qty
    return decisions


def oracle(view: SealedConfig, master: Master, truths: Iterable[InvoiceTruth]) -> dict[str, Decision]:
    """B3: the decision of every invoice in `truths` (key -> ``Decision``) from its true fields, its true QR and an ERP view made from `master` for the day it reached accounts payable (``replay``)."""
    return replay(view, master, truths, lambda truth, erp: decide(truth_fields(truth), truth_qr(truth, view), erp, view))
