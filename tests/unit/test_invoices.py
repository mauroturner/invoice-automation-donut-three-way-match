"""The synthetic invoices and their pages (U06; AC04, AC05, AC07 in part; plan sections 7.2 and 7.3, decisions D04, D05, D10): the same seed gives the same bytes,
every page says it is synthetic, the five layouts print every field the model has to read, the truth is what the page shows and arithmetic closes, the QR of
each page decodes to its exact text, and each planted discrepancy is what the plan's table says and comes out at its rate."""

from __future__ import annotations

import base64
import dataclasses
import io
import json
import math
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pytest
from PIL import Image

from invoiceflow.synth import printed
from invoiceflow.synth.cuit import SyntheticCuitError, is_synthetic_cuit
from invoiceflow.synth.invoices import FIRST_REASON, PLANTED_CLASSES, InvoiceTruth, invoices
from invoiceflow.synth.master import master as make_master
from invoiceflow.synth.render import NOTICE, draw_page, render
from tests.fixtures.pages import decode_qr_pyzbar

LAYOUTS = ("L1", "L2", "L3", "L4", "L5")


@pytest.fixture(scope="module")
def quick_train(quick_view, quick_master, tmp_path_factory):
    return invoices(quick_view, quick_master, "train", None, seal_path=tmp_path_factory.mktemp("seal") / "none.json")


@pytest.fixture(scope="module")
def big_train(view, default_master, tmp_path_factory):
    """The 2,000 invoices of the default training partition (truth only, no page): where the rates and the classes are measured."""
    return invoices(view, default_master, "train", None, seal_path=tmp_path_factory.mktemp("seal") / "none.json")


def with_synth(view, **changes):
    return dataclasses.replace(view, synth=dataclasses.replace(view.synth, **changes))


def orders_of(master):
    return {po.po_number: po for po in master.orders}


def receipts_of(master):
    by_po: dict[str, list] = {}
    for receipt in master.receipts:
        by_po.setdefault(receipt.po_number, []).append(receipt)
    return by_po


def qr_json(truth: InvoiceTruth) -> dict:
    assert truth.qr_text is not None
    return json.loads(base64.b64decode(truth.qr_text.split("?p=")[1]), parse_float=Decimal)


def test_same_seed_same_bytes_and_every_page_says_synthetic(quick_view, quick_master, quick_train, tmp_path):
    again = invoices(quick_view, quick_master, "train", None, seal_path=tmp_path / "none.json")
    assert again == quick_train and len(quick_train) == quick_view.synth.n_train, "the same seed gives the same truth"
    digests = []
    for truth in quick_train:
        first, second = render(truth, quick_view), render(truth, quick_view)
        assert first == second, truth.invoice_key
        digests.append(first)
    assert len(set(digests)) == len(digests), "no two invoices have the same page"
    other = invoices(with_synth(quick_view, seed_train=102), quick_master, "train", None, seal_path=tmp_path / "none.json")
    assert other != quick_train and [render(t, with_synth(quick_view, seed_train=102)) for t in other[:4]] != digests[:4]
    for truth in quick_train[:6]:
        for layout in LAYOUTS:
            page = draw_page(dataclasses.replace(truth, layout=layout), quick_view)
            notices = [box for text, box in page.texts if text == NOTICE]
            assert len(notices) == 1, (truth.invoice_key, layout, "the legend is on the page exactly once")
            left, top, right, bottom = notices[0]
            assert np.asarray(page.image.crop((left, top, right, bottom))).min() < 128, "and it is drawn with ink, not only recorded"
            assert page.image.size == (round(210 / 25.4 * quick_view.synth.dpi), round(297 / 25.4 * quick_view.synth.dpi)) and page.image.mode == "L"
    assert NOTICE == "DOCUMENTO SINTÉTICO — SIN VALIDEZ FISCAL"


