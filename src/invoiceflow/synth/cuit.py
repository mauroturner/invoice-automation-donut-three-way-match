"""The one range of CUIT numbers this project may write (RNF12; decision D05): ``30-0000xxxx-d``.

A CUIT is eleven digits: two of type, eight of number and a check digit that the module-11 rule of AFIP gives. A number that passes the rule can belong to
a real taxpayer, so the project never makes one at random: every CUIT it produces, stores, sends to the ERP or publishes is ``300000`` + a serial of
four digits + the check digit, a prefix that no taxpayer is known to have and that ARCA's own specification uses in its example (``30000000007``, serial 0).
``synthetic_cuit`` is the only maker and refuses any serial outside 0 to 9999; ``is_synthetic_cuit`` is the guard that the writers (M07) and the test of the
published bytes use; ``non_synthetic_cuits`` finds every CUIT-looking number of a text that is not synthetic. That nobody owns a number of the range is an
assumption, not a measurement: the project asks no register (it would send data to a third party) and the owner can confirm it by sampling (plan, section 29).
"""

from __future__ import annotations

import re
from collections.abc import Collection

PREFIX = "300000"
RANGE_TEXT = "30-0000xxxx-d"
WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
MAX_SERIAL = 9999
_ASCII_DIGITS = re.compile(r"[0-9]+")
_SYNTHETIC = re.compile(r"300000[0-9]{5}")
_LOOKS_LIKE_A_CUIT = re.compile(r"(?<![0-9])([0-9]{2})-?([0-9]{8})-?([0-9])(?![0-9])")


class SyntheticCuitError(ValueError):
    """A CUIT outside the synthetic range was about to be written. The message never repeats the number."""


def check_digit(first10: str) -> int | None:
    """The check digit of the ten first digits of a CUIT (modulo 11, weights 5 4 3 2 7 6 5 4 3 2): 11 - (sum mod 11), 11 giving 0; ``None`` when it is 10,
    because no CUIT has those ten digits."""
    if not (isinstance(first10, str) and len(first10) == 10 and _ASCII_DIGITS.fullmatch(first10)):
        raise ValueError(f"se esperaban 10 dígitos ASCII, se recibió un texto de {len(first10) if isinstance(first10, str) else '?'} caracteres")
    digit = 11 - sum(int(char) * weight for char, weight in zip(first10, WEIGHTS, strict=True)) % 11
    return None if digit == 10 else 0 if digit == 11 else digit


def synthetic_cuit(serial: int) -> str | None:
    """The synthetic CUIT of a serial from 0 to 9999, or ``None`` when no CUIT has that serial (one in eleven). Nothing else makes a CUIT."""
    if isinstance(serial, bool) or not isinstance(serial, int) or not 0 <= serial <= MAX_SERIAL:
        raise ValueError(f"la serie de un CUIT sintético es un entero de 0 a {MAX_SERIAL}")
    first10 = f"{PREFIX}{serial:04d}"
    digit = check_digit(first10)
    return None if digit is None else f"{first10}{digit}"


def is_synthetic_cuit(cuit: object) -> bool:
    """Whether `cuit` is eleven ASCII digits in the range ``30-0000xxxx-d`` with the right check digit (hyphens are not accepted: normalize first)."""
    if not (isinstance(cuit, str) and _SYNTHETIC.fullmatch(cuit)):
        return False
    return check_digit(cuit[:10]) == int(cuit[10])


def require_synthetic_cuit(cuit: object, where: str) -> None:
    """The guard of every write (M07): ``SyntheticCuitError`` naming `where` when `cuit` is not synthetic. It never prints the number."""
    if not is_synthetic_cuit(cuit):
        raise SyntheticCuitError(f"{where}: un CUIT no está en el rango sintético {RANGE_TEXT} con su dígito verificador")


def non_synthetic_cuits(text: str, allowed: Collection[str] = frozenset()) -> list[str]:
    """Every CUIT-looking number of `text` (eleven digits, with or without the hyphens after the second and the tenth) that is not synthetic and not in
    `allowed`, as eleven digits, in the order found. A run of digits longer than eleven (a CAE of fourteen) holds none."""
    found = []
    for match in _LOOKS_LIKE_A_CUIT.finditer(text):
        digits = "".join(match.groups())
        if digits not in allowed and not is_synthetic_cuit(digits):
            found.append(digits)
    return found
