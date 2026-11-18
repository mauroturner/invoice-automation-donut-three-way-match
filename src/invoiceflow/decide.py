"""The decision (RF13; AC14; plan section 11; decisions D06, D15): the one place that says ``auto_post`` or ``review``.

``decide`` is ``match.reasons`` and nothing more: ``auto_post`` when there is no reason, ``review`` with the ordered reasons otherwise. The baselines B1 to B3 call it with other inputs, so no
baseline can post what the flow's rule refuses. What it keeps besides the reasons is what the warehouse writes about a decision: the order, the supplier's CUIT, the total and the currency
it decided on, taken from the QR when there is one (the QR wins over the page, D06).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from invoiceflow.config import SealedConfig
from invoiceflow.match import ErpView, Reason, reasons
from invoiceflow.normalize import InvoiceFields
from invoiceflow.qr import QrRead

AUTO_POST, REVIEW = "auto_post", "review"


@dataclass(frozen=True)
class Decision:
    decision: str                       # "auto_post" or "review"
    reasons: tuple[Reason, ...]         # in the order of the plan's table; empty exactly when the decision is auto_post (and for the baseline B0)
    po_number: str | None
    supplier_cuit: str | None
    total: Decimal | None
    currency: str | None

    @property
    def first_reason(self) -> str | None:
        return self.reasons[0].code if self.reasons else None


def decide(fields: InvoiceFields | None, qr: QrRead | None, view: ErpView | None, cfg: SealedConfig, *, file_rejected: str | None = None) -> Decision:
    """The decision for one invoice. `file_rejected` is the rule (V12 to V15) that quarantined its file: there is no page to read, and the decision is review with that one reason."""
    found = tuple(reasons(fields, qr, view, cfg, file_rejected=file_rejected))
    if file_rejected is not None:
        return Decision(REVIEW, found, None, None, None, None)
    payload = qr.payload if qr.status == "ok" else None
    po_number = view.po.number if view.po is not None and fields.orden_compra else fields.orden_compra
    return Decision(REVIEW if found else AUTO_POST, found, po_number or None, payload.cuit if payload else fields.cuit_emisor,
                    payload.importe if payload else fields.total, payload.moneda if payload else fields.moneda)