def test_each_file_format_is_what_it_says_and_holds_one_page(quick_view, quick_train):
    seen = set()
    for truth in quick_train:
        data = render(truth, quick_view)
        seen.add(truth.file_format)
        if truth.file_format == "png":
            assert data[:8] == b"\x89PNG\r\n\x1a\n" and Image.open(io.BytesIO(data)).format == "PNG"
        elif truth.file_format == "jpeg":
            assert data[:3] == b"\xff\xd8\xff" and Image.open(io.BytesIO(data)).format == "JPEG"
        else:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            assert data[:5] == b"%PDF-" and len(reader.pages) == 1 and len(reader.pages[0].images) == 1 and reader.pages[0].extract_text() == ""
            assert b"/JS" not in data and b"/EmbeddedFile" not in data and b"/Font" not in data, "an image only: no script, no attachment, no text"
    assert seen == {"png", "jpeg", "pdf"}


def test_every_layout_prints_every_field_the_model_has_to_read(quick_view, quick_train):
    for truth in quick_train[:5]:
        h = truth.header
        for layout in LAYOUTS:
            page = draw_page(dataclasses.replace(truth, layout=layout), quick_view)
            text = "\n".join(t for t, _ in page.texts)
            expected = [h.razon_social, printed.cuit(h.cuit_emisor), printed.cuit(h.cuit_receptor), printed.pto_vta(h.pto_vta), printed.nro(h.nro),
                        printed.date_es(h.fecha), h.cae, printed.amount(truth.totals.neto), printed.amount(truth.totals.iva), printed.amount(truth.totals.total)]
            if h.orden_compra:
                expected.append(h.orden_compra)
            for line in truth.lines:
                expected += [line.code, line.description, printed.quantity(line.qty), line.unit, printed.amount(line.unit_price), printed.amount(line.amount)]
            for needle in expected:
                assert needle in text, (layout, truth.invoice_key, needle)
            assert ("21%" in text) == (truth.totals.iva_rate == Decimal("21")) and ("10,5%" in text) == (truth.totals.iva_rate == Decimal("10.5"))


def test_the_layouts_differ_from_each_other_in_what_the_model_sees(quick_view, quick_train):
    truth = quick_train[0]
    pages = {layout: np.asarray(draw_page(dataclasses.replace(truth, layout=layout), quick_view).image) for layout in LAYOUTS}
    for first in LAYOUTS:
        for second in LAYOUTS:
            if first < second:
                assert (pages[first] != pages[second]).mean() > 0.01, (first, second)
    fonts = {font: np.asarray(draw_page(dataclasses.replace(truth, font=font), quick_view).image) for font in ("DejaVuSans", "DejaVuSerif", "DejaVuSansMono-Bold")}
    assert (fonts["DejaVuSans"] != fonts["DejaVuSerif"]).any() and (fonts["DejaVuSans"] != fonts["DejaVuSansMono-Bold"]).any()


def test_the_truth_is_what_the_page_shows_and_the_arithmetic_closes(big_train):
    cent = Decimal("0.01")
    for truth in big_train:
        if truth.planted not in ("none", "price_off", "over_order", "over_receipt"):
            continue
        for line in truth.lines:
            assert line.amount == (line.qty * line.unit_price).quantize(cent, ROUND_HALF_UP) and line.qty > 0 and line.unit_price > 0
        totals = truth.totals
        assert totals.neto == sum(line.amount for line in truth.lines) and totals.iva == (totals.neto * totals.iva_rate / 100).quantize(cent, ROUND_HALF_UP)
        assert totals.total == totals.neto + totals.iva and totals.iva_rate in (Decimal("21"), Decimal("10.5"))
        assert truth.header.tipo == 1 and truth.header.cae.isdigit() and len(truth.header.cae) == 14 and truth.header.cae_vto > truth.header.fecha
        assert 1 <= len(truth.lines) <= 12


