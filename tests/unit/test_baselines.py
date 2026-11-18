"""The baselines (U11; AC25 in part; plan section 15, decision D18, D48): B0 sends everything to review; B1 reads the QR and the order and nothing else (no lines, no receipts); B2 is the
unadjusted CORD checkpoint's output taken to the invoice schema; B3 (the oracle, in ``tests/integration/test_baselines.py``) has the true fields. B1-B3 are the same ``decide`` with other
inputs: a baseline can only send to review something that the full flow would have posted, never post something that ``decide`` refuses."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from invoiceflow.baselines import b0, b1, b2, cord_tree_to_raw
from invoiceflow.decide import decide
from invoiceflow.normalize import normalize
from tests.fixtures.match_cases import DAY, NOT_READ, codes, good, invalid, invoice, line, make_view, order, qr_ok

D = Decimal


class Spy:
    """Records every attribute read from the object it wraps."""

    def __init__(self, wrapped, allowed=None):
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_allowed", allowed)
        object.__setattr__(self, "reads", [])

    def __getattr__(self, name):
        self.reads.append(name)
        return getattr(self._wrapped, name)


def test_b1_never_reads_lines_or_receipts(view):
    """AC25: the baseline that sees only the QR and the order does not even look at the page's lines, its totals, nor at the receipts: a spy on each says which attributes were read."""
    fields, qr, erp = good()
    spy_fields, spy_view = Spy(fields), Spy(erp)
    decision = b1(spy_fields, qr, spy_view, view)
    assert decision.decision == "auto_post"
    assert set(spy_fields.reads) <= {"orden_compra"}, f"B1 read {sorted(set(spy_fields.reads))} of the page"
    assert "receipts_until_day" not in spy_view.reads and "lines" not in spy_fields.reads
    assert set(spy_view.reads) <= {"po", "invoiced_before", "known_fiscal_keys", "supplier_by_cuit", "day"}


def test_b1_does_not_change_when_the_lines_or_the_receipts_change(view):
    fields, qr, erp = good()
    base = b1(fields, qr, erp, view)
    other_lines = dataclasses.replace(fields, lines=(line("ZZ", "999", "1.00"),), missing=("lines[0].cantidad",), neto=None)
    no_receipts = dataclasses.replace(erp, receipts_until_day=())
    assert b1(other_lines, qr, no_receipts, view) == base


def test_b1_cannot_see_what_only_the_receipts_or_the_lines_show(view):
    """What makes it a weaker baseline: an invoice for more than was received, a price that is off, a line that is not on the order are all posted when the QR total is the order's."""
    fields = invoice((line("A1", "10", "100.00"),))
    qr = qr_ok(fields)
    assert decide(fields, qr, make_view(receipts_until_day=()), view).decision == "review"
    assert b1(fields, qr, make_view(receipts_until_day=()), view).decision == "auto_post"
    by_code = invoice((line("ZZ", "10", "100.00"),))
    assert decide(by_code, qr_ok(by_code), make_view(), view).first_reason == "line_not_on_po"
    assert b1(by_code, qr_ok(by_code), make_view(), view).decision == "auto_post"


def test_b1_compares_the_qr_total_with_what_is_left_of_the_order_at_either_iva_rate(view):
    fields, _, erp = good()
    net = D("1000.00")
    for rate_total, ok in ((D("1210.00"), True), (D("1105.00"), True), (D("1210.01"), True), (D("1210.02"), False), (D("1000.00"), False), (D("1300.00"), False)):
        assert (b1(fields, qr_ok(fields, importe=rate_total), erp, view).decision == "auto_post") is ok, rate_total
    partly = make_view(invoiced_before={"A1": D("4")})
    assert b1(fields, qr_ok(fields, importe=D("726.00")), partly, view).decision == "auto_post", "6 of 10 left: 600 + 21 % = 726"
    assert b1(fields, qr_ok(fields, importe=D("1210.00")), partly, view).decision == "review"


