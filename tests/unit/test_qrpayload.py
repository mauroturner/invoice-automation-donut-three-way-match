"""The QR text of ARCA's specification (U06; V06 second half, AC07 in part; plan sections 7.2 and 8, decision D04): the example of the PDF reproduces byte for
byte through ``synth.qrpayload``, the numbers print as the specification prints them and the writer refuses a payload the specification does not allow."""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import pytest

from invoiceflow.synth import qrpayload
from invoiceflow.synth.qrpayload import AFIP_HOST, ARCA_HOST, FIELDS, SPEC_EXAMPLE_PAYLOAD, compact_json, number_text, qr_text, reproduce_spec_example
from tests.fixtures import hand_cases as h


def test_the_arca_example_reproduces_byte_for_byte_through_qrpayload():
    assert compact_json(SPEC_EXAMPLE_PAYLOAD) == h.SPEC_EXAMPLE_JSON
    assert base64.b64encode(compact_json(SPEC_EXAMPLE_PAYLOAD).encode()).decode() == h.SPEC_EXAMPLE_B64
    assert qr_text(SPEC_EXAMPLE_PAYLOAD, AFIP_HOST) == h.SPEC_EXAMPLE_URL, "the host the PDF prints in its example"
    assert qr_text(SPEC_EXAMPLE_PAYLOAD) == h.SPEC_EXAMPLE_URL_ARCA, "the host of the PDF's definition is the default"
    assert ARCA_HOST == "www.arca.gob.ar" and AFIP_HOST == "www.afip.gob.ar"
    assert reproduce_spec_example() is None


def test_the_constants_of_the_example_are_the_hand_cases(monkeypatch):
    assert (qrpayload.SPEC_EXAMPLE_JSON, qrpayload.SPEC_EXAMPLE_BASE64, qrpayload.SPEC_EXAMPLE_TEXT) == (h.SPEC_EXAMPLE_JSON, h.SPEC_EXAMPLE_B64, h.SPEC_EXAMPLE_URL)
    assert tuple(FIELDS) == h.QR_FIELDS
    for broken in ('{"ver":1}', h.SPEC_EXAMPLE_JSON.replace("12100", "12100.00")):
        monkeypatch.setattr(qrpayload, "SPEC_EXAMPLE_JSON", broken)
        with pytest.raises(ValueError, match="no se reproduce"):
            reproduce_spec_example()
    monkeypatch.undo()
    monkeypatch.setattr(qrpayload, "SPEC_EXAMPLE_BASE64", h.SPEC_EXAMPLE_B64[:-4] + "AAAA")
    with pytest.raises(ValueError, match="Base64"):
        reproduce_spec_example()


@pytest.mark.parametrize(("value", "text"), [
    (Decimal("12100"), "12100"), (Decimal("12100.00"), "12100"), (Decimal("1234.50"), "1234.5"), (Decimal("0.05"), "0.05"), (Decimal("1.000000"), "1"),
    (Decimal("1050.250000"), "1050.25"), (Decimal("1E+3"), "1000"), (Decimal("0.00"), "0"), (7, "7"), (30000000007, "30000000007"),
])
def test_numbers_print_without_exponent_or_trailing_zeros_as_the_specification_example_does(value, text):
    assert number_text(value) == text


@pytest.mark.parametrize("value", [1.5, "7", None, True])
def test_a_number_that_is_not_an_integer_or_a_decimal_is_refused(value):
    with pytest.raises(TypeError, match="entero o un Decimal"):
        number_text(value)


def test_the_fields_come_in_the_order_of_the_specification_whatever_the_order_given():
    shuffled = dict(reversed(list(SPEC_EXAMPLE_PAYLOAD.items())))
    assert compact_json(shuffled) == h.SPEC_EXAMPLE_JSON
    assert list(json.loads(compact_json(SPEC_EXAMPLE_PAYLOAD))) == list(FIELDS)


def test_the_receiver_may_be_left_out_but_only_as_a_pair():
    without = {k: v for k, v in SPEC_EXAMPLE_PAYLOAD.items() if k not in ("tipoDocRec", "nroDocRec")}
    assert list(json.loads(compact_json(without))) == [f for f in FIELDS if f not in ("tipoDocRec", "nroDocRec")]
    for name in ("tipoDocRec", "nroDocRec"):
        one = {k: v for k, v in SPEC_EXAMPLE_PAYLOAD.items() if k != name}
        with pytest.raises(ValueError, match="juntos"):
            compact_json(one)


def test_an_unknown_or_missing_field_is_refused_by_name():
    with pytest.raises(ValueError, match="extra"):
        compact_json({**SPEC_EXAMPLE_PAYLOAD, "extra": 1})
    for name in ("ver", "fecha", "cuit", "ptoVta", "tipoCmp", "nroCmp", "importe", "moneda", "ctz", "tipoCodAut", "codAut"):
        with pytest.raises(ValueError, match=name):
            compact_json({k: v for k, v in SPEC_EXAMPLE_PAYLOAD.items() if k != name})


def test_a_string_is_written_as_json_text():
    text = compact_json({**SPEC_EXAMPLE_PAYLOAD, "moneda": 'D"L'})
    assert json.loads(text)["moneda"] == 'D"L' and '\\"' in text
