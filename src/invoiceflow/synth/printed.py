"""How a figure is printed on a synthetic invoice, in one place (plan section 7.2): Argentine number format (dot for thousands, comma for decimals), the
date as day/month/year, the CUIT with its hyphens, the point of sale in five digits and the number in eight.

The layouts print with these functions and ``model/serialize.py`` (U09) writes the target of the document model with the same ones, so what the model is asked
to read is exactly what the page shows; ``normalize.py`` (U08) is the inverse of them.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def amount(value: Decimal) -> str:
    """``1234.5`` -> ``1.234,50``: two decimals, half up, a dot between thousands and a comma before the decimals; a negative value keeps its minus sign."""
    rounded = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    text = f"{abs(rounded):,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"-{text}" if rounded < 0 else text


def quantity(value: Decimal) -> str:
    """A whole quantity as ``12``; a fractional one with a comma and up to three decimals (``2,5``)."""
    value = Decimal(value)
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f").replace(".", ",")


def date_es(day: date) -> str:
    """``2026-10-14`` -> ``14/10/2026``."""
    return f"{day.day:02d}/{day.month:02d}/{day.year:04d}"


def cuit(digits: str) -> str:
    """Eleven digits as ``30-00000000-7``."""
    return f"{digits[:2]}-{digits[2:10]}-{digits[10]}"


def pto_vta(number: int) -> str:
    return f"{number:05d}"


def nro(number: int) -> str:
    return f"{number:08d}"
