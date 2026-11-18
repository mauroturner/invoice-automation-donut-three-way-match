"""The partitions of the synthetic invoices and their guards (U06; AC06, D10, D21): the test is generated only after the launch seal, a design, a font or a
supplier reserved for the test never appears before it, no voucher, image or order is shared between partitions, and a day is generated alone."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, timedelta

import pytest

from invoiceflow.registry import config_fingerprint
from invoiceflow.synth.invoices import GenerationRefusedError, invoices
from invoiceflow.synth.render import render
from tests.fixtures.seals import write_launch_seal

SPLITS = ("train", "validation_known", "validation_new", "test_known", "test_new")
TEST_SPLITS = ("test_known", "test_new")
FIT_SPLITS = ("train", "validation_known", "validation_new")


def calendar_days(view) -> list[date]:
    first, last = view.calendar.test_first_day, view.calendar.test_last_day
    return [d for n in range((last - first).days + 1) if (d := first + timedelta(days=n)).weekday() < 5]


@pytest.fixture(scope="module")
def sealed(quick_view, tmp_path_factory):
    return write_launch_seal(tmp_path_factory.mktemp("seal") / "model_seal.json", quick_view)


@pytest.fixture(scope="module")
def everything(quick_view, quick_master, sealed):
    """Every partition of the quick configuration: split -> truths (the test ones whole, after the seal)."""
    return {split: invoices(quick_view, quick_master, split, None, seal_path=sealed) for split in SPLITS}


def test_generating_the_test_without_the_launch_seal_is_refused(quick_view, quick_master, tmp_path):
    day = calendar_days(quick_view)[0]
    for split in TEST_SPLITS:
        missing = tmp_path / "missing" / "model_seal.json"
        with pytest.raises(GenerationRefusedError, match="sello"):
            invoices(quick_view, quick_master, split, day, seal_path=missing)
        assert not missing.parent.exists(), "a refusal writes nothing"
        for text, label in ((json.dumps({}), "no launch part"), (json.dumps({"test": {"config_sha256": "0" * 64}}), "only a test part"),
                            (json.dumps({"launch": {"config_sha256": "0" * 64}}), "another configuration"), ("{not json", "an unreadable seal"),
                            (json.dumps({"launch": {}}), "a launch without its fingerprint"), (json.dumps([1]), "a list")):
            seal = tmp_path / "seal.json"
            seal.write_text(text, encoding="utf-8")
            with pytest.raises(GenerationRefusedError, match="sello"):
                invoices(quick_view, quick_master, split, day, seal_path=seal)
            assert seal.read_text(encoding="utf-8") == text, label
    assert len(invoices(quick_view, quick_master, "test_known", day, seal_path=write_launch_seal(tmp_path / "ok.json", quick_view))) == quick_view.synth.test_per_day_known


def test_the_seal_of_another_configuration_refuses_every_key_that_is_fingerprinted(quick_view, quick_master, tmp_path):
    seal = write_launch_seal(tmp_path / "seal.json", quick_view)
    changed = dataclasses.replace(quick_view, synth=dataclasses.replace(quick_view.synth, seed_test=quick_view.synth.seed_test + 1))
    assert config_fingerprint(changed) != config_fingerprint(quick_view)
    with pytest.raises(GenerationRefusedError, match="otra configuración"):
        invoices(changed, quick_master, "test_new", calendar_days(quick_view)[0], seal_path=seal)
    wrong_sources = dataclasses.replace(quick_view, cord_sha256_seen=tuple("1" * 64 for _ in quick_view.cord_sha256_seen))
    with pytest.raises(GenerationRefusedError, match="otra configuración"):
        invoices(wrong_sources, quick_master, "test_known", calendar_days(quick_view)[0], seal_path=seal)


def test_the_training_and_the_validations_never_need_the_seal(quick_view, quick_master, tmp_path):
    for split in FIT_SPLITS:
        assert invoices(quick_view, quick_master, split, None, seal_path=tmp_path / "none.json"), split
    assert not (tmp_path / "none.json").exists()


def test_a_day_and_a_split_that_do_not_go_together_are_refused(quick_view, quick_master, sealed):
    for split in FIT_SPLITS:
        with pytest.raises(ValueError, match="día"):
            invoices(quick_view, quick_master, split, date(2026, 10, 1), seal_path=sealed)
    for day in (date(2026, 9, 30), date(2026, 10, 3), date(2026, 10, 6)):
        with pytest.raises(ValueError, match="día de prueba"):
            invoices(quick_view, quick_master, "test_known", day, seal_path=sealed)
    with pytest.raises(ValueError, match="partición"):
        invoices(quick_view, quick_master, "dev", None, seal_path=sealed)


def test_no_test_new_layout_font_or_cuit_appears_before_the_test(quick_view, quick_master, everything):
    """Everything a test_new invoice uses that the fit could memorise: its design, its font, its supplier and its CUIT (D10, AC06)."""
    before = [t for split in FIT_SPLITS for t in everything[split]]
    reserved = [s for s in quick_master.suppliers if s.cohort == "new_test"]
    assert reserved and {s.layout for s in reserved} == {quick_view.synth.layout_test_new} and {s.font for s in reserved} == {quick_view.synth.font_test_new}
    for truth in before:
        assert truth.layout != quick_view.synth.layout_test_new and truth.font != quick_view.synth.font_test_new
        assert truth.supplier_key not in {s.supplier_key for s in reserved} and truth.header.cuit_emisor not in {s.cuit for s in reserved}
    assert {t.layout for t in everything["test_new"]} == {quick_view.synth.layout_test_new}
    assert {t.font for t in everything["test_new"]} == {quick_view.synth.font_test_new}
    assert {t.supplier_key for t in everything["test_new"]} <= {s.supplier_key for s in reserved}
    assert {t.layout for t in everything["validation_new"]} == {quick_view.synth.layout_validation_new} != {quick_view.synth.layout_test_new}
    assert {t.font for t in everything["validation_new"]} == {quick_view.synth.font_validation_new} != {quick_view.synth.font_test_new}
    assert {t.layout for t in everything["train"] + everything["validation_known"] + everything["test_known"]} <= set(quick_view.synth.layouts_known)
    assert {t.font for t in everything["train"] + everything["validation_known"] + everything["test_known"]} <= set(quick_view.synth.fonts_known)


def test_the_new_suppliers_of_the_validation_and_of_the_test_are_not_the_known_ones(quick_view, everything):
    known = {t.supplier_key for split in ("train", "validation_known", "test_known") for t in everything[split]}
    assert not known & {t.supplier_key for t in everything["validation_new"]} and not known & {t.supplier_key for t in everything["test_new"]}
    assert not {t.supplier_key for t in everything["validation_new"]} & {t.supplier_key for t in everything["test_new"]}


def test_no_voucher_image_hash_or_order_is_shared_between_partitions(quick_view, everything):
    owner: dict[tuple, str] = {}
    pages: dict[str, str] = {}
    orders: dict[str, str] = {}
    for split, truths in everything.items():
        for truth in truths:
            voucher = (truth.header.cuit_emisor, truth.header.pto_vta, truth.header.tipo, truth.header.nro)
            assert owner.setdefault(voucher, split) == split, (split, voucher)
            digest = hashlib.sha256(render(truth, quick_view)).hexdigest()
            assert pages.setdefault(digest, truth.invoice_key) == truth.invoice_key, "two pages are never the same file"
            if truth.planted != "duplicate":
                assert orders.setdefault(truth.po_number, split) == split, (split, truth.po_number)
    numbers = [truth.header.nro for split in SPLITS for truth in everything[split] if truth.planted != "duplicate"]
    assert len(numbers) == len(set(numbers)), "no voucher number repeats between partitions, whoever the supplier"
    assert [len(everything[s]) for s in FIT_SPLITS] == [quick_view.synth.n_train, quick_view.synth.n_validation_known, quick_view.synth.n_validation_new]
    days = len(calendar_days(quick_view))
    assert len(everything["test_known"]) == days * quick_view.synth.test_per_day_known and len(everything["test_new"]) == days * quick_view.synth.test_per_day_new


def test_an_order_is_invoiced_once_across_all_partitions_in_the_default_configuration(view, default_master, tmp_path):
    seal = write_launch_seal(tmp_path / "seal.json", view)
    owner: dict[str, str] = {}
    for split in SPLITS:
        for truth in invoices(view, default_master, split, None, seal_path=seal):
            if truth.planted != "duplicate":
                assert owner.setdefault(truth.po_number, split) == split, (split, truth.po_number)
            assert truth.split == split


def test_each_day_is_generated_alone_and_equals_its_part_of_the_whole(quick_view, quick_master, sealed):
    for split, per_day in (("test_known", quick_view.synth.test_per_day_known), ("test_new", quick_view.synth.test_per_day_new)):
        whole = invoices(quick_view, quick_master, split, None, seal_path=sealed)
        days = calendar_days(quick_view)
        assert len(whole) == per_day * len(days)
        for index, day in enumerate(days):
            part = invoices(quick_view, quick_master, split, day, seal_path=sealed)
            assert part == whole[index * per_day:(index + 1) * per_day] and len(part) == per_day
            assert all(t.received_on == day and t.split == split for t in part)
            assert all(t.header.fecha == day or t.planted in ("date_before_po", "duplicate") for t in part)
        assert [t.invoice_key for t in whole] == sorted(t.invoice_key for t in whole) and len({t.invoice_key for t in whole}) == len(whole)


def test_a_day_depends_on_its_own_date_and_the_seed_never_on_the_other_days(view, default_master, tmp_path):
    seal = write_launch_seal(tmp_path / "seal.json", view)
    day = date(2026, 10, 14)
    alone = invoices(view, default_master, "test_known", day, seal_path=seal)
    assert alone == invoices(view, default_master, "test_known", day, seal_path=seal)
    assert [t.received_on for t in alone] == [day] * view.synth.test_per_day_known
    another = invoices(view, default_master, "test_known", date(2026, 10, 15), seal_path=seal)
    assert {t.header.nro for t in alone}.isdisjoint({t.header.nro for t in another})


def test_the_duplicates_come_from_an_earlier_day_and_never_from_the_first_one(view, default_master, tmp_path):
    view = dataclasses.replace(view, synth=dataclasses.replace(view.synth, planted_rate=0.07))   # about 40 duplicates: a source of the same day would show
    seal = write_launch_seal(tmp_path / "seal.json", view)
    whole = invoices(view, default_master, "test_known", None, seal_path=seal) + invoices(view, default_master, "test_new", None, seal_path=seal)
    first_day = calendar_days(view)[0]
    by_voucher = {}
    for truth in whole:
        by_voucher.setdefault((truth.split, truth.header.cuit_emisor, truth.header.pto_vta, truth.header.tipo, truth.header.nro), []).append(truth)
    duplicates = [t for t in whole if t.planted == "duplicate"]
    assert duplicates, "600 invoices at 2.3 % per class: some duplicates"
    for truth in duplicates:
        assert truth.received_on > first_day
        source = next(t for t in by_voucher[(truth.split, truth.header.cuit_emisor, truth.header.pto_vta, truth.header.tipo, truth.header.nro)] if t is not truth)
        assert source.planted == "none" and source.received_on < truth.received_on