def test_each_qr_decodes_to_its_exact_text_and_says_what_the_page_says(quick_view, quick_train):
    view = with_synth(quick_view, dpi=200)
    checked = 0
    for truth in quick_train[:14]:
        page = draw_page(truth, view)
        if truth.qr_text is None:
            assert decode_qr_pyzbar(page.image) == []
            continue
        assert decode_qr_pyzbar(page.image) == [truth.qr_text], truth.invoice_key
        payload, h = qr_json(truth), truth.header
        assert payload["ver"] == 1 and payload["cuit"] == int(h.cuit_emisor) and payload["ptoVta"] == h.pto_vta and payload["nroCmp"] == h.nro
        assert payload["fecha"] == h.fecha.isoformat() and payload["tipoCmp"] == h.tipo and payload["moneda"] == h.moneda and Decimal(str(payload["ctz"])) == h.cotizacion
        assert payload["tipoDocRec"] == 80 and payload["nroDocRec"] == int(h.cuit_receptor) and payload["tipoCodAut"] == "E" and payload["codAut"] == int(h.cae)
        assert Decimal(str(payload["importe"])) == truth.totals.total or truth.planted == "qr_tampered"
        checked += 1
    assert checked >= 10


def test_a_generated_cuit_outside_the_synthetic_range_is_refused_before_anything_is_drawn(quick_view, quick_master, tmp_path):
    suppliers = list(quick_master.suppliers)
    suppliers[0] = dataclasses.replace(suppliers[0], cuit="20123456786")
    bad = dataclasses.replace(quick_master, suppliers=tuple(suppliers))
    with pytest.raises(SyntheticCuitError):
        invoices(quick_view, bad, "train", None, seal_path=tmp_path / "none.json")
    bad_buyer = dataclasses.replace(quick_master, buyer=dataclasses.replace(quick_master.buyer, cuit="20123456786"))
    with pytest.raises(SyntheticCuitError):
        invoices(quick_view, bad_buyer, "train", None, seal_path=tmp_path / "none.json")


def test_every_cuit_of_every_invoice_and_every_qr_is_synthetic(big_train):
    for truth in big_train:
        h = truth.header
        assert is_synthetic_cuit(h.cuit_emisor) and is_synthetic_cuit(h.cuit_receptor)
        if truth.qr_text:
            payload = qr_json(truth)
            assert is_synthetic_cuit(str(payload["cuit"])) and is_synthetic_cuit(str(payload["nroDocRec"]))


def test_the_thirteen_classes_and_their_first_reasons_are_the_plans_table():
    assert PLANTED_CLASSES == ("price_off", "over_receipt", "over_order", "total_mismatch", "supplier_mismatch", "no_po", "duplicate", "date_before_po",
                               "currency_mismatch", "extra_line", "qr_missing", "qr_tampered", "credit_note")
    assert FIRST_REASON == {
        "price_off": ("price_out_of_tolerance",), "over_receipt": ("qty_over_receipt",), "over_order": ("qty_over_receipt", "qty_over_order"),
        "total_mismatch": ("total_arithmetic",), "supplier_mismatch": ("supplier_mismatch",), "no_po": ("no_po",), "duplicate": ("duplicate",),
        "date_before_po": ("date_out_of_range",), "currency_mismatch": ("currency_mismatch",), "extra_line": ("line_not_on_po",),
        "qr_missing": ("qr_not_read",), "qr_tampered": ("qr_print_mismatch",), "credit_note": ("unsupported_type",),
    }


