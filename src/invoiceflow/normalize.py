"""Normalizing what the document model read (RF09; AC12; plan section 9.5): printed text to values, strictly.

The model returns the text as it is printed (``model/serialize.py`` writes its target with ``synth.printed``'s formats); this module is the inverse of those formats and
reads nothing else. An amount is ``1.234,56`` (a dot between thousands in groups of three, a comma before one or two decimals), with an optional leading minus and an optional
leading currency (``$``, ``US$``, ``U$S``, ``USD``, ``ARS``, ``PES``, ``DOL``); an English ``1,234.56``, a dot as decimals, a trailing currency or a third decimal is not an amount
(it would be read as another number), and neither is any digit that is not ASCII. Amounts are ``Decimal`` with two decimals from the first character: no ``float`` is ever made.
A date is ``d/m/yyyy`` (or with hyphens) or ``yyyy-mm-dd``, a real day of the calendar. A CUIT is eleven digits with or without hyphens, spaces or dots: whether its check digit is right
is the QR's and the match's business (``qr_invalid``, ``supplier_mismatch``), not this module's. What cannot be read is ``None``, never a guess.

``normalize`` turns the model's tree (``header``, ``lines``, ``totals``) into ``InvoiceFields``, never raises, and names in ``missing`` what the match needs and did not come out: each line's
quantity, unit price and amount and its code (or its description), the net and the total, and ``lines`` itself when there are none. The order number is not in that list: a page with no
order is the match's ``no_po``, and the model cannot tell a page that has none from one it could not read (decision D45).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENT = Decimal("0.01")
MAX_TEXT = 40            # the longest text a parser of a figure (an amount, a quantity, a date, a CUIT) reads; a free text has no cap here (L98)
MAX_DIGITS = 15          # more integer digits than any amount of an invoice: a guard, and it keeps Decimal inside its precision
_WHITESPACE = " \t "
_CURRENCY = re.compile(r"(?:US\$|U\$S|\$|USD|ARS|PES|DOL)", re.IGNORECASE)
_AMOUNT = re.compile(r"(-?)([0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)(?:,([0-9]{1,2}))?")
_QUANTITY = re.compile(r"([0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)(?:,([0-9]{1,3}))?")
_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_DAY_FIRST = re.compile(r"([0-9]{1,2})[/-]([0-9]{1,2})[/-]([0-9]{4})")
_CUIT_SEPARATORS = re.compile(r"[-.\s]")
_ELEVEN_DIGITS = re.compile(r"[0-9]{11}")
_DIGITS = re.compile(r"[0-9]+")


def _clean(text: Any, limit: int | None = None) -> str | None:
    """The text trimmed, or None when it is not a text, is empty or (with a `limit`) is longer than that."""
    if not isinstance(text, str):
        return None
    text = text.strip(_WHITESPACE)
    return text if text and (limit is None or len(text) <= limit) else None


def parse_amount(text: Any) -> Decimal | None:
    """``1.234,56`` -> ``Decimal("1234.56")``; None when the text is not an amount in the printed form (see the module's text)."""
    text = _clean(text, MAX_TEXT)
    if text is None:
        return None
    text = _CURRENCY.sub("", text, count=1).strip(_WHITESPACE) if _CURRENCY.match(text) else text
    match = _AMOUNT.fullmatch(text)
    if match is None:
        return None
    sign, whole, cents = match.groups()
    if len(whole.replace(".", "")) > MAX_DIGITS:
        return None
    value = Decimal(f"{whole.replace('.', '')}.{cents or '0'}").quantize(CENT, rounding=ROUND_HALF_UP)
    return -value if sign else value


def parse_quantity(text: Any) -> Decimal | None:
    """``12`` -> ``Decimal("12")``, ``2,5`` -> ``Decimal("2.5")``, ``1.000`` -> ``Decimal("1000")``; None otherwise (up to three decimals, no sign)."""
    text = _clean(text, MAX_TEXT)
    match = _QUANTITY.fullmatch(text) if text is not None else None
    if match is None:
        return None
    whole, decimals = match.groups()
    if len(whole.replace(".", "")) > MAX_DIGITS:
        return None
    return Decimal(f"{whole.replace('.', '')}.{decimals}") if decimals else Decimal(whole.replace(".", ""))


def parse_date(text: Any) -> date | None:
    """``14/10/2026`` or ``2026-10-14`` -> ``date(2026, 10, 14)``; None when it is not one of those or not a day of the calendar."""
    text = _clean(text, MAX_TEXT)
    if text is None:
        return None
    try:
        if (match := _ISO_DATE.fullmatch(text)) is not None:
            year, month, day = match.groups()
        elif (match := _DAY_FIRST.fullmatch(text)) is not None:
            day, month, year = match.groups()
        else:
            return None
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def parse_cuit(text: Any) -> str | None:
    """Eleven digits, hyphens, dots and spaces dropped: ``30-00001234-5`` -> ``30000012345``; None otherwise. The check digit is not looked at."""
    text = _clean(text, MAX_TEXT)
    if text is None:
        return None
    digits = _CUIT_SEPARATORS.sub("", text)
    return digits if _ELEVEN_DIGITS.fullmatch(digits) else None


def _integer(text: Any) -> int | None:
    text = _clean(text, MAX_TEXT)
    return int(text) if text is not None and _DIGITS.fullmatch(text) else None


def _text(value: Any) -> str | None:
    """A free text (a description, a code, a company name, a currency, the order number, the CAE): trimmed, never cut or dropped for its length."""
    return _clean(value)


@dataclass(frozen=True)
class LineFields:
    codigo: str | None
    descripcion: str | None
    cantidad: Decimal | None
    unidad: str | None
    precio_unitario: Decimal | None
    importe: Decimal | None


@dataclass(frozen=True)
class InvoiceFields:
    tipo: int | None = None
    pto_vta: int | None = None
    nro: int | None = None
    fecha: date | None = None
    cuit_emisor: str | None = None
    razon_social: str | None = None
    cuit_receptor: str | None = None
    orden_compra: str | None = None
    moneda: str | None = None
    cae: str | None = None
    lines: tuple[LineFields, ...] = ()
    neto: Decimal | None = None
    iva: Decimal | None = None
    iva_rate: Decimal | None = None
    total: Decimal | None = None
    missing: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Nothing the match needs is missing (``extraction_incomplete`` is the opposite)."""
        return not self.missing


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _line(raw: Any) -> LineFields:
    raw = _mapping(raw)
    return LineFields(_text(raw.get("codigo")), _text(raw.get("descripcion")), parse_quantity(raw.get("cantidad")), _text(raw.get("unidad")),
                      parse_amount(raw.get("precio_unitario")), parse_amount(raw.get("importe")))


def normalize(raw: Any) -> InvoiceFields:
    """The typed fields of the model's tree `raw` (``header``, ``lines``, ``totals`` of printed texts). Anything else, or a part that is not what it should be, gives empty fields and
    the names of what is missing; it never raises."""
    tree = _mapping(raw)
    header, totals = _mapping(tree.get("header")), _mapping(tree.get("totals"))
    raw_lines = tree.get("lines")
    lines = tuple(_line(item) for item in raw_lines) if isinstance(raw_lines, (list, tuple)) else ()
    missing: list[str] = [] if lines else ["lines"]
    for number, line in enumerate(lines):
        for name in ("cantidad", "precio_unitario", "importe"):
            if getattr(line, name) is None:
                missing.append(f"lines[{number}].{name}")
        if line.codigo is None and line.descripcion is None:
            missing.append(f"lines[{number}].codigo")
    neto, total = parse_amount(totals.get("neto")), parse_amount(totals.get("total"))
    missing += [f"totals.{name}" for name, value in (("neto", neto), ("total", total)) if value is None]
    iva21, iva105 = parse_amount(totals.get("iva_21")), parse_amount(totals.get("iva_105"))
    if (iva21 is None) == (iva105 is None):      # neither, or both: not a reading of the IVA
        iva, rate = None, None
    else:
        iva, rate = (iva21, Decimal("21")) if iva21 is not None else (iva105, Decimal("10.5"))
    order = _text(header.get("orden_compra"))
    return InvoiceFields(
        tipo=_integer(header.get("tipo")), pto_vta=_integer(header.get("pto_vta")), nro=_integer(header.get("nro")), fecha=parse_date(header.get("fecha")),
        cuit_emisor=parse_cuit(header.get("cuit_emisor")), razon_social=_text(header.get("razon_social")), cuit_receptor=parse_cuit(header.get("cuit_receptor")),
        orden_compra=order.upper() if order is not None and re.fullmatch(r"(?i)oc-[0-9]{4}-[0-9]{6}", order) else order, moneda=_text(header.get("moneda")),
        cae=_text(header.get("cae")), lines=lines, neto=neto, iva=iva, iva_rate=rate, total=total, missing=tuple(missing),
    )
