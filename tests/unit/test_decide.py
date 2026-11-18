"""The decision (U11; AC14; plan section 11, decisions D06, D15): ``auto_post`` only when there is no reason and, with ``[match] require_qr``, a QR that was read and validates. It is the one
function that says it; the baselines call it with other inputs (``tests/unit/test_baselines.py``)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from invoiceflow.decide import Decision, decide
from invoiceflow.match import REASONS, reasons
from tests.fixtures.match_cases import NOT_READ, ORDER_NO, SUPPLIER, codes, good, invalid, invoice, line, make_view, order, qr_ok, with_match

D = Decimal


def test_no_auto_post_without_a_valid_qr(view):
    """AC14: a page whose QR was not read, or whose text does not validate, goes to review whatever else closes, with the default ``require_qr = true``."""
    fields, _, erp = good()
    assert view.match.require_qr is True
    for qr, reason in ((NOT_READ, "qr_not_read"), (invalid("host"), "qr_invalid"), (invalid("multiple_qr"), "qr_invalid")):
        decision = decide(fields, qr, erp, view)
        assert decision.decision == "review", reason
        assert decision.first_reason == reason and decision.reasons[0].code == reason
        assert codes(decision.reasons) == [reason], "nothing else is wrong: it is the one reason"


def test_a_good_invoice_with_a_valid_qr_is_posted(view):
    fields, qr, erp = good()
    decision = decide(fields, qr, erp, view)
    assert isinstance(decision, Decision) and decision.decision == "auto_post" and decision.reasons == () and decision.first_reason is None


def test_one_reason_is_enough_to_send_it_to_review_and_the_first_one_of_the_table_is_the_first(view):
    fields = invoice((line("A1", "11", "100.00"),))
    qr = qr_ok(fields)
    decision = decide(fields, qr, make_view(known_fiscal_keys=frozenset({("30000000007", 1, 1, 10)})), view)
    assert decision.decision == "review" and decision.first_reason == "duplicate"
    assert codes(decision.reasons) == ["duplicate", "qty_over_receipt", "qty_over_order"]
    assert [r.rank for r in decision.reasons] == [REASONS.index(c) + 1 for c in codes(decision.reasons)]


def test_the_decision_is_the_reasons_the_match_gives_and_nothing_else(view):
    fields, qr, erp = good()
    for changed in (fields, dataclasses.replace(fields, nro=99), dataclasses.replace(fields, fecha=None), dataclasses.replace(fields, missing=("lines",))):
        assert decide(changed, qr, erp, view).reasons == tuple(reasons(changed, qr, erp, view))
        assert (decide(changed, qr, erp, view).decision == "auto_post") is (reasons(changed, qr, erp, view) == [])


def test_without_require_qr_a_missing_qr_is_not_a_reason_and_the_printed_values_decide(view):
    """``[match] require_qr = false`` is for a deployment that accepts pages with no QR: the match then runs on what is printed. The default never does."""
    relaxed = with_match(view, require_qr=False)
    fields, _, erp = good()
    assert decide(fields, NOT_READ, erp, relaxed).decision == "auto_post"
    assert decide(fields, invalid(), erp, relaxed).decision == "auto_post"
    assert decide(fields, NOT_READ, erp, view).decision == "review"
    other_supplier = make_view(po=order(supplier="SUP-02"))
    assert decide(fields, NOT_READ, other_supplier, relaxed).first_reason == "supplier_mismatch", "the printed CUIT is the supplier when there is no QR"
    unsupported = dataclasses.replace(fields, tipo=3)
    assert decide(unsupported, NOT_READ, erp, relaxed).first_reason == "unsupported_type"
    assert decide(dataclasses.replace(fields, tipo=None), NOT_READ, erp, relaxed).first_reason == "unsupported_type", "a type nobody read is not a supported one"


def test_a_rejected_file_is_review_with_file_rejected_and_nothing_else(view):
    decision = decide(None, None, None, view, file_rejected="V15")
    assert decision.decision == "review" and decision.first_reason == "file_rejected" and codes(decision.reasons) == ["file_rejected"]
    assert decision.reasons[0].detail == {"rule": "V15"}
    assert (decision.po_number, decision.supplier_cuit, decision.total, decision.currency) == (None, None, None, None)


def test_the_decision_keeps_the_order_the_supplier_the_total_and_the_currency_it_decided_on(view):
    fields, qr, erp = good()
    decision = decide(fields, qr, erp, view)
    assert (decision.po_number, decision.supplier_cuit, decision.total, decision.currency) == (ORDER_NO, "30000000007", fields.total, "PES")
    tampered = qr_ok(fields, importe=fields.total + D("9.00"), cuit="30000000015", moneda="DOL")
    again = decide(fields, tampered, erp, view)
    assert (again.supplier_cuit, again.total, again.currency) == ("30000000015", fields.total + D("9.00"), "DOL"), "the QR wins over the page (D06)"
    no_order = decide(dataclasses.replace(fields, orden_compra="OC-2026-000009"), qr, make_view(po=None), view)
    assert no_order.po_number == "OC-2026-000009", "an order the ERP does not have is still the one the page cited"


def test_deciding_twice_gives_the_same_decision_and_changes_nothing(view):
    fields, qr, erp = good()
    assert decide(fields, qr, erp, view) == decide(fields, qr, erp, view)
    assert SUPPLIER in erp.supplier_by_cuit.values()