def test_each_planted_class_is_what_the_table_says_and_carries_its_expected_decision(big_train, default_master):
    orders, receipts = orders_of(default_master), receipts_of(default_master)
    by_class: dict[str, list[InvoiceTruth]] = {name: [] for name in ("none", *PLANTED_CLASSES)}
    for truth in big_train:
        by_class[truth.planted].append(truth)
    assert all(by_class[name] for name in PLANTED_CLASSES), {name: len(items) for name, items in by_class.items()}
    for name, items in by_class.items():
        for truth in items:
            assert truth.expected_decision == ("auto_post" if name == "none" else "review")
            assert truth.expected_reasons == (() if name == "none" else FIRST_REASON[name])
    for truth in by_class["none"]:
        po = orders[truth.po_number]
        assert truth.header.orden_compra == po.po_number and truth.header.moneda == po.currency and truth.header.fecha == truth.received_on
        assert [(line.code, line.qty, line.unit_price) for line in truth.lines] == [(line.item_code, line.qty, line.unit_price) for line in po.lines]
        assert po.po_date <= truth.header.fecha and truth.supplier_key == po.supplier_key
        arrived = {}
        for receipt in receipts[po.po_number]:
            assert receipt.receipt_date <= truth.received_on, "an invoice that is right is invoiced when the whole order was received"
            for line in receipt.lines:
                arrived[line.item_code] = arrived.get(line.item_code, 0) + line.qty
        assert arrived == {line.item_code: line.qty for line in po.lines}
        payload = qr_json(truth)
        assert Decimal(str(payload["importe"])) == truth.totals.total and payload["tipoCmp"] == 1
    for truth in by_class["price_off"]:
        po = orders[truth.po_number]
        changed = [(line, order) for line, order in zip(truth.lines, po.lines, strict=True) if line.unit_price != order.unit_price]
        assert len(changed) == 1 and changed[0][0].unit_price > changed[0][1].unit_price
        assert (changed[0][0].unit_price - changed[0][1].unit_price) / changed[0][1].unit_price * 100 >= 3
        assert truth.header.orden_compra == po.po_number
    for truth in by_class["over_receipt"]:
        po = orders[truth.po_number]
        due = receipts[po.po_number]
        assert len(due) == 2 and due[0].receipt_date <= truth.received_on < due[1].receipt_date, "the second receipt had not arrived on the day"
        assert [(line.code, line.qty) for line in truth.lines] == [(line.item_code, line.qty) for line in po.lines]
    for truth in by_class["over_order"]:
        po = orders[truth.po_number]
        assert any(line.qty > order.qty for line, order in zip(truth.lines, po.lines, strict=True)) and len(truth.lines) == len(po.lines)
    for truth in by_class["total_mismatch"]:
        assert truth.totals.neto != sum(line.amount for line in truth.lines) and truth.totals.total == truth.totals.neto + truth.totals.iva
        assert Decimal(str(qr_json(truth)["importe"])) == truth.totals.total, "the QR carries the printed total"
    for truth in by_class["supplier_mismatch"]:
        cited = orders[truth.header.orden_compra]
        assert cited.supplier_key != truth.supplier_key and orders[truth.po_number].supplier_key == truth.supplier_key
    for truth in by_class["no_po"]:
        assert truth.header.orden_compra is None or truth.header.orden_compra not in orders
    for truth in by_class["date_before_po"]:
        assert truth.header.fecha < orders[truth.po_number].po_date and qr_json(truth)["fecha"] == truth.header.fecha.isoformat()
    for truth in by_class["currency_mismatch"]:
        assert truth.header.moneda == "DOL" and orders[truth.po_number].currency == "PES" and truth.header.cotizacion > 1 and qr_json(truth)["moneda"] == "DOL"
        for line, order in zip(truth.lines, orders[truth.po_number].lines, strict=True):
            assert line.unit_price == max((order.unit_price / 1000).quantize(Decimal("0.01"), ROUND_HALF_UP), Decimal("0.01")), "a dollar invoice prices an item at a thousandth of its peso price"
    for truth in by_class["extra_line"]:
        po_codes = {line.item_code for line in orders[truth.po_number].lines}
        assert [line.code for line in truth.lines if line.code not in po_codes] and len(truth.lines) > len(orders[truth.po_number].lines)
    for truth in by_class["qr_missing"]:
        assert truth.qr_text is None
    for truth in by_class["qr_tampered"]:
        assert Decimal(str(qr_json(truth)["importe"])) != truth.totals.total
    for truth in by_class["credit_note"]:
        assert truth.header.tipo == 3 and qr_json(truth)["tipoCmp"] == 3
    firsts = {}
    for truth in big_train:
        firsts.setdefault((truth.split, truth.header.cuit_emisor, truth.header.pto_vta, truth.header.tipo, truth.header.nro), []).append(truth)
    for truth in by_class["duplicate"]:
        siblings = firsts[(truth.split, truth.header.cuit_emisor, truth.header.pto_vta, truth.header.tipo, truth.header.nro)]
        assert len(siblings) == 2 and {t.planted for t in siblings} == {"none", "duplicate"}
        source = next(t for t in siblings if t.planted == "none")
        assert source.invoice_key < truth.invoice_key and source.lines == truth.lines and source.qr_text == truth.qr_text and source.header == truth.header


