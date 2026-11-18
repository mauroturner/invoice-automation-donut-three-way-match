"""The master of the synthetic ERP (U05, AC04; plan section 7.1, decisions D04, D05, D22): suppliers, items, a buyer, purchase orders and the receipts that
follow them, all deterministic from the seed, all with CUIT in the synthetic range, sized from the configuration so that every invoice that will be drawn
has an order."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from invoiceflow.synth.cuit import is_synthetic_cuit, non_synthetic_cuits
from invoiceflow.synth.master import Master, fingerprint, master, to_jsonable

PO_NUMBER = re.compile(r"OC-2026-\d{6}")
RECEIPT_NUMBER = re.compile(r"REM-\d{6}")


@pytest.fixture(scope="module")
def full(view):
    return master(view)


@pytest.fixture(scope="module")
def full_fingerprint(full):
    return fingerprint(full)


def days(view) -> int:
    first, last = view.calendar.test_first_day, view.calendar.test_last_day
    every = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    return sum(1 for day in every if not view.calendar.weekdays_only or day.weekday() < 5)


def with_synth(view, **changes):
    return dataclasses.replace(view, synth=dataclasses.replace(view.synth, **changes))


def test_receipts_follow_their_orders_and_the_master_is_deterministic(view, full, full_fingerprint):
    again = master(view)
    assert again == full and fingerprint(again) == full_fingerprint and re.fullmatch(r"[0-9a-f]{64}", full_fingerprint)
    assert master(with_synth(view, seed_master=12)) != full and fingerprint(master(with_synth(view, seed_master=12))) != full_fingerprint, "another seed, another master"
    orders = {po.po_number: po for po in full.orders}
    assert len(orders) == len(full.orders) and all(PO_NUMBER.fullmatch(number) for number in orders)
    received: dict[str, dict[str, Decimal]] = {number: {} for number in orders}
    per_order: dict[str, list] = {number: [] for number in orders}
    for receipt in full.receipts:
        po = orders[receipt.po_number]
        assert po.po_date + timedelta(days=1) <= receipt.receipt_date <= po.po_date + timedelta(days=10), receipt.receipt_number
        assert RECEIPT_NUMBER.fullmatch(receipt.receipt_number) and receipt.lines
        ordered = {line.item_code: line.qty for line in po.lines}
        for line in receipt.lines:
            assert line.item_code in ordered and line.qty > 0 and line.qty == line.qty.to_integral_value() and isinstance(line.qty, Decimal)
            received[po.po_number][line.item_code] = received[po.po_number].get(line.item_code, Decimal(0)) + line.qty
        per_order[po.po_number].append(receipt)
    for number, po in orders.items():
        assert received[number] == {line.item_code: line.qty for line in po.lines}, f"{number}: what was received is exactly what was ordered"
        assert 1 <= len(per_order[number]) <= 2
        dates = [r.receipt_date for r in per_order[number]]
        assert dates == sorted(dates) and len(set(dates)) == len(dates)
    assert len({r.receipt_number for r in full.receipts}) == len(full.receipts)
    assert [r.receipt_number for r in full.receipts] == sorted(r.receipt_number for r in full.receipts), "numbered in the order they arrive"
    assert [r.receipt_date for r in full.receipts] == sorted(r.receipt_date for r in full.receipts)


def test_most_orders_arrive_whole_and_the_rest_in_two_receipts(full):
    counts = {}
    for receipt in full.receipts:
        counts[receipt.po_number] = counts.get(receipt.po_number, 0) + 1
    assert set(counts) == {po.po_number for po in full.orders}, "every order has a receipt"
    single = sum(1 for n in counts.values() if n == 1) / len(counts)
    assert 0.55 <= single <= 0.65, "60 % whole (plan 7.1)"


def test_the_suppliers_of_each_cohort_have_their_layouts_and_fonts_and_the_numbers_of_the_plan(view, full):
    synth = view.synth
    by_cohort = {c: [s for s in full.suppliers if s.cohort == c] for c in ("known", "new_validation", "new_test")}
    assert [len(by_cohort[c]) for c in by_cohort] == [synth.suppliers_known, synth.suppliers_new_validation, synth.suppliers_new_test] == [40, 8, 15]
    assert [s.supplier_key for s in full.suppliers] == [f"S{n:03d}" for n in range(1, 64)]
    assert {s.layout for s in by_cohort["known"]} == set(synth.layouts_known) and {s.font for s in by_cohort["known"]} == set(synth.fonts_known)
    assert {(s.layout, s.font) for s in by_cohort["new_validation"]} == {(synth.layout_validation_new, synth.font_validation_new)}
    assert {(s.layout, s.font) for s in by_cohort["new_test"]} == {(synth.layout_test_new, synth.font_test_new)}
    names = [s.name_es for s in full.suppliers]
    assert len(set(names)) == 63 and all(re.search(r"\((ficticio|ficticia)\)$", name) for name in names)
    cuits = [s.cuit for s in full.suppliers] + [full.buyer.cuit]
    assert len(set(cuits)) == 64 and all(is_synthetic_cuit(cuit) for cuit in cuits)
    low, high = synth.cuit_serial_range
    assert all(low <= int(cuit[6:10]) <= high for cuit in cuits), "serials drawn from the configured range"
    for supplier in full.suppliers:
        assert supplier.iva_condition == "Responsable Inscripto" and 1 <= len(supplier.points_of_sale) <= 3
        assert list(supplier.points_of_sale) == sorted(set(supplier.points_of_sale)) and all(1 <= p <= 99999 for p in supplier.points_of_sale)
    assert full.buyer.name_es == "Comprador Ejemplo (ficticio) S.A." and full.buyer.cuit not in {s.cuit for s in full.suppliers}


def test_the_items_are_150_with_a_unit_and_a_list_price_in_pesos(full):
    assert len(full.items) == 150 and [i.code for i in full.items] == [f"ART-{n:04d}" for n in range(1, 151)]
    assert len({i.description_es for i in full.items}) == 150
    assert {i.unit for i in full.items} <= {"u", "caja", "kg", "l", "m"}
    for item in full.items:
        assert isinstance(item.list_price, Decimal) and item.list_price > 0 and item.list_price == item.list_price.quantize(Decimal("0.01"))


def test_the_orders_are_numbered_by_date_and_have_distinct_lines_in_the_item_list(view, full):
    codes = {item.code for item in full.items}
    keys = [(po.po_date, po.supplier_key) for po in full.orders]
    assert keys == sorted(keys) and [po.po_number for po in full.orders] == [f"OC-2026-{n:06d}" for n in range(1, len(full.orders) + 1)]
    suppliers = {s.supplier_key for s in full.suppliers}
    for po in full.orders:
        assert po.supplier_key in suppliers and po.currency in ("PES", "DOL") and 1 <= len(po.lines) <= min(8, view.synth.max_lines)
        assert len({line.item_code for line in po.lines}) == len(po.lines) and {line.item_code for line in po.lines} <= codes
        assert po.po_date.weekday() < 5
        for line in po.lines:
            assert isinstance(line.qty, Decimal) and 1 <= line.qty <= 50 and line.qty == line.qty.to_integral_value()
            assert isinstance(line.unit_price, Decimal) and line.unit_price >= Decimal("0.01") and line.unit_price == line.unit_price.quantize(Decimal("0.01"))
    dol = sum(po.currency == "DOL" for po in full.orders) / len(full.orders)
    assert abs(dol - view.synth.usd_share) < 0.02, "the share of orders in dollars follows usd_share"
    items = {item.code: item for item in full.items}
    for po in full.orders[:300]:
        for line in po.lines:
            reference = items[line.item_code].list_price / (Decimal(1000) if po.currency == "DOL" else Decimal(1))
            assert Decimal("0.94") * reference - Decimal("0.01") <= line.unit_price <= Decimal("1.06") * reference + Decimal("0.01"), "within 5 % of the list price"
            assert line.unit == items[line.item_code].unit and line.description_es == items[line.item_code].description_es


def test_every_invoice_that_will_be_drawn_has_an_order_in_its_window(view, full):
    synth, calendar = view.synth, view.calendar
    cohort = {s.supplier_key: s.cohort for s in full.suppliers}
    history = lambda po: calendar.history_start <= po.po_date <= calendar.launch_last_day  # noqa: E731
    test = lambda po: calendar.test_first_day - timedelta(days=14) <= po.po_date <= calendar.test_last_day - timedelta(days=1)  # noqa: E731
    known = [po for po in full.orders if cohort[po.supplier_key] == "known"]
    new_validation = [po for po in full.orders if cohort[po.supplier_key] == "new_validation"]
    new_test = [po for po in full.orders if cohort[po.supplier_key] == "new_test"]
    assert all(history(po) or test(po) for po in known) and all(history(po) for po in new_validation) and all(test(po) for po in new_test)
    n = days(view)
    assert n == 20
    assert sum(history(po) for po in known) >= synth.n_train + synth.n_validation_known
    assert sum(test(po) for po in known) >= n * synth.test_per_day_known
    assert len(new_validation) >= synth.n_validation_new and len(new_test) >= n * synth.test_per_day_new
    for supplier in full.suppliers:
        assert any(po.supplier_key == supplier.supplier_key for po in full.orders), supplier.supplier_key
    # at least one and a half orders per invoice of the cohort, and not much more than that (the ceiling of each supplier's share is all that is added)
    for orders, needed, suppliers in ((known, synth.n_train + synth.n_validation_known + n * synth.test_per_day_known, synth.suppliers_known),
                                      (new_validation, synth.n_validation_new, synth.suppliers_new_validation),
                                      (new_test, n * synth.test_per_day_new, synth.suppliers_new_test)):
        assert 1.5 * needed <= len(orders) <= 1.5 * needed + 2 * suppliers
    assert any(po.po_date < calendar.test_first_day for po in new_test), "the orders of the test days start before the first one"


def test_the_events_are_the_orders_and_the_receipts_by_date_the_order_first(full):
    assert len(full.events) == len(full.orders) + len(full.receipts)
    keys = [(event.date, 0 if event.kind == "po" else 1, event.document.po_number if event.kind == "po" else event.document.receipt_number) for event in full.events]
    assert keys == sorted(keys)
    assert all((event.date == event.document.po_date) if event.kind == "po" else (event.date == event.document.receipt_date) for event in full.events)
    assert {event.document.po_number for event in full.events if event.kind == "po"} == {po.po_number for po in full.orders}


def test_no_cuit_in_the_whole_master_is_outside_the_synthetic_range(full):
    text = json.dumps(to_jsonable(full), ensure_ascii=False)
    assert non_synthetic_cuits(text) == []
    assert full.buyer.cuit in text and all(s.cuit in text for s in full.suppliers)


def test_the_master_reads_the_generator_and_the_calendar_only(view, full, full_fingerprint):
    other = dataclasses.replace(view, model=dataclasses.replace(view.model, epochs=5, candidate_epochs=(1, 5)), match=dataclasses.replace(view.match, price_tolerance_pct=9.0))
    assert master(other) == full and fingerprint(master(other)) == full_fingerprint
    shifted = dataclasses.replace(view, calendar=dataclasses.replace(view.calendar, test_last_day=date(2026, 10, 7)))
    assert fingerprint(master(shifted)) != full_fingerprint
    fewer = master(with_synth(view, n_train=100))
    assert len(fewer.orders) < len(full.orders) and [s.cuit for s in fewer.suppliers] == [s.cuit for s in full.suppliers], "the suppliers do not move when only the sizes do"


def test_the_quick_master_is_small_and_has_the_same_shape(quick_view):
    small = master(quick_view)
    assert isinstance(small, Master) and len(small.suppliers) == 63 and len(small.items) == 150
    assert 0 < len(small.orders) < 1500 and non_synthetic_cuits(json.dumps(to_jsonable(small))) == []
    assert days(quick_view) == 3


def test_a_range_with_too_few_serials_with_a_cuit_is_refused(view):
    with pytest.raises(ValueError, match=r"cuit_serial_range"):
        master(with_synth(view, cuit_serial_range=(1, 64)))   # 64 serials, and some of them have no CUIT (the remainder is 10): fewer than the 64 numbers needed


def test_the_fingerprint_changes_with_any_price_quantity_date_or_name(full, full_fingerprint):
    base = full_fingerprint
    po = full.orders[0]
    line = dataclasses.replace(po.lines[0], unit_price=po.lines[0].unit_price + Decimal("0.01"))
    changed = dataclasses.replace(full, orders=(dataclasses.replace(po, lines=(line, *po.lines[1:])), *full.orders[1:]))
    assert fingerprint(changed) != base
    assert fingerprint(dataclasses.replace(full, orders=(dataclasses.replace(po, po_date=po.po_date + timedelta(days=1)), *full.orders[1:]))) != base
    assert fingerprint(dataclasses.replace(full, buyer=dataclasses.replace(full.buyer, name_es="Otro (ficticio) S.A."))) != base


def test_the_fingerprint_covers_the_suppliers_the_items_the_receipts_and_the_buyer_too(full, full_fingerprint):
    supplier = full.suppliers[0]
    for change in (dataclasses.replace(supplier, cuit=full.buyer.cuit), dataclasses.replace(supplier, layout="L9"), dataclasses.replace(supplier, font="X"),
                   dataclasses.replace(supplier, points_of_sale=(99,)), dataclasses.replace(supplier, name_es="Otra (ficticia)"),
                   dataclasses.replace(supplier, cohort="new_test"), dataclasses.replace(supplier, iva_condition="Monotributo")):
        assert fingerprint(dataclasses.replace(full, suppliers=(change, *full.suppliers[1:]))) != full_fingerprint
    item = full.items[0]
    for change in (dataclasses.replace(item, list_price=item.list_price + Decimal("0.01")), dataclasses.replace(item, unit="kg"),
                   dataclasses.replace(item, description_es="otro")):
        assert fingerprint(dataclasses.replace(full, items=(change, *full.items[1:]))) != full_fingerprint
    receipt = full.receipts[0]
    line = receipt.lines[0]
    for change in (dataclasses.replace(receipt, receipt_date=receipt.receipt_date + timedelta(days=1)), dataclasses.replace(receipt, po_number="OC-2026-999999"),
                   dataclasses.replace(receipt, lines=(dataclasses.replace(line, qty=line.qty + 1), *receipt.lines[1:])),
                   dataclasses.replace(receipt, lines=(dataclasses.replace(line, item_code="ART-0001"), *receipt.lines[1:]))):
        assert fingerprint(dataclasses.replace(full, receipts=(change, *full.receipts[1:]))) != full_fingerprint
    po = full.orders[0]
    more = dataclasses.replace(po.lines[0], qty=po.lines[0].qty + 1)
    assert fingerprint(dataclasses.replace(full, orders=(dataclasses.replace(po, lines=(more, *po.lines[1:])), *full.orders[1:]))) != full_fingerprint
    for change in (dataclasses.replace(po, currency="DOL" if po.currency == "PES" else "PES"), dataclasses.replace(po, supplier_key="S999"),
                   dataclasses.replace(po, po_number="OC-2026-999999")):
        assert fingerprint(dataclasses.replace(full, orders=(change, *full.orders[1:]))) != full_fingerprint
    assert fingerprint(dataclasses.replace(full, buyer=dataclasses.replace(full.buyer, cuit=full.suppliers[0].cuit))) != full_fingerprint


def test_every_cuit_is_checked_by_the_guard_of_the_writers_as_it_is_made(view, monkeypatch):
    from invoiceflow.synth import master as module

    seen: list[tuple[str, str]] = []
    real = module.require_synthetic_cuit
    monkeypatch.setattr(module, "require_synthetic_cuit", lambda cuit, where: (seen.append((cuit, where)), real(cuit, where))[1])
    made = module.master(view)
    assert {cuit for cuit, _ in seen} == {s.cuit for s in made.suppliers} | {made.buyer.cuit} and len(seen) == 64 and {where for _, where in seen} == {"synth.master"}


def test_each_aspect_of_the_master_draws_from_its_own_stream():
    from invoiceflow.synth.master import _stream

    firsts = {aspect: _stream(11, aspect).random() for aspect in ("cuit", "names", "points_of_sale", "items", "orders", "receipts")}
    assert len(set(firsts.values())) == 6 and _stream(11, "cuit").random() == firsts["cuit"] != _stream(12, "cuit").random()