def test_b1_reviews_what_decide_reviews_for_the_reasons_that_only_need_the_qr_and_the_order(view):
    fields, qr, erp = good()
    cases = {
        "qr_not_read": (fields, NOT_READ, erp), "qr_invalid": (fields, invalid(), erp), "no_po (none cited)": (dataclasses.replace(fields, orden_compra=None), qr, erp),
        "no_po (not in the ERP)": (fields, qr, make_view(po=None)), "supplier_mismatch": (fields, qr, make_view(po=order(supplier="SUP-02"))),
        "currency_mismatch": (fields, qr_ok(fields, moneda="DOL"), erp), "date_out_of_range": (fields, qr_ok(fields, fecha=DAY.replace(year=2025)), erp),
        "duplicate": (fields, qr, make_view(known_fiscal_keys=frozenset({("30000000007", 1, 1, 10)}))), "unsupported_type": (fields, qr_ok(fields, tipo_cmp=3), erp),
    }
    for name, (f, q, v) in cases.items():
        assert b1(f, q, v, view).decision == "review", name


def test_b1_trusts_the_qr_for_the_header_so_a_page_that_disagrees_with_its_qr_is_posted(view):
    fields, qr, erp = good()
    misprinted = dataclasses.replace(fields, nro=11, total=fields.total + D("50.00"), cuit_emisor="30000000015")
    assert decide(misprinted, qr, erp, view).first_reason == "qr_print_mismatch"
    assert b1(misprinted, qr, erp, view).decision == "auto_post"


def test_b1_with_no_order_number_from_the_model_is_review(view):
    fields, qr, erp = good()
    assert b1(dataclasses.replace(fields, orden_compra=None), qr, erp, view).decision == "review"
    assert b1(dataclasses.replace(fields, orden_compra=""), qr, erp, view).decision == "review"


def test_b0_sends_everything_to_review():
    decision = b0()
    assert decision.decision == "review" and decision.reasons == () and decision.first_reason is None


# --- B2: the unadjusted checkpoint's output taken to the invoice schema -----------------------------------------------------------------------


def test_the_cord_schema_is_taken_to_the_invoice_schema_by_the_documented_fields_only():
    tree = {"menu": [{"nm": "Tornillo", "cnt": "2", "unitprice": "10,00", "price": "20,00"}, {"nm": "Tuerca", "cnt": "1", "price": "5,50"}],
            "sub_total": {"subtotal_price": "25,50"}, "total": {"total_price": "30,00"}}
    raw = cord_tree_to_raw(tree)
    assert raw == {"header": {}, "lines": [{"descripcion": "Tornillo", "cantidad": "2", "precio_unitario": "10,00", "importe": "20,00"},
                                           {"descripcion": "Tuerca", "cantidad": "1", "importe": "5,50"}], "totals": {"total": "30,00"}}
    fields = normalize(raw)
    assert fields.total == D("30.00") and fields.neto is None and len(fields.lines) == 2 and fields.lines[0].cantidad == D("2") and fields.lines[0].codigo is None
    assert "totals.neto" in fields.missing and "lines[1].precio_unitario" in fields.missing


def test_the_cord_menu_as_one_object_or_missing_and_a_total_that_is_a_list_or_a_text(view):
    assert cord_tree_to_raw({"menu": {"nm": "Solo", "cnt": "1", "unitprice": "1,00", "price": "1,00"}})["lines"] == [{"descripcion": "Solo", "cantidad": "1", "precio_unitario": "1,00", "importe": "1,00"}]
    assert cord_tree_to_raw({})["lines"] == [] and cord_tree_to_raw({"menu": "x", "total": "y"}) == {"header": {}, "lines": [], "totals": {}}
    assert cord_tree_to_raw({"total": {"total_price": ["1,00", "2,00"]}})["totals"] == {}, "a total that is not one text is not read"
    assert cord_tree_to_raw({"menu": [{"nm": 5, "cnt": None}]})["lines"] == [{}], "only texts are taken"
    assert cord_tree_to_raw(None) == {"header": {}, "lines": [], "totals": {}}


def test_b2_is_decide_over_those_fields_and_reviews_what_the_checkpoint_does_not_give(view):
    """The unadjusted checkpoint writes no order, no code, no net and no VAT: with the full rules nothing is posted (research: 0 of 30 invoices with everything right)."""
    fields, qr, erp = good()
    tree = {"menu": [{"nm": "Tornillo hexagonal", "cnt": "10", "unitprice": "100,00", "price": "1.000,00"}], "total": {"total_price": "1.210,00"}}
    decision = b2(tree, qr, erp, view)
    assert decision.decision == "review"
    assert "extraction_incomplete" in codes(decision.reasons) and "no_po" in codes(decision.reasons)
    assert b2(tree, NOT_READ, erp, view).first_reason == "qr_not_read"
    assert decide(normalize(cord_tree_to_raw(tree)), qr, erp, view) == decision
