"""The QR of an invoice (U07; AC07, AC08; RF10; plan section 8, decisions D06, D07): the specification's example reads, each malformed variant is ``qr_invalid`` with its
reason and never repeats the text, pyzbar reads first and Aruco only when pyzbar returned nothing, and a page with no QR is ``not_read``."""

from __future__ import annotations

import base64
import dataclasses
import json
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image

from invoiceflow import qr
from invoiceflow.qr import QrInvalid, QrPayload, QrRead, parse_qr_text, read_qr
from invoiceflow.synth.invoices import invoices
from invoiceflow.synth.render import draw_page
from tests.fixtures import hand_cases as h

REASONS = {
    "foreign_host": "host", "foreign_path": "path", "not_https": "scheme", "two_parameters": "query", "no_parameter": "query", "broken_base64": "base64",
    "json_is_a_list": "not_object", "other_version": "version", "extra_field": "fields", "missing_field": "fields", "wrong_type": "type:ptoVta",
    "bad_cuit_digit": "cuit_check_digit", "too_long": "too_long",
}


def url_of(payload: dict, host: str = "www.arca.gob.ar") -> str:
    return f"https://{host}/fe/qr/?p=" + base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


@pytest.fixture(scope="module")
def spec_payload() -> dict:
    return json.loads(h.SPEC_EXAMPLE_JSON)


def test_the_arca_example_reproduces_byte_for_byte(quick_view):
    payload = parse_qr_text(h.SPEC_EXAMPLE_URL, quick_view)
    assert payload == QrPayload(1, date(2020, 10, 13), "30000000007", 10, 1, 94, Decimal("12100"), "DOL", Decimal("65"), 80, 20000000001, "E", 70417054367476)
    assert parse_qr_text(h.SPEC_EXAMPLE_URL_ARCA, quick_view) == payload, "both hosts of the specification are accepted"
    from invoiceflow.synth.qrpayload import AFIP_HOST, SPEC_EXAMPLE_PAYLOAD, qr_text

    assert qr_text(SPEC_EXAMPLE_PAYLOAD, AFIP_HOST) == h.SPEC_EXAMPLE_URL, "and what the reader reads is what the writer writes"


def test_each_malformed_qr_gives_qr_invalid(quick_view):
    variants = h.malformed_qr_texts()
    assert set(variants) == set(REASONS)
    for name, text in variants.items():
        with pytest.raises(QrInvalid) as error:
            parse_qr_text(text, quick_view)
        assert error.value.reason == REASONS[name], name
        assert text[-30:] not in str(error.value) and "eyJ" not in str(error.value), "the reason never holds the text"


