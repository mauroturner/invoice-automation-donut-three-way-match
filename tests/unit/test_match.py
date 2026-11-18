"""The reasons of the two- and three-way match (U11; AC13; plan section 11, decisions D14, D15, D45, D48; RF12): eighteen reasons in a fixed order, each with its rule at its border.
The borders are written by hand in ``tests/fixtures/hand_cases.py`` and the test that walks them is the one the plan names; each reason also has its own test that it fires and that it does not
fire one step inside. ``dataclasses.replace`` changes one thing of a good invoice (``tests/fixtures/match_cases.py``)."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from invoiceflow.match import REASONS, ErpView, PoLine, Reason, post_failed, reasons
from tests.fixtures import hand_cases as hc
from tests.fixtures.match_cases import (
    CUIT,
    DAY,
    ORDER_NO,
    OTHER_CUIT,
    OTHER_SUPPLIER,
    SUPPLIER,
    codes,
    good,
    invalid,
    invoice,
    line,
    order,
    payload,
    qr_ok,
    receipt,
    make_view,
    with_match,
)

D = Decimal


def test_a_good_invoice_has_no_reason(view):
    fields, qr, erp = good()
    assert reasons(fields, qr, erp, view) == []


def test_the_eighteen_reasons_are_the_ones_of_the_plans_table_in_its_order():
    assert REASONS == ("file_rejected", "qr_not_read", "qr_invalid", "extraction_incomplete", "unsupported_type", "qr_print_mismatch", "duplicate", "no_po", "supplier_mismatch",
                       "currency_mismatch", "date_out_of_range", "line_not_on_po", "line_arithmetic", "total_arithmetic", "price_out_of_tolerance", "qty_over_receipt",
                       "qty_over_order", "post_failed")
    assert len(REASONS) == len(set(REASONS)) == 18


def test_every_tolerance_boundary_by_hand(view):
    """Price at 1.99 %, 2.00 % and 2.01 % of the order's; quantity equal to, one more and one less than what was received; the receipt of the day and the next day's; the rounding at 0.01 and
    0.02. ``inside`` includes the border. The expected results are the hand cases, not computed here."""
    for order_price, price, inside in hc.PRICE_CASES:
        fields = invoice((line(price=price),))
        erp = make_view(po=order((PoLine("A1", "Tornillo hexagonal", D("10"), D(order_price)),)))
        assert ("price_out_of_tolerance" not in codes(reasons(fields, qr_ok(fields), erp, view))) is inside, (order_price, price)
    for received, before, qty, within in hc.RECEIPT_QUANTITY_CASES:
        fields = invoice((line(qty=qty, price="1.00"),))
        receipts = (receipt(received),) if D(received) else ()
        erp = make_view(receipts_until_day=receipts, invoiced_before={"A1": D(before)}, po=order((PoLine("A1", "Tornillo hexagonal", D("100"), D("1.00")),)))
        assert ("qty_over_receipt" not in codes(reasons(fields, qr_ok(fields), erp, view))) is within, (received, before, qty)
    for receipt_day, counts in hc.RECEIPT_DAY_CASES:
        assert hc.INVOICE_DAY == DAY
        erp = make_view(receipts_until_day=(receipt("10", receipt_day),))
        fields = invoice()
        assert ("qty_over_receipt" not in codes(reasons(fields, qr_ok(fields), erp, view))) is counts, receipt_day
    for qty, price, amount, closes in hc.ROUNDING_CASES:
        fields = invoice((line(qty=qty, price=price, amount=amount),))
        erp = make_view(po=order((PoLine("A1", "Tornillo hexagonal", D(qty) or D(1), D(price)),)), receipts_until_day=(receipt(qty if D(qty) else "1"),))
        assert ("line_arithmetic" not in codes(reasons(fields, qr_ok(fields), erp, view))) is closes, (qty, price, amount)


def test_the_totals_close_at_one_cent_per_line_and_one_cent_for_the_iva(view):
    two = (line("A1", "1", "10.00"), line("A2", "1", "20.00"))
    po = order((PoLine("A1", "x", D(1), D(10)), PoLine("A2", "y", D(1), D(20))))
    erp = make_view(po=po, receipts_until_day=(receipt("1"), receipt("1", code="A2", number="REM-2")))
    for shift, ok in (("0.00", True), ("0.01", True), ("0.02", True), ("0.03", False)):
        fields = invoice(two)
        fields = dataclasses.replace(fields, neto=fields.neto + D(shift), iva=((fields.neto + D(shift)) * D("0.21")).quantize(D("0.01")))
        fields = dataclasses.replace(fields, total=fields.neto + fields.iva)
        assert ("total_arithmetic" not in codes(reasons(fields, qr_ok(fields), erp, view))) is ok, f"net {shift} over the sum of two lines"
    one = invoice()
    erp1 = make_view()
    for shift, ok in (("0.00", True), ("0.01", True), ("0.02", False)):
        fields = dataclasses.replace(one, total=one.total + D(shift))
        assert ("total_arithmetic" not in codes(reasons(fields, qr_ok(fields), erp1, view))) is ok, f"total {shift} over net plus iva"
        fields = dataclasses.replace(one, total=one.total - D(shift))
        assert ("total_arithmetic" not in codes(reasons(fields, qr_ok(fields), erp1, view))) is ok, f"total -{shift} under net plus iva"


def test_the_tolerances_come_from_the_configuration(view):
    fields = invoice((line(price="104.00"),))
    erp = make_view()
    assert "price_out_of_tolerance" in codes(reasons(fields, qr_ok(fields), erp, view))
    assert "price_out_of_tolerance" not in codes(reasons(fields, qr_ok(fields), erp, with_match(view, price_tolerance_pct=4.0)))
    assert "price_out_of_tolerance" in codes(reasons(fields, qr_ok(fields), erp, with_match(view, price_tolerance_pct=3.99)))
    over = invoice((line(qty="11", price="100.00"),))
    erp_over = make_view(po=order((PoLine("A1", "x", D("20"), D("100.00")),)))
    assert "qty_over_receipt" in codes(reasons(over, qr_ok(over), erp_over, view))
    assert "qty_over_receipt" not in codes(reasons(over, qr_ok(over), erp_over, with_match(view, qty_over_receipt=0.1)))
    assert "qty_over_receipt" in codes(reasons(over, qr_ok(over), erp_over, with_match(view, qty_over_receipt=0.09)))
    off = invoice((line(amount="1000.05"),))
    assert "line_arithmetic" in codes(reasons(off, qr_ok(off), erp, view))
    assert "line_arithmetic" not in codes(reasons(off, qr_ok(off), erp, with_match(view, rounding_per_line=0.05)))
    one = invoice()
    shown = dataclasses.replace(one, total=one.total + D("0.05"))
    assert "total_arithmetic" in codes(reasons(shown, qr_ok(shown), erp, view))
    assert "qr_print_mismatch" in codes(reasons(shown, qr_ok(one), erp, view))
    assert "qr_print_mismatch" not in codes(reasons(shown, qr_ok(one), erp, with_match(view, total_qr_tolerance=0.05)))
    assert "qr_print_mismatch" in codes(reasons(shown, qr_ok(one), erp, with_match(view, total_qr_tolerance=0.04)))


def test_a_price_with_a_zero_on_the_order_is_never_divided_by(view):
    erp = make_view(po=order((PoLine("A1", "x", D("10"), D("0.00")),)))
    zero = invoice((line(price="0.00"),))
    assert "price_out_of_tolerance" not in codes(reasons(zero, qr_ok(zero), erp, view))
    dear = invoice((line(price="0.01"),))
    assert "price_out_of_tolerance" in codes(reasons(dear, qr_ok(dear), erp, view))


# --- one reason at a time -------------------------------------------------------------------------------------------------------------------


def test_file_rejected_stands_alone_and_names_the_rule(view):
    only = reasons(None, None, None, view, file_rejected="V14")
    assert codes(only) == ["file_rejected"] and only[0].rank == 1 and only[0].detail == {"rule": "V14"}


def test_a_page_whose_qr_was_not_read_or_is_invalid_gives_its_reason(view):
    fields = invoice()
    from tests.fixtures.match_cases import NOT_READ

    assert codes(reasons(fields, NOT_READ, make_view(), view))[:1] == ["qr_not_read"]
    result = reasons(fields, invalid("host"), make_view(), view)
    assert codes(result)[:1] == ["qr_invalid"] and result[0].detail == {"reason": "host"}


def test_extraction_incomplete_lists_what_is_missing_and_the_order_number_is_not_in_it(view):
    fields, qr, erp = good()
    missing = dataclasses.replace(fields, missing=("lines[0].cantidad", "totals.neto"))
    result = reasons(missing, qr, erp, view)
    assert codes(result) == ["extraction_incomplete"] and result[0].detail == {"missing": ["lines[0].cantidad", "totals.neto"]}
    nobody = dataclasses.replace(fields, orden_compra=None)
    assert "extraction_incomplete" not in codes(reasons(nobody, qr, erp, view)) and "no_po" in codes(reasons(nobody, qr, erp, view)), "D45"


def test_unsupported_type_reads_the_qr_first_then_the_page(view):
    fields = invoice(tipo=3)
    assert codes(reasons(fields, qr_ok(fields), make_view(), view))[:1] == ["unsupported_type"]
    printed_other = dataclasses.replace(invoice(), tipo=3)
    assert "unsupported_type" not in codes(reasons(printed_other, qr_ok(invoice()), make_view(), view)), "the QR says 1: the QR wins (D06)"
    assert "unsupported_type" in codes(reasons(invoice(), qr_ok(invoice(), tipo_cmp=3), make_view(), view))
    wide = with_match(view, supported_types=(1, 3))
    assert "unsupported_type" not in codes(reasons(fields, qr_ok(fields), make_view(), wide))


def test_qr_print_mismatch_names_each_field_that_differs(view):
    fields, _, erp = good()
    cases = {"cuit": dict(cuit=OTHER_CUIT), "pto_vta": dict(pto_vta=2), "nro": dict(nro_cmp=11), "fecha": dict(fecha=DAY - timedelta(days=1)), "total": dict(importe=fields.total + D("0.05"))}
    for name, change in cases.items():
        result = reasons(fields, qr_ok(fields, **change), erp, view)
        mismatch = [r for r in result if r.code == "qr_print_mismatch"]
        assert mismatch and mismatch[0].detail == {"fields": [name]}, name
    both = reasons(fields, qr_ok(fields, pto_vta=2, importe=fields.total + 1), erp, view)
    assert [r.detail for r in both if r.code == "qr_print_mismatch"] == [{"fields": ["pto_vta", "total"]}]
    assert "qr_print_mismatch" not in codes(reasons(fields, qr_ok(fields, importe=fields.total + D("0.01")), erp, view)), "one cent is inside the tolerance"
    unread = dataclasses.replace(fields, pto_vta=None)
    assert "qr_print_mismatch" in codes(reasons(unread, qr_ok(fields), erp, view)), "what cannot be read cannot be confirmed"


def test_a_duplicate_is_a_voucher_the_warehouse_or_the_erp_already_knows(view):
    fields, qr, _ = good()
    known = make_view(known_fiscal_keys=frozenset({(CUIT, 1, 1, 10)}))
    assert "duplicate" in codes(reasons(fields, qr, known, view))
    for key in ((CUIT, 2, 1, 10), (CUIT, 1, 3, 10), (CUIT, 1, 1, 11), (OTHER_CUIT, 1, 1, 10)):
        assert "duplicate" not in codes(reasons(fields, qr, make_view(known_fiscal_keys=frozenset({key})), view)), key


def test_no_po_when_the_page_cites_none_or_the_erp_has_none(view):
    fields, qr, erp = good()
    none_cited = dataclasses.replace(fields, orden_compra=None)
    result = reasons(none_cited, qr, make_view(po=None), view)
    assert [r.detail for r in result if r.code == "no_po"] == [{"cited": False}]
    cited_but_absent = reasons(fields, qr, make_view(po=None), view)
    assert [r.detail for r in cited_but_absent if r.code == "no_po"] == [{"cited": True}]
    rest = {"supplier_mismatch", "currency_mismatch", "date_out_of_range", "line_not_on_po", "price_out_of_tolerance", "qty_over_receipt", "qty_over_order"}
    assert not rest & set(codes(cited_but_absent)), "nothing about an order that is not there"


def test_supplier_mismatch_compares_the_order_with_the_supplier_of_the_qrs_cuit(view):
    fields, qr, _ = good()
    assert "supplier_mismatch" in codes(reasons(fields, qr, make_view(po=order(supplier=OTHER_SUPPLIER)), view))
    assert "supplier_mismatch" in codes(reasons(fields, qr, make_view(supplier_by_cuit={}), view)), "a CUIT the ERP does not know is nobody's"
    assert "supplier_mismatch" not in codes(reasons(fields, qr, make_view(), view))
    printed_only = qr_ok(fields, cuit=OTHER_CUIT)
    assert "supplier_mismatch" in codes(reasons(fields, printed_only, make_view(), view)), "the QR's CUIT is the supplier"


def test_currency_mismatch_uses_the_qrs_currency_and_reads_arca_and_iso_codes_alike(view):
    fields, _, erp = good()
    assert "currency_mismatch" in codes(reasons(fields, qr_ok(fields, moneda="DOL"), erp, view))
    assert "currency_mismatch" not in codes(reasons(fields, qr_ok(fields, moneda="PES"), make_view(po=order(currency="ARS")), view)), "PES is ARS"
    assert "currency_mismatch" not in codes(reasons(fields, qr_ok(fields, moneda="DOL"), make_view(po=order(currency="USD")), view))
    assert "currency_mismatch" in codes(reasons(fields, qr_ok(fields, moneda="DOL"), make_view(po=order(currency="ARS")), view))
    assert [r.detail for r in reasons(fields, qr_ok(fields, moneda="DOL"), erp, view) if r.code == "currency_mismatch"] == [{"invoice": "DOL", "order": "PES"}]


def test_the_date_is_between_the_order_and_the_day_of_the_delivery_borders_included(view):
    for fecha, ok in ((date(2026, 9, 20), True), (date(2026, 9, 19), False), (DAY, True), (DAY + timedelta(days=1), False)):
        fields = invoice(fecha=fecha)
        assert ("date_out_of_range" not in codes(reasons(fields, qr_ok(fields), make_view(), view))) is ok, fecha
    before = invoice(fecha=date(2026, 9, 1))
    after = invoice(fecha=DAY + timedelta(days=3))
    assert [r.detail for r in reasons(before, qr_ok(before), make_view(), view) if r.code == "date_out_of_range"] == [{"side": "before_order"}]
    assert [r.detail for r in reasons(after, qr_ok(after), make_view(), view) if r.code == "date_out_of_range"] == [{"side": "after_day"}]


def test_a_line_is_on_the_order_by_code_or_by_description_when_it_has_no_code(view):
    two_codes = (line("A1"), line("ZZ", "1", "5.00"))
    fields = invoice(two_codes)
    result = reasons(fields, qr_ok(fields), make_view(), view)
    assert [r.detail for r in result if r.code == "line_not_on_po"] == [{"lines": [1]}]
    by_description = invoice((line(None, description="  TORNILLO   hexagonal "),))
    assert "line_not_on_po" not in codes(reasons(by_description, qr_ok(by_description), make_view(), view)), "case, blanks and edges are normalised"
    other = invoice((line(None, description="Tuerca"),))
    assert "line_not_on_po" in codes(reasons(other, qr_ok(other), make_view(), view))
    lower = invoice((line("a1"),))
    assert "line_not_on_po" not in codes(reasons(lower, qr_ok(lower), make_view(), view))


def test_quantities_add_up_by_item_across_lines_and_with_what_was_invoiced_before(view):
    erp = make_view(po=order((PoLine("A1", "Tornillo hexagonal", D("10"), D("100.00")),)), receipts_until_day=(receipt("10"),))
    split = invoice((line("A1", "6"), line("A1", "5")))
    result = reasons(split, qr_ok(split), erp, view)
    assert [r.detail for r in result if r.code == "qty_over_receipt"] == [{"lines": [0, 1]}]
    assert [r.detail for r in result if r.code == "qty_over_order"] == [{"lines": [0, 1]}]
    fits = invoice((line("A1", "6"), line("A1", "4")))
    assert not {"qty_over_receipt", "qty_over_order"} & set(codes(reasons(fits, qr_ok(fits), erp, view)))
    before = make_view(invoiced_before={"A1": D("1")}, po=erp.po, receipts_until_day=erp.receipts_until_day)
    assert {"qty_over_receipt", "qty_over_order"} <= set(codes(reasons(fits, qr_ok(fits), before, view)))


def test_over_the_order_is_over_what_was_received_too_and_both_reasons_come_in_that_order(view):
    fields = invoice((line("A1", "11"),))
    result = codes(reasons(fields, qr_ok(fields), make_view(), view))
    assert result[result.index("qty_over_receipt"):result.index("qty_over_receipt") + 2] == ["qty_over_receipt", "qty_over_order"]


def test_a_receipt_of_another_item_or_after_the_day_is_not_received_stock(view):
    fields, qr, _ = good()
    assert "qty_over_receipt" in codes(reasons(fields, qr, make_view(receipts_until_day=(receipt("10", code="B2"),)), view))
    assert "qty_over_receipt" in codes(reasons(fields, qr, make_view(receipts_until_day=(receipt("10", DAY + timedelta(days=1)),)), view)), "the future does not count even if it is handed over"
    two = make_view(receipts_until_day=(receipt("4", number="R1"), receipt("6", DAY, number="R2")))
    assert "qty_over_receipt" not in codes(reasons(fields, qr, two, view)), "two receipts add up, the day's included"


def test_an_unreadable_iva_cannot_be_checked_and_is_a_total_arithmetic_reason(view):
    fields, qr, erp = good()
    blind = dataclasses.replace(fields, iva=None, iva_rate=None)
    assert [r.detail for r in reasons(blind, qr, erp, view) if r.code == "total_arithmetic"] == [{"parts": ["iva_unreadable"]}]
    mismatch = dataclasses.replace(fields, neto=fields.neto + D("5.00"))
    assert [r.detail for r in reasons(mismatch, qr, erp, view) if r.code == "total_arithmetic"] == [{"parts": ["sum_vs_net", "net_plus_iva_vs_total"]}]


def test_the_reasons_come_in_the_order_of_the_table_whatever_the_number_of_them(view):
    fields = invoice((line("A1", "11"), line("ZZ", "1", "5.00", amount="9.00")), tipo=3, orden_compra=ORDER_NO, fecha=date(2026, 9, 1))
    fields = dataclasses.replace(fields, total=fields.total + D("7.00"))
    erp = make_view(known_fiscal_keys=frozenset({(CUIT, 1, 3, 10)}), po=order(supplier=OTHER_SUPPLIER, currency="DOL"))
    result = reasons(fields, qr_ok(fields, moneda="PES", importe=fields.total + D("3.00")), erp, view)
    assert len(result) >= 10
    assert [r.rank for r in result] == sorted(r.rank for r in result) and all(REASONS[r.rank - 1] == r.code for r in result)
    assert len({r.code for r in result}) == len(result), "each reason once"


def test_post_failed_is_the_last_reason_and_comes_from_the_post(view):
    reason = post_failed("read_back")
    assert reason == Reason("post_failed", 18, {"stage": "read_back"}) and isinstance(reason, Reason)
    with pytest.raises(ValueError):
        post_failed("because")


def test_no_reason_carries_a_text_of_the_document(view):
    """The details are keys, numbers and fixed codes (decision_reasons.detail_json): a description or a name from the page never reaches them."""
    secret = "TEXTO-SECRETO-DEL-DOCUMENTO"
    fields = invoice((line("A1", "11", description=secret), line("ZZ", "1", "5.00", amount="9.00", description=secret)), razon_social=secret, tipo=3, fecha=date(2026, 9, 1))
    erp = make_view(po=order(supplier=OTHER_SUPPLIER, currency="DOL"), known_fiscal_keys=frozenset({(CUIT, 1, 3, 10)}))
    result = reasons(dataclasses.replace(fields, missing=("lines[0].cantidad",)), qr_ok(fields, importe=fields.total + 2), erp, view)
    assert result and secret not in json.dumps([r.detail for r in result])
    for r in result:
        json.dumps(r.detail)   # and it is JSON


def test_the_view_is_not_changed_by_asking(view):
    fields, qr, erp = good()
    before = (erp.known_fiscal_keys, dict(erp.invoiced_before), erp.receipts_until_day, erp.po)
    reasons(fields, qr, erp, view)
    assert (erp.known_fiscal_keys, dict(erp.invoiced_before), erp.receipts_until_day, erp.po) == before
    assert isinstance(erp, ErpView) and dataclasses.is_dataclass(erp)


def test_an_unread_total_is_a_mismatch_with_the_qr_and_not_an_error(view):
    fields, qr, erp = good()
    unread = dataclasses.replace(fields, total=None)
    result = reasons(unread, qr, erp, view)
    assert [r.detail for r in result if r.code == "qr_print_mismatch"] == [{"fields": ["total"]}]


def test_without_a_qr_the_printed_voucher_is_the_one_that_can_be_a_duplicate_and_the_printed_date_the_one_that_is_checked(view):
    from tests.fixtures.match_cases import NOT_READ

    relaxed = with_match(view, require_qr=False)
    fields = invoice()
    assert "duplicate" in codes(reasons(fields, NOT_READ, make_view(known_fiscal_keys=frozenset({(CUIT, 1, 1, 10)})), relaxed))
    assert "duplicate" not in codes(reasons(fields, NOT_READ, make_view(known_fiscal_keys=frozenset({(CUIT, 1, 10, 1)})), relaxed)), "the key is (CUIT, point of sale, type, number)"
    early = invoice(fecha=date(2026, 9, 1))
    assert "date_out_of_range" in codes(reasons(early, NOT_READ, make_view(), relaxed))


def test_the_qr_date_is_the_one_that_is_checked_when_the_page_and_the_qr_differ(view):
    fields, _, erp = good()
    printed_early = dataclasses.replace(fields, fecha=date(2026, 9, 1))
    result = codes(reasons(printed_early, qr_ok(fields), erp, view))
    assert "date_out_of_range" not in result and "qr_print_mismatch" in result
    qr_early = qr_ok(fields, fecha=date(2026, 9, 1))
    result = codes(reasons(fields, qr_early, erp, view))
    assert "date_out_of_range" in result and "qr_print_mismatch" in result


def test_only_the_receipts_of_the_order_count_as_received(view):
    from invoiceflow.match import Receipt, ReceiptLine

    fields, qr, _ = good()
    other_order = Receipt("REM-X", "OC-2026-000999", date(2026, 10, 4), (ReceiptLine("A1", D("10")),))
    assert "qty_over_receipt" in codes(reasons(fields, qr, make_view(receipts_until_day=(other_order,)), view))


def test_an_item_that_is_on_two_lines_of_the_order_is_ordered_in_the_sum_of_both(view):
    po = order((PoLine("A1", "Tornillo hexagonal", D("6"), D("100.00")), PoLine("A1", "Tornillo hexagonal", D("6"), D("100.00"))))
    erp = make_view(po=po, receipts_until_day=(receipt("12"),))
    fields = invoice((line("A1", "12"),))
    assert not {"qty_over_order", "qty_over_receipt"} & set(codes(reasons(fields, qr_ok(fields), erp, view)))
    over = invoice((line("A1", "13"),))
    assert "qty_over_order" in codes(reasons(over, qr_ok(over), erp, view))
