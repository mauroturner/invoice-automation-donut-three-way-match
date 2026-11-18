"""The text of the QR that ARCA's specification asks for (version 1 of the data; plan sections 7.2 and 8; decision D04, rule V06).

The text a QR holds is ``{URL}?p={Base64 of the compact JSON}``. The JSON has the 13 fields of the specification in the specification's order (``tipoDocRec``
and ``nroDocRec`` are written when the invoice has a receiver document, and then both); a number is written without exponent and without a trailing zero
after the decimal point (``12100``, not ``12100.00``), as the specification's own example prints it. The writer is pure: it checks the field names and no
value, so the specification's own example (whose receiver CUIT is not in the synthetic range) reproduces byte for byte. The generator guards the CUIT it
writes (``invoices.py``, M07); the validation of what a QR holds is the reader's (``qr.py``, U07).

``reproduce_spec_example`` is the second half of V06: the example of ``QRespecificaciones.pdf`` (JSON, Base64 and the URL with the host the PDF prints) must come out of
this module byte for byte, and ``sources.check_sources`` refuses the run when it does not.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from decimal import Decimal

ARCA_HOST = "www.arca.gob.ar"
AFIP_HOST = "www.afip.gob.ar"          # the host of the example in the PDF (the PDF's own definition says www.arca.gob.ar)
PATH = "/fe/qr/"
FIELDS = ("ver", "fecha", "cuit", "ptoVta", "tipoCmp", "nroCmp", "importe", "moneda", "ctz", "tipoDocRec", "nroDocRec", "tipoCodAut", "codAut")
OPTIONAL_FIELDS = ("tipoDocRec", "nroDocRec")      # written together, or both left out

SPEC_EXAMPLE_PAYLOAD = {
    "ver": 1, "fecha": "2020-10-13", "cuit": 30000000007, "ptoVta": 10, "tipoCmp": 1, "nroCmp": 94, "importe": Decimal("12100"), "moneda": "DOL",
    "ctz": Decimal("65"), "tipoDocRec": 80, "nroDocRec": 20000000001, "tipoCodAut": "E", "codAut": 70417054367476,
}
SPEC_EXAMPLE_JSON = ('{"ver":1,"fecha":"2020-10-13","cuit":30000000007,"ptoVta":10,"tipoCmp":1,"nroCmp":94,"importe":12100,"moneda":"DOL","ctz":65,'
                     '"tipoDocRec":80,"nroDocRec":20000000001,"tipoCodAut":"E","codAut":70417054367476}')
SPEC_EXAMPLE_BASE64 = ("eyJ2ZXIiOjEsImZlY2hhIjoiMjAyMC0xMC0xMyIsImN1aXQiOjMwMDAwMDAwMDA3LCJwdG9WdGEiOjEwLCJ0aXBvQ21wIjox"
                       "LCJucm9DbXAiOjk0LCJpbXBvcnRlIjoxMjEwMCwibW9uZWRhIjoiRE9MIiwiY3R6Ijo2NSwidGlwb0RvY1JlYyI6ODAsIm5yb0Rv"
                       "Y1JlYyI6MjAwMDAwMDAwMDEsInRpcG9Db2RBdXQiOiJFIiwiY29kQXV0Ijo3MDQxNzA1NDM2NzQ3Nn0=")
SPEC_EXAMPLE_TEXT = f"https://{AFIP_HOST}{PATH}?p={SPEC_EXAMPLE_BASE64}"


def number_text(value: int | Decimal) -> str:
    """A JSON number: integers as they are, a ``Decimal`` without exponent and without trailing zeros (``Decimal("12100.00")`` -> ``12100``)."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise TypeError(f"un número del QR es un entero o un Decimal, se recibió {type(value).__name__}")
    if isinstance(value, int):
        return str(value)
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def compact_json(payload: Mapping[str, object]) -> str:
    """The JSON of the specification: its fields in its order, no spaces. A field outside the specification, a missing required one, or only one of the two
    receiver fields is a ``ValueError``."""
    unknown = sorted(set(payload) - set(FIELDS))
    if unknown:
        raise ValueError(f"campos que la especificación del QR no tiene: {', '.join(unknown)}")
    missing = [name for name in FIELDS if name not in OPTIONAL_FIELDS and name not in payload]
    if missing:
        raise ValueError(f"faltan campos obligatorios del QR: {', '.join(missing)}")
    if (OPTIONAL_FIELDS[0] in payload) != (OPTIONAL_FIELDS[1] in payload):
        raise ValueError("tipoDocRec y nroDocRec se escriben juntos o no se escriben")
    parts = []
    for name in FIELDS:
        if name not in payload:
            continue
        value = payload[name]
        parts.append(f"{json.dumps(name)}:{json.dumps(value, ensure_ascii=False) if isinstance(value, str) else number_text(value)}")
    return "{" + ",".join(parts) + "}"


def qr_text(payload: Mapping[str, object], host: str = ARCA_HOST) -> str:
    """The text a QR holds: ``https://<host>/fe/qr/?p=<Base64 of the compact JSON>`` (the host of the specification's definition by default)."""
    encoded = base64.b64encode(compact_json(payload).encode("utf-8")).decode("ascii")
    return f"https://{host}{PATH}?p={encoded}"


def reproduce_spec_example() -> None:
    """V06, second half: the specification's example comes out of ``compact_json``, Base64 and ``qr_text`` byte for byte. ``ValueError`` says what differs."""
    produced = compact_json(SPEC_EXAMPLE_PAYLOAD)
    if produced != SPEC_EXAMPLE_JSON:
        raise ValueError(f"el JSON del ejemplo de la especificación no se reproduce: se obtuvo {produced!r}")
    if base64.b64encode(produced.encode("utf-8")).decode("ascii") != SPEC_EXAMPLE_BASE64:
        raise ValueError("el Base64 del ejemplo de la especificación no se reproduce")
    if qr_text(SPEC_EXAMPLE_PAYLOAD, AFIP_HOST) != SPEC_EXAMPLE_TEXT:
        raise ValueError("el texto del QR del ejemplo de la especificación no se reproduce")