def test_no_other_comprobante_repeats_inside_a_partition(big_train):
    keys = Counter((t.header.cuit_emisor, t.header.pto_vta, t.header.tipo, t.header.nro) for t in big_train)
    repeated = [key for key, count in keys.items() if count > 1]
    assert len(repeated) == sum(1 for t in big_train if t.planted == "duplicate"), "only the planted duplicates share a comprobante"


def test_each_class_comes_out_at_its_rate_within_a_written_tolerance(big_train, view):
    """The tolerance, written: a class drawn with probability p over n invoices is within 4.5 standard deviations of n p (a binomial; the chance of a miss
    is about 7 in a million per class). The two classes that need something of the world (a duplicate needs an earlier day; an invoice partly
    received needs a two-receipt order) may fall short, never above, and by at most 30 %."""
    n, p = len(big_train), view.synth.planted_rate
    counts = Counter(t.planted for t in big_train)
    sigma = math.sqrt(n * p * (1 - p))
    for name in PLANTED_CLASSES:
        low = n * p - 4.5 * sigma
        if name in ("duplicate", "over_receipt"):
            assert 0.7 * n * p <= counts[name] <= n * p + 4.5 * sigma, (name, counts[name])
        else:
            assert low <= counts[name] <= n * p + 4.5 * sigma, (name, counts[name], n * p)
    planted = sum(counts[name] for name in PLANTED_CLASSES)
    assert 0.8 * 13 * n * p <= planted <= 13 * n * p + 4.5 * math.sqrt(13 * n * p), planted
    assert counts["none"] == n - planted


def test_the_other_draws_come_out_at_their_shares(big_train, view):
    n, synth = len(big_train), view.synth
    for field, shares in (("scan_profile", dataclasses.asdict(synth.scan_profiles)), ("file_format", dataclasses.asdict(synth.file_formats))):
        counts = Counter(getattr(t, field) for t in big_train)
        assert set(counts) == set(shares)
        for key, share in shares.items():
            assert abs(counts[key] / n - share) <= 4.5 * math.sqrt(share * (1 - share) / n), (field, key)
    assert Counter(t.qr_cm for t in big_train) and set(t.qr_cm for t in big_train) == set(synth.qr_cm)
    plain = [t for t in big_train if t.planted not in ("currency_mismatch",)]
    dol = sum(1 for t in plain if t.header.moneda == "DOL") / len(plain)
    assert abs(dol - synth.usd_share) <= 0.03, dol
    iva105 = sum(1 for t in plain if t.totals.iva_rate == Decimal("10.5")) / len(plain)
    assert abs(iva105 - synth.iva105_share) <= 0.03, iva105


def test_the_issuer_comes_from_the_master_and_the_layout_and_font_are_the_suppliers(big_train, default_master):
    suppliers = {s.supplier_key: s for s in default_master.suppliers}
    for truth in big_train[:300]:
        supplier = suppliers[truth.supplier_key]
        assert (truth.header.cuit_emisor, truth.header.razon_social, truth.layout, truth.font) == (supplier.cuit, supplier.name_es, supplier.layout, supplier.font)
        assert truth.header.cuit_receptor == default_master.buyer.cuit and truth.header.pto_vta in supplier.points_of_sale
        assert truth.split == "train" and truth.cohort == "known" and truth.invoice_key.startswith("train-")


