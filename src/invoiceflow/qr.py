"""Reading the QR of an invoice and validating what it holds (RF10; AC07, AC08; plan section 8; decisions D06, D07).

The text of a QR is untrusted input: it comes from a file anybody could have sent. ``parse_qr_text`` accepts only what ARCA's specification describes (version 1 of the
data) and says, for anything else, a short reason that never repeats what the text held:

* the text is at most ``[qr] max_text_bytes`` bytes of UTF-8 (the size is checked before anything is parsed);
* an ``https`` URL with one of ``[qr] hosts``, no port, no user, no fragment, exactly the path ``[qr] path`` and exactly one query parameter, ``p``;
* ``p`` is strict Base64 (``validate=True``: no stray characters, correct padding) of UTF-8 JSON that is an object without repeated keys;
* ``ver`` is 1 and the keys are the 13 of the specification (``tipoDocRec`` and ``nroDocRec`` may both be missing), each with its type and its size: a CUIT of 11 digits with
  a right check digit, a real calendar date, a currency of three capital letters, ``tipoCodAut`` ``A`` or ``E``, a 14-digit ``codAut``, amounts with at most two (``importe``)
  or six (``ctz``) decimals.

``read_qr`` finds the text on a page: the readers named by ``[qr] readers`` in that order, the next one only when the one before it returned nothing at all (D07). A page with no
reader's text is ``not_read``; a text that does not validate is ``invalid``; two different texts that validate are ``invalid`` (``multiple_qr``). The warnings that zbar prints to
the console are discarded; they never change a result.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit

from PIL import Image

from invoiceflow.config import SealedConfig
from invoiceflow.synth.cuit import check_digit
from invoiceflow.synth.qrpayload import FIELDS, OPTIONAL_FIELDS

Reader = Callable[[Image.Image], list[str]]
_QUERY = re.compile(r"p=([^&]*)")
_CURRENCY = re.compile(r"[A-Z]{3}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MAX_INTEGER_DIGITS = 13


class QrInvalid(ValueError):
    """The text of a QR is not what the specification describes. ``reason`` is a short code that names the rule and never holds a byte of the text."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"QR inválido: {reason}")
        self.reason = reason


@dataclass(frozen=True)
class QrPayload:
    ver: int
    fecha: date
    cuit: str                  # eleven digits
    pto_vta: int
    tipo_cmp: int
    nro_cmp: int
    importe: Decimal
    moneda: str
    ctz: Decimal
    tipo_doc_rec: int | None
    nro_doc_rec: int | None
    tipo_cod_aut: str
    cod_aut: int


@dataclass(frozen=True)
class QrRead:
    reader: str | None
    status: Literal["ok", "not_read", "invalid"]
    payload: QrPayload | None
    reason: str | None


class _Duplicate(ValueError):
    pass


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _Duplicate
    return dict(pairs)


def _no_constant(name: str) -> object:
    raise ValueError(name)


