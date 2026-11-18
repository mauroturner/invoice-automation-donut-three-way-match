"""The plan of deliveries and their manifest (U07; RF06; plan sections 3 and 6, decision D22): a launch, one delivery per business day of the test and a close, each with
its identifier and its ``as_of``, and the manifest that says which were published, written atomically."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime

import pytest

from invoiceflow.deliveries import DeliverySpec, delivery_plan, next_pending, read_manifest, write_manifest


def test_the_default_plan_is_a_launch_twenty_business_days_and_a_close(view):
    plan = delivery_plan(view)
    assert len(plan) == 22 and [s.kind for s in plan] == ["launch"] + ["daily"] * 20 + ["close"]
    assert plan[0] == DeliverySpec("L2026-09-30", "launch", date(2026, 9, 30), datetime(2026, 10, 1))
    assert plan[1] == DeliverySpec("D2026-10-01", "daily", date(2026, 10, 1), datetime(2026, 10, 2))
    assert plan[-2] == DeliverySpec("D2026-10-28", "daily", date(2026, 10, 28), datetime(2026, 10, 29))
    assert plan[-1] == DeliverySpec("C2026-10-29", "close", date(2026, 10, 29), datetime(2026, 10, 29))
    days = [s.day for s in plan if s.kind == "daily"]
    assert all(d.weekday() < 5 for d in days) and days == sorted(set(days)) and len({s.delivery_id for s in plan}) == 22
    assert datetime(2026, 10, 3) in {s.as_of for s in plan if s.day == date(2026, 10, 2)} or date(2026, 10, 2).weekday() == 4, "a Friday's as_of is the next midnight"
    friday = next(s for s in plan if s.day == date(2026, 10, 2))
    assert friday.as_of == datetime(2026, 10, 3), "00:00 of the following day, a Saturday included"


def test_the_quick_plan_has_its_three_days(quick_view):
    assert [s.delivery_id for s in delivery_plan(quick_view)] == ["L2026-09-30", "D2026-10-01", "D2026-10-02", "D2026-10-05", "C2026-10-06"]
    assert delivery_plan(quick_view)[-1].as_of == datetime(2026, 10, 6)


def test_weekends_are_days_when_the_calendar_says_so(quick_view):
    weekend = dataclasses.replace(quick_view, calendar=dataclasses.replace(quick_view.calendar, weekdays_only=False))
    assert [s.day.isoformat() for s in delivery_plan(weekend) if s.kind == "daily"] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]


def test_the_next_pending_delivery_is_the_first_not_loaded(quick_view):
    plan = delivery_plan(quick_view)
    assert next_pending({"version": 1, "deliveries": {}}, plan) == plan[0]
    loaded = {"version": 1, "deliveries": {s.delivery_id: {"status": "loaded"} for s in plan[:2]}}
    assert next_pending(loaded, plan) == plan[2]
    published = {"version": 1, "deliveries": {plan[0].delivery_id: {"status": "loaded"}, plan[1].delivery_id: {"status": "published"}}}
    assert next_pending(published, plan) == plan[1], "a published delivery that was never loaded is still the one that comes next"
    gap = {"version": 1, "deliveries": {plan[2].delivery_id: {"status": "loaded"}}}
    assert next_pending(gap, plan) == plan[0], "a later one loaded does not skip an earlier one"
    done = {"version": 1, "deliveries": {s.delivery_id: {"status": "loaded"} for s in plan}}
    assert next_pending(done, plan) is None


def test_the_manifest_is_written_atomically_with_a_version_and_read_back(tmp_path):
    path = tmp_path / "deliveries" / "manifest.json"
    assert read_manifest(path) == {"version": 1, "deliveries": {}}, "no file, no deliveries"
    manifest = {"version": 1, "deliveries": {"L2026-09-30": {"status": "published", "files": {"a.png": {"bytes": 3, "sha256": "0" * 64}}}}}
    write_manifest(path, manifest)
    assert read_manifest(path) == manifest and json.loads(path.read_text(encoding="utf-8")) == manifest
    assert path.read_text(encoding="utf-8").endswith("\n") and [p.name for p in path.parent.iterdir()] == ["manifest.json"]
    first = path.read_bytes()
    write_manifest(path, manifest)
    assert path.read_bytes() == first, "the same manifest, the same bytes"


@pytest.mark.parametrize("text", ["{no", "[]", '{"version": 2, "deliveries": {}}', '{"version": 1}', '{"version": 1, "deliveries": []}', '{"version": 1, "deliveries": {}, "x": 1}'])
def test_a_manifest_that_is_not_the_exact_shape_is_refused(tmp_path, text):
    path = tmp_path / "manifest.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="manifiesto"):
        read_manifest(path)


def test_writing_a_manifest_of_another_shape_is_refused_and_leaves_the_file(tmp_path):
    path = tmp_path / "manifest.json"
    write_manifest(path, {"version": 1, "deliveries": {}})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="manifiesto"):
        write_manifest(path, {"version": 3, "deliveries": {}})
    assert path.read_bytes() == before