def test_the_size_is_checked_before_anything_is_parsed(quick_view, monkeypatch):
    monkeypatch.setattr(qr, "urlsplit", lambda *a, **k: pytest.fail("parsed an oversize text"))
    with pytest.raises(QrInvalid) as error:
        parse_qr_text("x" * (quick_view.qr.max_text_bytes + 1), quick_view)
    assert error.value.reason == "too_long"
    monkeypatch.undo()
    padded = "https://www.arca.gob.ar/fe/qr/?p=" + "A" * (quick_view.qr.max_text_bytes - len("https://www.arca.gob.ar/fe/qr/?p="))
    with pytest.raises(QrInvalid) as at_limit:
        parse_qr_text(padded, quick_view)
    assert at_limit.value.reason != "too_long", "a text of exactly the limit is not too long"
    multibyte = "ñ" * (quick_view.qr.max_text_bytes // 2 + 1)
    with pytest.raises(QrInvalid) as wide:
        parse_qr_text(multibyte, quick_view)
    assert wide.value.reason == "too_long", "the limit counts bytes, not characters"


@pytest.mark.parametrize(("url", "reason"), [
    ("http://www.arca.gob.ar/fe/qr/?p=AAAA", "scheme"), ("ftp://www.arca.gob.ar/fe/qr/?p=AAAA", "scheme"), ("www.arca.gob.ar/fe/qr/?p=AAAA", "scheme"),
    ("https://www.arca.gob.ar:8443/fe/qr/?p=AAAA", "host"), ("https://user@www.arca.gob.ar/fe/qr/?p=AAAA", "host"), ("https://www.arca.gob.ar.evil.com/fe/qr/?p=AAAA", "host"),
    ("https://evil.com/www.arca.gob.ar/fe/qr/?p=AAAA", "host"), ("https://WWW.ARCA.GOB.AR/fe/qr/?p=AAAA", "json"),
    ("https://www.arca.gob.ar/fe/qr?p=AAAA", "path"), ("https://www.arca.gob.ar/fe/qr/x?p=AAAA", "path"), ("https://www.arca.gob.ar/fe/qr/?p=AAAA#x", "path"),
    ("https://www.arca.gob.ar/fe/qr/", "query"), ("https://www.arca.gob.ar/fe/qr/?p=", "json"), ("https://www.arca.gob.ar/fe/qr/?P=AAAA", "query"),
    ("https://www.arca.gob.ar/fe/qr/?p=AAAA&p=AAAA", "query"), ("https://www.arca.gob.ar/fe/qr/?p=AA AA", "base64"), ("https://www.arca.gob.ar/fe/qr/?p=AAA", "base64"),
    ("https://www.arca.gob.ar/fe/qr/?p=AA==AA", "base64"), ("https://[::1/fe/qr/?p=AAAA", "url"),
])
def test_the_url_is_checked_part_by_part(quick_view, url, reason):
    with pytest.raises(QrInvalid) as error:
        parse_qr_text(url, quick_view)
    assert error.value.reason == reason


def test_the_hosts_and_the_path_are_the_configured_ones(quick_view):
    other = dataclasses.replace(quick_view, qr=dataclasses.replace(quick_view.qr, hosts=("qr.example.test",), path="/x/"))
    with pytest.raises(QrInvalid) as error:
        parse_qr_text(h.SPEC_EXAMPLE_URL, other)
    assert error.value.reason == "host"
    text = h.SPEC_EXAMPLE_URL.replace("www.afip.gob.ar/fe/qr/", "qr.example.test/x/")
    assert parse_qr_text(text, other).nro_cmp == 94
    with pytest.raises(QrInvalid) as wrong_path:
        parse_qr_text(h.SPEC_EXAMPLE_URL.replace("www.afip.gob.ar", "qr.example.test"), other)
    assert wrong_path.value.reason == "path"


@pytest.mark.parametrize(("edits", "reason"), [
    ({"ver": True}, "version"), ({"ver": 1.0}, "version"), ({"ver": "1"}, "version"),
    ({"fecha": "2020-13-01"}, "value:fecha"), ({"fecha": "2020-1-1"}, "type:fecha"), ({"fecha": 20201013}, "type:fecha"), ({"fecha": "2020-10-13T00:00:00"}, "type:fecha"),
    ({"cuit": "30000000007"}, "type:cuit"), ({"cuit": 3000000000}, "value:cuit"), ({"cuit": 300000000070}, "value:cuit"), ({"cuit": 30000000008}, "cuit_check_digit"),
    ({"cuit": 30000000007.0}, "type:cuit"), ({"ptoVta": 0}, "value:ptoVta"), ({"ptoVta": 100000}, "value:ptoVta"), ({"ptoVta": True}, "type:ptoVta"),
    ({"tipoCmp": 1000}, "value:tipoCmp"), ({"tipoCmp": 0}, "value:tipoCmp"), ({"nroCmp": 100000000}, "value:nroCmp"), ({"nroCmp": -1}, "value:nroCmp"),
    ({"importe": "12100"}, "type:importe"), ({"importe": -1}, "value:importe"), ({"importe": 12100.123}, "value:importe"), ({"importe": 10**13}, "value:importe"),
    ({"importe": None}, "type:importe"), ({"moneda": "DO"}, "value:moneda"), ({"moneda": "dol"}, "value:moneda"), ({"moneda": 3}, "type:moneda"),
    ({"ctz": 0}, "value:ctz"), ({"ctz": 1.1234567}, "value:ctz"), ({"ctz": "1"}, "type:ctz"),
    ({"tipoDocRec": 100}, "value:tipoDocRec"), ({"nroDocRec": 10**20}, "value:nroDocRec"), ({"tipoDocRec": "80"}, "type:tipoDocRec"),
    ({"tipoCodAut": "X"}, "value:tipoCodAut"), ({"tipoCodAut": 1}, "type:tipoCodAut"), ({"codAut": 1234567890123}, "value:codAut"), ({"codAut": 10**14}, "value:codAut"),
    ({"ver": 2}, "version"),
])
def test_each_field_is_checked_for_its_type_and_its_size(quick_view, spec_payload, edits, reason):
    with pytest.raises(QrInvalid) as error:
        parse_qr_text(url_of({**spec_payload, **edits}), quick_view)
    assert error.value.reason == reason


def test_the_numbers_at_their_edges_are_accepted(quick_view, spec_payload):
    edge = {**spec_payload, "importe": 9999999999999.99, "ctz": 1.123456, "ptoVta": 99999, "nroCmp": 99999999, "tipoCmp": 999, "tipoDocRec": 0, "nroDocRec": 10**20 - 1,
            "codAut": 10**14 - 1}
    payload = parse_qr_text(url_of(edge), quick_view)
    assert payload.importe == Decimal("9999999999999.99") and payload.ctz == Decimal("1.123456") and payload.nro_doc_rec == 10**20 - 1
    without = {k: v for k, v in spec_payload.items() if k not in ("tipoDocRec", "nroDocRec")}
    assert parse_qr_text(url_of(without), quick_view).tipo_doc_rec is None
    for name in ("tipoDocRec", "nroDocRec"):
        with pytest.raises(QrInvalid) as error:
            parse_qr_text(url_of({k: v for k, v in spec_payload.items() if k != name}), quick_view)
        assert error.value.reason == "fields"


def test_json_that_is_not_what_it_seems_is_refused(quick_view):
    def link(raw: bytes) -> str:
        return "https://www.arca.gob.ar/fe/qr/?p=" + base64.b64encode(raw).decode()

    duplicate = h.SPEC_EXAMPLE_JSON.replace('"ver":1,', '"ver":1,"ver":1,')
    cases = [("duplicate_key", duplicate.encode()), ("json", b"{not json"), ("json", bytes([0xFF, 0xFE])), ("not_object", b'"text"'), ("not_object", b"[" * 300 + b"]" * 300),
             ("json", b'{"ver":NaN}'), ("json", b'{"ver":Infinity}'), ("json", b"")]
    for expected, raw in cases:
        with pytest.raises(QrInvalid) as error:
            parse_qr_text(link(raw), quick_view)
        assert error.value.reason == expected, raw[:20]


# --- reading a page -------------------------------------------------------------------------------------------------------------


class Spy:
    def __init__(self, name: str, texts: list[str], calls: list[str]) -> None:
        self.name, self.texts, self.calls = name, texts, calls

    def __call__(self, image: Image.Image) -> list[str]:
        self.calls.append(self.name)
        return self.texts


def spies(quick_view, pyzbar: list[str], aruco: list[str]):
    calls: list[str] = []
    return {"pyzbar": Spy("pyzbar", pyzbar, calls), "aruco": Spy("aruco", aruco, calls)}, calls


def test_pyzbar_first_and_aruco_only_when_pyzbar_reads_nothing(quick_view):
    page = Image.new("L", (10, 10), 255)
    readers, calls = spies(quick_view, [h.SPEC_EXAMPLE_URL], [h.SPEC_EXAMPLE_URL])
    result = read_qr(page, quick_view, readers=readers)
    assert (result.reader, result.status, result.reason) == ("pyzbar", "ok", None) and result.payload.nro_cmp == 94 and calls == ["pyzbar"]
    readers, calls = spies(quick_view, [], [h.SPEC_EXAMPLE_URL])
    result = read_qr(page, quick_view, readers=readers)
    assert (result.reader, result.status) == ("aruco", "ok") and calls == ["pyzbar", "aruco"]
    readers, calls = spies(quick_view, [h.malformed_qr_texts()["foreign_host"]], [h.SPEC_EXAMPLE_URL])
    result = read_qr(page, quick_view, readers=readers)
    assert (result.reader, result.status, result.reason, result.payload) == ("pyzbar", "invalid", "host", None) and calls == ["pyzbar"], "pyzbar read something: Aruco is not asked"
    readers, calls = spies(quick_view, [], [])
    assert read_qr(page, quick_view, readers=readers) == QrRead(None, "not_read", None, None) and calls == ["pyzbar", "aruco"]


def test_the_order_of_the_readers_is_the_configured_one(quick_view):
    flipped = dataclasses.replace(quick_view, qr=dataclasses.replace(quick_view.qr, readers=("aruco", "pyzbar")))
    readers, calls = spies(quick_view, [h.SPEC_EXAMPLE_URL], [h.SPEC_EXAMPLE_URL])
    assert read_qr(Image.new("L", (4, 4)), flipped, readers=readers).reader == "aruco" and calls == ["aruco"]
    only = dataclasses.replace(quick_view, qr=dataclasses.replace(quick_view.qr, readers=("pyzbar",)))
    readers, calls = spies(quick_view, [], [h.SPEC_EXAMPLE_URL])
    assert read_qr(Image.new("L", (4, 4)), only, readers=readers).status == "not_read" and calls == ["pyzbar"]


def test_several_texts_on_a_page_the_one_that_validates_wins_and_two_that_validate_are_multiple_qr(quick_view):
    page = Image.new("L", (4, 4))
    bad = h.malformed_qr_texts()["other_version"]
    readers, _ = spies(quick_view, [bad, h.SPEC_EXAMPLE_URL, "not a qr text"], [])
    assert read_qr(page, quick_view, readers=readers).status == "ok"
    readers, _ = spies(quick_view, [bad, "not a qr text"], [])
    result = read_qr(page, quick_view, readers=readers)
    assert (result.status, result.reason) == ("invalid", "version"), "the reason of the first text that failed"
    readers, _ = spies(quick_view, [h.SPEC_EXAMPLE_URL, h.SPEC_EXAMPLE_URL_ARCA], [])
    result = read_qr(page, quick_view, readers=readers)
    assert (result.status, result.reason, result.payload) == ("invalid", "multiple_qr", None)
    readers, _ = spies(quick_view, [h.SPEC_EXAMPLE_URL, h.SPEC_EXAMPLE_URL], [])
    assert read_qr(page, quick_view, readers=readers).status == "ok", "the same text twice is one text"


@pytest.fixture(scope="module")
def clean_pages(quick_view, quick_master, tmp_path_factory):
    view = dataclasses.replace(quick_view, synth=dataclasses.replace(quick_view.synth, dpi=200, qr_cm=(4.0,)))
    truths = invoices(view, quick_master, "train", None, seal_path=tmp_path_factory.mktemp("s") / "none.json")
    return [(truth, draw_page(truth, view).image) for truth in truths[:12]]


def test_both_real_readers_read_a_clean_page_and_a_page_without_qr_is_not_read(quick_view, clean_pages):
    readable = [(t, p) for t, p in clean_pages if t.qr_text]
    assert len(readable) >= 8
    for truth, page in readable:
        by_zbar = read_qr(page, quick_view)
        assert (by_zbar.reader, by_zbar.status) == ("pyzbar", "ok"), truth.invoice_key
        assert by_zbar.payload.nro_cmp == truth.header.nro and by_zbar.payload.cuit == truth.header.cuit_emisor
        by_aruco = read_qr(page, quick_view, readers={"pyzbar": lambda image: [], "aruco": qr.read_aruco})
        assert (by_aruco.reader, by_aruco.status) == ("aruco", "ok") and by_aruco.payload == by_zbar.payload
    blank = [(t, p) for t, p in clean_pages if t.qr_text is None]
    for _truth, page in blank:
        assert read_qr(page, quick_view) == QrRead(None, "not_read", None, None)
    assert read_qr(Image.new("L", (800, 800), 255), quick_view).status == "not_read"


def test_a_tampered_qr_that_still_validates_is_read_as_what_it_says(quick_view, clean_pages):
    tampered = [(t, p) for t, p in clean_pages if t.planted == "qr_tampered"]
    for truth, page in tampered:
        assert read_qr(page, quick_view).payload.importe != truth.totals.total, "reading is not the match: the difference is qr_print_mismatch's"


def test_zbars_console_warnings_are_dropped_and_the_stream_is_restored(quick_view, clean_pages, capfd):
    import os

    truth, page = next((t, p) for t, p in clean_pages if t.qr_text)
    before = os.dup(2)
    try:
        read_qr(page, quick_view)
        os.write(2, b"after\n")
    finally:
        os.close(before)
    err = capfd.readouterr().err
    assert err.strip() == "after", "fd 2 works again after a read"