def _integer(value: object, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QrInvalid(f"type:{field}")
    if not low <= value <= high:
        raise QrInvalid(f"value:{field}")
    return value


def _number(value: object, field: str, decimals: int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise QrInvalid(f"type:{field}")
    number = Decimal(value)
    if not number.is_finite() or number < 0 or number >= Decimal(10) ** MAX_INTEGER_DIGITS or number != number.quantize(Decimal(1).scaleb(-decimals)):
        raise QrInvalid(f"value:{field}")
    return number


def parse_qr_text(text: str, view: SealedConfig) -> QrPayload:
    """The payload of the QR text `text`, or ``QrInvalid`` with the reason. Only the sealed ``[qr]`` section is read."""
    qr = view.qr
    if not isinstance(text, str):
        raise QrInvalid("type:text")
    if len(text.encode("utf-8", "surrogatepass")) > qr.max_text_bytes:
        raise QrInvalid("too_long")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise QrInvalid("url") from error
    if parts.scheme != "https":
        raise QrInvalid("scheme")
    if parts.username is not None or parts.password is not None or port is not None or parts.netloc.lower() != (parts.hostname or "") or (parts.hostname or "") not in qr.hosts:
        raise QrInvalid("host")
    if parts.path != qr.path or parts.fragment:
        raise QrInvalid("path")
    match = _QUERY.fullmatch(parts.query)
    if match is None:
        raise QrInvalid("query")
    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except (binascii.Error, ValueError) as error:
        raise QrInvalid("base64") from error
    try:
        data = json.loads(raw.decode("utf-8"), parse_float=Decimal, parse_constant=_no_constant, object_pairs_hook=_object)
    except _Duplicate as error:
        raise QrInvalid("duplicate_key") from error
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise QrInvalid("json") from error
    if not isinstance(data, dict):
        raise QrInvalid("not_object")
    return _payload(data)


def _payload(data: Mapping[str, object]) -> QrPayload:
    if type(data.get("ver")) is not int or data["ver"] != 1:
        raise QrInvalid("version")
    unknown = set(data) - set(FIELDS)
    missing = {name for name in FIELDS if name not in data and name not in OPTIONAL_FIELDS}
    if unknown or missing or (OPTIONAL_FIELDS[0] in data) != (OPTIONAL_FIELDS[1] in data):
        raise QrInvalid("fields")
    fecha = data["fecha"]
    if not (isinstance(fecha, str) and _DATE.fullmatch(fecha)):
        raise QrInvalid("type:fecha")
    try:
        day = date.fromisoformat(fecha)
    except ValueError as error:
        raise QrInvalid("value:fecha") from error
    cuit = _integer(data["cuit"], "cuit", 10**10, 10**11 - 1)
    digits = str(cuit)
    if check_digit(digits[:10]) != int(digits[10]):
        raise QrInvalid("cuit_check_digit")
    moneda = data["moneda"]
    if not isinstance(moneda, str):
        raise QrInvalid("type:moneda")
    if not _CURRENCY.fullmatch(moneda):
        raise QrInvalid("value:moneda")
    ctz = _number(data["ctz"], "ctz", 6)
    if ctz <= 0:
        raise QrInvalid("value:ctz")
    kind = data["tipoCodAut"]
    if not isinstance(kind, str):
        raise QrInvalid("type:tipoCodAut")
    if kind not in ("A", "E"):
        raise QrInvalid("value:tipoCodAut")
    return QrPayload(
        1, day, digits, _integer(data["ptoVta"], "ptoVta", 1, 99_999), _integer(data["tipoCmp"], "tipoCmp", 1, 999), _integer(data["nroCmp"], "nroCmp", 1, 99_999_999),
        _number(data["importe"], "importe", 2), moneda, ctz,
        _integer(data["tipoDocRec"], "tipoDocRec", 0, 99) if "tipoDocRec" in data else None,
        _integer(data["nroDocRec"], "nroDocRec", 0, 10**20 - 1) if "nroDocRec" in data else None,
        kind, _integer(data["codAut"], "codAut", 10**13, 10**14 - 1),
    )


# --- the readers ---------------------------------------------------------------------------------------------------------------


@contextlib.contextmanager
def _quiet_stderr() -> Iterator[None]:
    """zbar writes its assertion warnings to the C-level standard error; they are dropped for the length of a read."""
    try:
        sys.stderr.flush()
        saved = os.dup(2)
        silent = os.open(os.devnull, os.O_WRONLY)
    except (OSError, ValueError):
        yield
        return
    try:
        os.dup2(silent, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(silent)


def read_pyzbar(image: Image.Image) -> list[str]:
    """Every QR text zbar finds on the page (a gray copy of it)."""
    from pyzbar.pyzbar import ZBarSymbol, decode

    with _quiet_stderr():
        found = decode(image.convert("L"), symbols=[ZBarSymbol.QRCODE])
    texts = []
    for item in found:
        with contextlib.suppress(UnicodeDecodeError):
            texts.append(item.data.decode("utf-8"))
    return texts


def read_aruco(image: Image.Image) -> list[str]:
    """Every QR text OpenCV's Aruco-based detector finds on the page."""
    import cv2
    import numpy as np

    detector = cv2.QRCodeDetectorAruco()
    ok, texts, _points, _codes = detector.detectAndDecodeMulti(np.asarray(image.convert("L")))
    return [text for text in texts if text] if ok else []


DEFAULT_READERS: dict[str, Reader] = {"pyzbar": read_pyzbar, "aruco": read_aruco}


def read_qr(image: Image.Image, view: SealedConfig, *, readers: Mapping[str, Reader] | None = None) -> QrRead:
    """The QR of a page: the readers of ``[qr] readers`` in order, the next only when the one before returned no text at all. Among several texts, the one that validates;
    two different ones that validate are ``multiple_qr``; texts that all fail are ``invalid`` with the reason of the first."""
    available = DEFAULT_READERS if readers is None else readers
    for name in view.qr.readers:
        texts = available[name](image)
        if not texts:
            continue
        valid, first_reason = {}, None
        for text in texts:
            try:
                valid[text] = parse_qr_text(text, view)
            except QrInvalid as error:
                first_reason = first_reason or error.reason
        if len(valid) == 1:
            return QrRead(name, "ok", next(iter(valid.values())), None)
        return QrRead(name, "invalid", None, "multiple_qr" if valid else first_reason)
    return QrRead(None, "not_read", None, None)