def test_a_scan_profile_keeps_a_clean_page_and_spoils_the_others_in_their_own_way(quick_view, quick_train):
    from invoiceflow.synth.render import scan

    page = draw_page(quick_train[0], quick_view).image
    import random

    clean, quality = scan(page, "clean", random.Random(1))
    assert quality is None and np.array_equal(np.asarray(clean), np.asarray(page))
    mild, mild_quality = scan(page, "mild", random.Random(1))
    poor, poor_quality = scan(page, "poor", random.Random(1))
    assert 60 <= mild_quality <= 85 and 35 <= poor_quality <= 50
    assert mild.size == poor.size == page.size
    distance = lambda image: float(np.abs(np.asarray(image, dtype=float) - np.asarray(page, dtype=float)).mean())  # noqa: E731
    assert 0 < distance(mild) < distance(poor)
    with pytest.raises(ValueError, match="perfil"):
        scan(page, "other", random.Random(1))


def test_a_week_of_daily_pages_is_the_same_page_in_any_order(quick_view, quick_train):
    first = [render(t, quick_view) for t in quick_train[:5]]
    second = [render(t, quick_view) for t in reversed(quick_train[:5])]
    assert first == second[::-1], "a page depends on its own invoice and the seed only, never on what was drawn before"


def test_the_seed_of_the_split_picks_the_stream(view, default_master, tmp_path):
    a = invoices(view, default_master, "validation_known", None, seal_path=tmp_path / "x.json")
    b = invoices(with_synth(view, seed_validation=203), default_master, "validation_known", None, seal_path=tmp_path / "x.json")
    c = invoices(with_synth(view, seed_train=999), default_master, "validation_known", None, seal_path=tmp_path / "x.json")
    assert a != b and a == c, "validation reads seed_validation and not seed_train"
    assert len(a) == view.synth.n_validation_known
    assert timedelta(0) <= max(t.received_on for t in a) - min(t.received_on for t in a)


def test_the_scan_noise_depends_on_the_seed_of_the_split_and_on_the_invoice(quick_view, quick_train):
    truth = dataclasses.replace(quick_train[0], scan_profile="mild", file_format="png")      # the quick mode draws clean pages only (D56): the profile is the test's
    base = render(truth, quick_view)
    assert render(truth, with_synth(quick_view, seed_train=quick_view.synth.seed_train + 1)) != base, "another seed, another noise"
    assert render(dataclasses.replace(truth, invoice_key="train-99999"), quick_view) != base, "another invoice, another noise"
    assert render(truth, with_synth(quick_view, seed_validation=quick_view.synth.seed_validation + 1)) == base, "a training page does not read the validation's seed"
    assert render(dataclasses.replace(truth, scan_profile="clean"), quick_view) != base


def test_a_partly_received_order_fits_an_over_receipt_between_its_two_receipts_only():
    from invoiceflow.synth.invoices import _fits, _OrderFacts

    order = _OrderFacts(None, date(2026, 10, 5), date(2026, 10, 8), 2)
    assert [_fits("over_receipt", order, date(2026, 10, d)) for d in (4, 5, 6, 7, 8, 9)] == [False, True, True, True, False, False]
    assert [_fits("none", order, date(2026, 10, d)) for d in (7, 8, 9)] == [False, True, True], "a right invoice waits for the last receipt, the same day included"
    one = _OrderFacts(None, date(2026, 10, 5), date(2026, 10, 5), 1)
    assert not _fits("over_receipt", one, date(2026, 10, 5)) and _fits("none", one, date(2026, 10, 5))


def test_a_poor_scan_flips_one_pixel_in_two_hundred_and_a_mild_one_flips_none():
    import random

    from invoiceflow.synth.render import scan

    white = Image.new("L", (400, 400), 255)
    poor, _ = scan(white, "poor", random.Random(3))
    dark = (np.asarray(poor) < 128).mean()
    assert 0.003 < dark < 0.008, dark
    mild, _ = scan(white, "mild", random.Random(3))
    assert (np.asarray(mild) < 128).sum() == 0
