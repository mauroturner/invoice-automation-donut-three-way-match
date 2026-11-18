"""How figures are printed on a synthetic invoice (U06; plan section 7.2): Argentine number format, the date, the CUIT, the point of sale and the number."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from invoiceflow.synth import printed


@pytest.mark.parametrize(("value", "text"), [
    ("1234.56", "1.234,56"), ("1234.5", "1.234,50"), ("0", "0,00"), ("0.005", "0,01"), ("0.004", "0,00"), ("1234567.891", "1.234.567,89"), ("999.995", "1.000,00"),
    ("-1234.56", "-1.234,56"), ("-0.001", "0,00"), ("12", "12,00"),
])
def test_an_amount_prints_with_dot_thousands_comma_decimals_and_half_up_cents(value, text):
    assert printed.amount(Decimal(value)) == text


@pytest.mark.parametrize(("value", "text"), [("12", "12"), ("12.0", "12"), ("2.5", "2,5"), ("0.125", "0,125"), ("1000", "1000"), ("1E+3", "1000")])
def test_a_quantity_prints_whole_or_with_a_comma(value, text):
    assert printed.quantity(Decimal(value)) == text


def test_dates_cuits_points_of_sale_and_numbers_print_in_their_fixed_widths():
    assert printed.date_es(date(2026, 10, 4)) == "04/10/2026" and printed.date_es(date(2026, 12, 31)) == "31/12/2026"
    assert printed.cuit("30000000007") == "30-00000000-7"
    assert printed.pto_vta(5) == "00005" and printed.pto_vta(12345) == "12345"
    assert printed.nro(1234) == "00001234" and printed.nro(99999999) == "99999999"
