"""Builders of a good invoice and its ERP view for the tests of the match, the decision and the baselines (U11): one line of ten units at 100.00 of an order that was received whole the day
before, a QR that says what the page says. Each test changes one thing with ``dataclasses.replace`` and says which reason that must give."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from invoiceflow.match import ErpView, PoLine, PurchaseOrder, Receipt, ReceiptLine
from invoiceflow.normalize import InvoiceFields, LineFields
from invoiceflow.qr import QrPayload, QrRead

DAY = date(2026, 10, 5)
CUIT = "30000000007"
SUPPLIER = "SUP-01"
OTHER_SUPPLIER = "SUP-02"
OTHER_CUIT = "30000000015"
ORDER_NO = "OC-2026-000001"
D = Decimal


def line(code: str | None = "A1", qty: str = "10", price: str = "100.00", amount: str | None = None, description: str | None = "Tornillo hexagonal") -> LineFields:
    quantity, unit_price = D(qty), D(price)
    return LineFields(code, description, quantity, "u", unit_price, D(amount) if amount is not None else (quantity * unit_price).quantize(D("0.01")))


def invoice(lines: tuple[LineFields, ...] | None = None, **changes) -> InvoiceFields:
    """A good invoice: its totals follow its lines (21 % IVA) unless a change says otherwise."""
    lines = (line(),) if lines is None else lines
    neto = sum((item.importe for item in lines), D(0))
    iva = (neto * D("0.21")).quantize(D("0.01"))
    values = dict(tipo=1, pto_vta=1, nro=10, fecha=DAY, cuit_emisor=CUIT, razon_social="Proveedor Uno (ficticio) S.A.", cuit_receptor="30000000023", orden_compra=ORDER_NO,
                  moneda="PES", cae="12345678901234", lines=lines, neto=neto, iva=iva, iva_rate=D("21"), total=neto + iva, missing=())
    values.update(changes)
    return InvoiceFields(**values)


def payload(fields: InvoiceFields | None = None, **changes) -> QrPayload:
    fields = fields or invoice()
    values = dict(ver=1, fecha=fields.fecha, cuit=fields.cuit_emisor, pto_vta=fields.pto_vta, tipo_cmp=fields.tipo, nro_cmp=fields.nro, importe=fields.total, moneda="PES", ctz=D(1),
                  tipo_doc_rec=80, nro_doc_rec=30000000023, tipo_cod_aut="E", cod_aut=12345678901234)
    values.update(changes)
    return QrPayload(**values)


def qr_ok(fields: InvoiceFields | None = None, **changes) -> QrRead:
    return QrRead("pyzbar", "ok", payload(fields, **changes), None)


NOT_READ = QrRead(None, "not_read", None, None)


def invalid(reason: str = "bad_base64") -> QrRead:
    return QrRead("pyzbar", "invalid", None, reason)


def order(lines: tuple[PoLine, ...] | None = None, **changes) -> PurchaseOrder:
    lines = (PoLine("A1", "Tornillo hexagonal", D("10"), D("100.00")),) if lines is None else lines
    values = dict(number=ORDER_NO, supplier=SUPPLIER, currency="PES", date=date(2026, 9, 20), lines=lines)
    values.update(changes)
    return PurchaseOrder(**values)


def receipt(qty: str = "10", day: date = date(2026, 10, 4), code: str = "A1", number: str = "REM-000001") -> Receipt:
    return Receipt(number, ORDER_NO, day, (ReceiptLine(code, D(qty)),))


def make_view(**changes) -> ErpView:
    values = dict(po=order(), receipts_until_day=(receipt(),), invoiced_before={}, known_fiscal_keys=frozenset(), supplier_by_cuit={CUIT: SUPPLIER, OTHER_CUIT: OTHER_SUPPLIER}, day=DAY)
    values.update(changes)
    return ErpView(**values)


def good():
    fields = invoice()
    return fields, qr_ok(fields), make_view()


def codes(reasons) -> list[str]:
    return [reason.code for reason in reasons]


def with_match(sealed, **changes):
    """The sealed view with other ``[match]`` values."""
    return dataclasses.replace(sealed, match=dataclasses.replace(sealed.match, **changes))
