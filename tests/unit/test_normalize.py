"""Normalizing what the model read (U08; AC12, RF09; plan section 9.5, decision D06): the printed text of an amount, a date, a CUIT and a quantity becomes a value, or nothing when
it cannot be read, and a field the match needs that did not come out is named so that the decision can say ``extraction_incomplete``. Every expected value is a literal of
``tests/fixtures/hand_cases.py``; amounts are ``Decimal``, never ``float``."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest

from invoiceflow import normalize
from invoiceflow.normalize import InvoiceFields, normalize as normalize_fields, parse_amount, parse_cuit, parse_date, parse_quantity
from invoiceflow.synth import printed
from tests.fixtures import hand_cases as h


def test_amounts_are_decimal_and_parse_every_printed_form():
    for text, expected in h.AMOUNT_CASES:
        got = parse_amount(text)
        if expected is None:
            assert got is None, text
        else:
            assert isinstance(got, Decimal) and got == Decimal(expected) and got.as_tuple().exponent == -2, text
    rng = random.Random(7)
    for _ in range(500):
        value = (Decimal(rng.randint(-10**9, 10**11)) / 100).quantize(Decimal("0.01"))
        assert parse_amount(printed.amount(value)) == value, f"{value}: what the page prints is read back exactly"
    assert parse_amount(printed.amount(Decimal("0.00"))) == Decimal("0.00")


@pytest.mark.parametrize(("text", "value"), [
    ("1.234", "1234.00"), ("1.234.567,89", "1234567.89"), ("12,5", "12.50"), ("0,05", "0.05"), ("0", "0.00"), ("-5,00", "-5.00"), ("  12,00  ", "12.00"),
    (" 12,00 ", "12.00"), ("US$ 12,00", "12.00"), ("U$S 12,00", "12.00"), ("usd 12,00", "12.00"), ("ARS 1.000,00", "1000.00"), ("PES 7,00", "7.00"), ("DOL 7,00", "7.00"),
    ("$12,00", "12.00"), ("$ -3,00", "-3.00"), ("100.000,00", "100000.00"), ("999", "999.00"),
])
def test_other_printed_forms_of_an_amount(text, value):
    assert parse_amount(text) == Decimal(value)


@pytest.mark.parametrize("text", [
    "1.23", "1,234.56", "1.2345,00", "12,345", "12,", ",50", ".5", "1 234,56", "12,00 USD", "12.00", "--5", "5-", "(5,00)", "1.234,56,78", "$ $ 5", "٣٣,٠٠", "１２,００",
    "1e3", "12,0e1", "NaN", "Infinity", "12,00\n13,00", "1.0000.000,00", "0x10", "9" * 40, None, 12, 12.5, ["12,00"],
])
def test_what_is_not_an_amount_is_nothing(text):
    assert parse_amount(text) is None


def test_dates_are_read_day_first_and_checked_in_the_calendar():
    for text, expected in h.DATE_CASES:
        got = parse_date(text)
        assert (got.isoformat() if got else None) == expected, text
    assert parse_date("29/02/2028") == date(2028, 2, 29) and parse_date("29/02/2027") is None
    assert parse_date("14-10-2026") == date(2026, 10, 14) and parse_date(" 14/10/2026 ") == date(2026, 10, 14)
    for text in ("2026-13-01", "2026-10-32", "14/13/2026", "14/10/26", "2026/10/14", "14.10.2026", "2026-1-1", "٢٠٢٦-١٠-١٤", None, 20261014, "14/10/2026 08:00", "00/10/2026"):
        assert parse_date(text) is None, text
    for day in (date(2026, 1, 5), date(2026, 12, 31), date(2028, 2, 29)):
        assert parse_date(printed.date_es(day)) == day and parse_date(day.isoformat()) == day


def test_a_cuit_is_eleven_digits_and_its_check_digit_is_not_this_modules_business():
    for text, expected in h.CUIT_TEXT_CASES:
        assert parse_cuit(text) == expected, text
    assert parse_cuit("30-00000000-7") == "30000000007" and parse_cuit("30.00000000.7") == "30000000007" and parse_cuit(" 30000000007 ") == "30000000007"
    for text in ("30-00000000-77", "3000000000", "30-0000000a-7", "٣٠٠٠٠٠٠٠٠٠٧", "30000000007\n30000000015", None, 30000000007, ""):
        assert parse_cuit(text) is None, text
    assert parse_cuit(printed.cuit("30000000007")) == "30000000007"
    assert parse_cuit("30" + " " * 40 + "00000007") is None, "a text longer than any figure of an invoice is not read at all"


@pytest.mark.parametrize(("text", "value"), [("12", "12"), ("1.000", "1000"), ("2,5", "2.5"), ("0,125", "0.125"), ("10", "10"), (" 7 ", "7"), ("0", "0")])
def test_a_quantity_is_a_whole_number_or_has_up_to_three_decimals(text, value):
    got = parse_quantity(text)
    assert got == Decimal(value) and isinstance(got, Decimal)
    assert parse_quantity(printed.quantity(Decimal(value))) == Decimal(value)


@pytest.mark.parametrize("text", ["", "abc", "1,2345", "1.5", "-3", "1,", ",5", "12 u", "1.00", None, 3, "٣"])
def test_what_is_not_a_quantity_is_nothing(text):
    assert parse_quantity(text) is None


RAW = {
    "header": {"tipo": "1", "pto_vta": "00005", "nro": "00001234", "fecha": "14/10/2026", "cuit_emisor": "30-00000015-5", "razon_social": " Papelera Andina S.A. (ficticia) ",
               "cuit_receptor": "30-00000023-3", "orden_compra": "oc-2026-000123", "moneda": "PES", "cae": "70417054367476"},
    "lines": [{"codigo": "ART-0001", "descripcion": "Tornillo hexagonal", "cantidad": "12", "unidad": "caja", "precio_unitario": "1.234,50", "importe": "14.814,00"},
              {"codigo": "ART-0002", "descripcion": "Tuerca", "cantidad": "3", "unidad": "u", "precio_unitario": "10,00", "importe": "30,00"}],
    "totals": {"neto": "14.844,00", "iva_21": "3.117,24", "iva_105": "", "total": "17.961,24"},
}


def test_a_complete_reading_becomes_typed_fields_with_nothing_missing():
    fields = normalize_fields(RAW)
    assert isinstance(fields, InvoiceFields) and fields.missing == () and fields.complete
    assert (fields.tipo, fields.pto_vta, fields.nro, fields.fecha) == (1, 5, 1234, date(2026, 10, 14))
    assert (fields.cuit_emisor, fields.cuit_receptor, fields.cae, fields.moneda) == ("30000000155", "30000000233", "70417054367476", "PES")
    assert fields.razon_social == "Papelera Andina S.A. (ficticia)" and fields.orden_compra == "OC-2026-000123"
    assert [(line.codigo, line.cantidad, line.unidad, line.precio_unitario, line.importe) for line in fields.lines] == [
        ("ART-0001", Decimal("12"), "caja", Decimal("1234.50"), Decimal("14814.00")), ("ART-0002", Decimal("3"), "u", Decimal("10.00"), Decimal("30.00"))]
    assert (fields.neto, fields.iva, fields.iva_rate, fields.total) == (Decimal("14844.00"), Decimal("3117.24"), Decimal("21"), Decimal("17961.24"))


def test_the_reduced_iva_is_read_as_10_5_percent():
    raw = {**RAW, "totals": {"neto": "100,00", "iva_21": None, "iva_105": "10,50", "total": "110,50"}}
    fields = normalize_fields(raw)
    assert (fields.iva, fields.iva_rate) == (Decimal("10.50"), Decimal("10.5")) and fields.complete
    both = normalize_fields({**RAW, "totals": {"neto": "100,00", "iva_21": "21,00", "iva_105": "10,50", "total": "131,50"}})
    assert both.iva is None and both.iva_rate is None, "two kinds of IVA on one page is not a reading"
    neither = normalize_fields({**RAW, "totals": {"neto": "100,00", "total": "100,00"}})
    assert neither.iva is None and neither.iva_rate is None and neither.complete, "the IVA is not what the match needs to be there"


def test_a_field_the_match_needs_that_did_not_come_out_is_named():
    def lines(**changes):
        return [{**RAW["lines"][0], **changes}, RAW["lines"][1]]

    cases = {
        "lines[0].cantidad": {**RAW, "lines": lines(cantidad="doce")}, "lines[0].precio_unitario": {**RAW, "lines": lines(precio_unitario="1.234.50")},
        "lines[0].importe": {**RAW, "lines": lines(importe="")}, "lines[0].codigo": {**RAW, "lines": lines(codigo=None, descripcion="")},
        "totals.neto": {**RAW, "totals": {**RAW["totals"], "neto": "N/A"}}, "totals.total": {**RAW, "totals": {**RAW["totals"], "total": None}},
        "lines": {**RAW, "lines": []},
    }
    for name, raw in cases.items():
        fields = normalize_fields(raw)
        assert name in fields.missing and not fields.complete, name
    only = normalize_fields({**RAW, "lines": lines(cantidad="x", importe="y")})
    assert only.missing == ("lines[0].cantidad", "lines[0].importe") and only.lines[0].cantidad is None and only.lines[1].cantidad == Decimal("3")
    nothing = normalize_fields({**RAW, "lines": lines(codigo="", descripcion="Tornillo")})
    assert nothing.complete and nothing.lines[0].codigo is None, "a line with a description and no code is matched by its description"


def test_what_the_page_does_not_have_or_the_model_could_not_tell_is_empty_and_not_a_guess():
    fields = normalize_fields({**RAW, "header": {**RAW["header"], "fecha": "pasado mañana", "cuit_emisor": "abc", "pto_vta": "", "nro": "x", "orden_compra": "", "tipo": "A"}})
    assert (fields.fecha, fields.cuit_emisor, fields.pto_vta, fields.nro, fields.orden_compra, fields.tipo) == (None, None, None, None, None, None)
    assert fields.complete, "the header comes from the QR and the order's absence is the match's no_po: none of it is what the decision calls incomplete"


@pytest.mark.parametrize("raw", [None, [], "text", 5, {}, {"header": 1, "lines": "x", "totals": []}, {"lines": [1, None, "x"]}, {"lines": [{"cantidad": ["1"]}]}])
def test_whatever_the_model_returned_something_comes_out_and_never_raises(raw):
    fields = normalize_fields(raw)
    assert isinstance(fields, InvoiceFields) and not fields.complete and "lines" in fields.missing or fields.lines
    assert "totals.neto" in fields.missing and "totals.total" in fields.missing


def test_the_orders_number_is_trimmed_and_upper_case_and_nothing_else():
    assert normalize_fields({**RAW, "header": {**RAW["header"], "orden_compra": "  oc-2026-000123 "}}).orden_compra == "OC-2026-000123"
    assert normalize_fields({**RAW, "header": {**RAW["header"], "orden_compra": "OC 2026 123"}}).orden_compra == "OC 2026 123"


def test_the_module_reads_no_float():
    import inspect

    source = inspect.getsource(normalize)
    assert "float(" not in source and "float " not in source.replace("# ", ""), "an amount is a Decimal from the first character"
