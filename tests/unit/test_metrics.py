"""The measures of the model (U14; AC18, AC22 in part; plan sections 9.3 and 16, decision D12): the tree-edit-distance accuracy and the leaf recall of the research (``research/s02``), the
amounts of CORD read in their own formats, and the rate of exact invoice lines. Every expected value is worked out by hand in the test; U16 adds the intervals and the rates of the closing
evaluation to the same module."""

from __future__ import annotations

from decimal import Decimal

import pytest

from invoiceflow import metrics
from invoiceflow.metrics import exact_lines, leaf_match, leaves, normalise, parse_money, ted_accuracy
from invoiceflow.normalize import InvoiceFields, LineFields


@pytest.mark.parametrize(("text", "value"), [("60.000", "60000"), ("Rp 16.500", "16500"), ("1,591,600", "1591600"), ("226,500.00", "226500.00"), ("2.00", "2.00"),
                                             ("2,5", "2.5"), ("-1.200", "-1200"), ("1.234.567,89", "1234567.89"), ("RP45", "45"), ("12", "12")])
def test_an_amount_of_cord_is_read_in_the_format_it_was_annotated_in(text, value):
    got = parse_money(text)
    assert got == Decimal(value) and isinstance(got, Decimal)


@pytest.mark.parametrize("text", ["", "abc", "1,2,3", "12x", "1.2.3", "39,200,00", None, 12, ["1"]])
def test_what_is_not_an_amount_is_none_and_never_a_guess(text):
    assert parse_money(text) is None


def test_the_normal_form_of_a_leaf_is_the_amount_to_cents_or_the_lower_case_text_with_its_whitespace_collapsed():
    assert normalise("Rp 16.500") == "16500.00" and normalise("16,500.00") == "16500.00" and normalise("2") == "2.00"
    assert normalise("  Nasi   GORENG ") == "nasi goreng" and normalise(None) == "" and normalise(5) == "5.00" and normalise("") == ""


def test_the_ted_accuracy_is_one_minus_the_edit_distance_over_the_distance_from_nothing_by_hand():
    gt = {"total": {"total_price": "100"}}          # root, total, total_price and the leaf: three nodes to insert from nothing
    assert ted_accuracy(gt, gt) == 1.0
    assert ted_accuracy({"total": {"total_price": "100.00"}}, gt) == 1.0, "the same amount in another form is the same leaf"
    assert ted_accuracy({"total": {"total_price": "200"}}, gt) == pytest.approx(1 - 1 / 3), "one leaf relabelled"
    assert ted_accuracy({}, gt) == 0.0 and ted_accuracy({"total": {}}, gt) == pytest.approx(1 - 2 / 3), "a node and its leaf missing"
    assert ted_accuracy({"total": {"total_price": "100"}, "x": {"y": "1"}}, gt) == 0.0, "three nodes too many: as far from the truth as nothing is"
    assert ted_accuracy({"total": {"total_price": "100"}, "x": "1"}, gt) == pytest.approx(1 - 2 / 3), "two nodes too many"
    assert ted_accuracy({"a": {"b": "c"}, "x": {"y": "1"}}, gt) == 0.0, "never below zero"
    assert ted_accuracy({"menu": [{"nm": "a"}, {"nm": "b"}]}, {"menu": [{"nm": "a"}, {"nm": "b"}]}) == 1.0
    assert ted_accuracy({"menu": [{"nm": "b"}, {"nm": "a"}]}, {"menu": [{"nm": "a"}, {"nm": "b"}]}) < 1.0, "the order of the lines is part of the tree"
    assert ted_accuracy({"anything": "1"}, {}) == 1.0, "nothing to read, nothing to lose: the research's rule for an empty ground truth"


def test_the_leaves_are_listed_with_their_path_and_the_leaf_recall_counts_repeats_once_each():
    assert leaves({"menu": [{"nm": "A", "cnt": "1"}, {"nm": "B", "cnt": "2"}], "total": {"total_price": "Rp 3.000"}}) == [
        ("menu[].nm", "a"), ("menu[].cnt", "1.00"), ("menu[].nm", "b"), ("menu[].cnt", "2.00"), ("total.total_price", "3000.00")]
    gt = {"menu": [{"nm": "A"}, {"nm": "A"}, {"nm": "B"}], "total": {"total_price": "3.000"}}
    matched, total, per_path = leaf_match({"menu": [{"nm": "a"}, {"nm": "b"}], "total": {"total_price": "3000"}}, gt)
    assert (matched, total) == (3, 4), "two lines called A and the prediction has one: only one of them is matched"
    assert per_path == {"menu[].nm": (3, 2), "total.total_price": (1, 1)}
    assert leaf_match({}, {}) == (0, 0, {})


def line(code, qty, price, amount, **extra):
    from invoiceflow.synth.invoices import InvoiceLine

    return InvoiceLine(code, extra.get("desc", f"Articulo {code}"), Decimal(qty), "u", Decimal(price), Decimal(amount))


def read(code, qty, price, amount):
    return LineFields(code, "x", None if qty is None else Decimal(qty), "u", None if price is None else Decimal(price), None if amount is None else Decimal(amount))


def test_a_line_is_exact_when_code_quantity_unit_price_and_amount_are_the_truths_and_the_rate_counts_the_truths_lines():
    truth = [line("A1", "2", "10.00", "20.00"), line("A2", "1", "5.50", "5.50"), line("A3", "3", "1.00", "3.00")]
    fields = InvoiceFields(lines=(read("A1", "2", "10.00", "20.00"), read("A2", "1", "5.50", "5.51"), read("A3", "3", "1.00", "3.00")))
    assert exact_lines(fields, truth) == (2, 3), "one amount is a cent off: the line is not exact"
    assert exact_lines(InvoiceFields(), truth) == (0, 3) and exact_lines(InvoiceFields(lines=(read("A1", None, "10.00", "20.00"),)), truth) == (0, 3)
    swapped = InvoiceFields(lines=(read("A3", "3", "1.00", "3.00"), read("A1", "2", "10.00", "20.00"), read("A2", "1", "5.50", "5.50")))
    assert exact_lines(swapped, truth) == (3, 3), "a line is matched to a line of the truth wherever it is read"
    twice = InvoiceFields(lines=(read("A1", "2", "10.00", "20.00"),) * 2)
    assert exact_lines(twice, [line("A1", "2", "10.00", "20.00")]) == (1, 1), "a read line is used once"
    assert exact_lines(twice, [line("A1", "2", "10.00", "20.00")] * 2) == (2, 2)
    assert exact_lines(InvoiceFields(lines=(read("A1", "2", "10.00", "20.00"),)), [line("A1", "2", "10.00", "20.00")] * 2) == (1, 2), "one read line matches one line of the truth, not two"
    assert exact_lines(fields, []) == (0, 0)


def test_the_module_reads_no_float_into_an_amount():
    import inspect

    source = inspect.getsource(metrics.parse_money)
    assert "float(" not in source

