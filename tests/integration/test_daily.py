"""The daily run (U15; RF09, RF13, RF14, RF18; D15, D17, D22, D28; AC11, AC15, AC21; risk item 1: what was issued is not redone): each invoice of a day is read, its QR is read, the ERP is asked
what it knew that day, the decision is issued and, right after it, an ``auto_post`` is posted, all in the order of the invoice keys. Nothing issued changes, a delivery of one day and one of
five days give the same decisions, and an ERP that cannot be read stops the run with nothing of that invoice written."""

from __future__ import annotations

import dataclasses
import json
from datetime import date

import httpx
import pytest

from invoiceflow import daily
from invoiceflow.baselines import oracle
from invoiceflow.erp.client import ErpAuthError, ErpClient, ErpRejected, ErpUnreachable
from tests.fixtures.daily_world import DailyWorld, build_world, decision_rows, fresh_erp, load_files, open_warehouse, snapshot, with_days
from tests.fixtures.erp_world import TOKEN


@pytest.fixture(scope="module")
def world(quick_view, tmp_path_factory) -> DailyWorld:
    return build_world(quick_view, tmp_path_factory.mktemp("daily_world"))


def read_the_printed_qr(monkeypatch, world: DailyWorld) -> None:
    """The QR of a 50 dpi page cannot be read by any reader (U07's tests read real ones at the size they are printed): the days here read the QR the generator printed."""
    monkeypatch.setattr(daily, "read_qr", world.read_qr)


def run_each_day(world: DailyWorld, conn, client, folder, *, only: set | None = None):
    results = []
    for day in world.days:
        spec = world.specs()[day]
        keys = [t.invoice_key for t in world.truths[day]]
        run, inbox = load_files(world, conn, spec, keys, folder)
        results.append(daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox))
    return results


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_each_day_issues_one_decision_per_invoice_and_never_changes_it(world, tmp_path, monkeypatch):
    read_the_printed_qr(monkeypatch, world)
    client, http = fresh_erp(world, tmp_path)
    conn = open_warehouse(tmp_path)
    results = run_each_day(world, conn, client, tmp_path)
    everything = world.all_truths()
    assert [r.day for r in results] == world.days and all(r.issued for r in results)
    assert count(conn, "decisions") == len(everything) == sum(r.invoices for r in results) and count(conn, "daily_ops") == len(world.days)
    assert count(conn, "qr_reads") == len(everything) and count(conn, "erp_posts") == sum(1 for (d,) in conn.execute("SELECT decision FROM decisions") if d == "auto_post")
    # a row per field read: the header, each line and the totals of every invoice, read as the model wrote them
    first = everything[0]
    fields = {row[0] for row in conn.execute("SELECT field FROM extracted_fields WHERE invoice_key = ?", (first.invoice_key,))}
    assert {"header.cuit_emisor", "header.nro", "header.fecha", "line.code", "line.qty", "line.unit_price", "line.amount", "totals.neto", "totals.total"} <= fields
    lines = conn.execute("SELECT count(DISTINCT ordinal) FROM extracted_fields WHERE invoice_key = ? AND field = 'line.qty'", (first.invoice_key,)).fetchone()[0]
    assert lines == len(first.lines) and conn.execute("SELECT min(ordinal) FROM extracted_fields WHERE invoice_key = ? AND field = 'line.qty'", (first.invoice_key,)).fetchone()[0] == 0
    # with the true fields the flow decides what the oracle, which never saw a page or an ERP, decides (AC21 with AC25)
    expected = oracle(world.view, world.master, everything)
    got = {key: (decision, reason) for key, decision, reason, *_ in decision_rows(conn)}
    assert got == {k: (d.decision, d.first_reason) for k, d in expected.items()}
    # the day's row counts what was issued, and every auto_post is a draft in the ERP, read back equal
    for result in results:
        row = conn.execute("SELECT invoices, accepted, quarantined, auto_post, review, posted, post_failed FROM daily_ops WHERE date_key = ?", (int(result.day.strftime("%Y%m%d")),)).fetchone()
        assert row == (result.invoices, result.invoices, 0, result.auto_post, result.review, result.posted, result.post_failed)
    posted = [r[0] for r in conn.execute("SELECT erp_name FROM erp_posts WHERE status = 'posted'")]
    assert len(posted) > 0 and all(r[0] == 1 for r in conn.execute("SELECT readback_equal FROM erp_posts")) and len(client._list("Purchase Invoice", [], ["name"])) == len(posted)
    # nothing issued is ever changed: the same day again touches no row of the warehouse and no document of the ERP
    before, erp_before = snapshot(conn), client._list("Purchase Invoice", [], ["name", "bill_no"])
    spec = world.specs()[world.days[0]]
    again = daily.run_day(conn, client, world.extractor, _run_of(conn, spec), world.view, spec, tmp_path / "inbox" / spec.delivery_id)
    assert not again.issued and again.invoices == 0 and snapshot(conn) == before and client._list("Purchase Invoice", [], ["name", "bill_no"]) == erp_before


def _run_of(conn, spec):
    from invoiceflow.registry import RunMeta

    (run_key,) = conn.execute("SELECT run_key FROM deliveries WHERE delivery_id = ?", (spec.delivery_id,)).fetchone()
    return RunMeta(run_key, spec.as_of)


def test_the_issued_rows_cannot_be_changed_by_anyone_not_even_the_run_that_issued_them(world, tmp_path, monkeypatch):
    read_the_printed_qr(monkeypatch, world)
    client, _ = fresh_erp(world, tmp_path)
    conn = open_warehouse(tmp_path)
    run_each_day(world, conn, client, tmp_path)
    for table in ("decisions", "extracted_fields", "qr_reads", "erp_posts", "decision_reasons", "daily_ops"):
        with pytest.raises(Exception, match="solo inserción"):
            conn.execute(f"DELETE FROM {table}")


def test_a_day_is_refused_when_it_is_not_a_loaded_daily_delivery_or_not_after_the_launch(world, tmp_path):
    client, _ = fresh_erp(world, tmp_path)
    conn = open_warehouse(tmp_path)
    spec = world.specs()[world.days[0]]
    run, inbox = load_files(world, conn, spec, [t.invoice_key for t in world.truths[world.days[0]]], tmp_path)
    for kwargs, message in (({"day": world.days[1]}, "después de la entrega"), ({"day": date(2026, 9, 30)}, "lanzamiento"), ({"invoice_keys": ["test_known-99999"]}, "no es de esta entrega")):
        before = snapshot(conn)
        with pytest.raises(daily.ScoringError, match=message):
            daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox, **kwargs)
        assert snapshot(conn) == before
    other = dataclasses.replace(run, as_of=spec.as_of.replace(year=2025))
    with pytest.raises(daily.ScoringError, match="instante"):
        daily.run_day(conn, client, world.extractor, other, world.view, spec, inbox)
    close = dataclasses.replace(spec, kind="close")
    with pytest.raises(daily.ScoringError, match="diaria"):
        daily.run_day(conn, client, world.extractor, run, world.view, close, inbox)
    assert count(conn, "decisions") == 0


def test_an_auto_post_is_posted_right_after_its_decision_so_the_next_invoice_sees_what_was_invoiced(world, tmp_path, monkeypatch):
    read_the_printed_qr(monkeypatch, world)
    """L99: the ERP's «already invoiced» of an order is what the earlier invoices of the same day posted, so the post of an invoice comes before the reads of the next one."""
    client, http = fresh_erp(world, tmp_path)
    calls: list[tuple[str, str]] = []
    current = {"key": None}
    for name in ("supplier_by_cuit", "purchase_order", "receipts", "invoiced_quantities", "find_invoice", "post_invoice"):
        real = getattr(client, name)

        def spy(*args, _name=name, _real=real, **kwargs):
            calls.append((_name, current["key"]))
            return _real(*args, **kwargs)

        setattr(client, name, spy)
    conn = open_warehouse(tmp_path)
    spec = world.specs()[world.days[0]]
    keys = [t.invoice_key for t in world.truths[world.days[0]]]
    run, inbox = load_files(world, conn, spec, keys, tmp_path)

    class Tracking:
        model_version = world.extractor.model_version

        def extract(self, image):
            raw = world.extractor.extract(image)
            current["key"] = raw.get("header", {}).get("nro")
            return raw

    daily.run_day(conn, client, Tracking(), run, world.view, spec, inbox)
    order = [(name, key) for name, key in calls]
    posts = [i for i, (name, _) in enumerate(order) if name == "post_invoice"]
    reads = [i for i, (name, _) in enumerate(order) if name == "supplier_by_cuit"]
    assert len(reads) == len(keys) and posts
    for post in posts:
        later = [r for r in reads if r > post]
        assert all(order[post][1] != order[r][1] for r in later), "a post comes before the reads of the next invoice and never after the reads of another"
        assert not any(order[r][1] == order[post][1] for r in later), "the invoice's own reads are all before its post"


def test_decisions_are_identical_with_one_day_and_five_day_deliveries(quick_view, tmp_path_factory, monkeypatch):
    """AC21: the days of a week delivered one at a time and the same days in one delivery give the same decisions, the same reasons and the same drafts, byte for byte (what is not the same is the run and the seconds)."""
    view = with_days(quick_view, date(2026, 10, 7))
    world = build_world(view, tmp_path_factory.mktemp("five_days"))
    assert len(world.days) == 5
    read_the_printed_qr(monkeypatch, world)
    one_by_one = tmp_path_factory.mktemp("one_by_one")
    client_a, _ = fresh_erp(world, one_by_one)
    conn_a = open_warehouse(one_by_one)
    run_each_day(world, conn_a, client_a, one_by_one)
    together = tmp_path_factory.mktemp("together")
    client_b, _ = fresh_erp(world, together)
    conn_b = open_warehouse(together)
    last = world.specs()[world.days[-1]]
    keys = [t.invoice_key for day in world.days for t in world.truths[day]]
    run, inbox = load_files(world, conn_b, last, keys, together)
    asked: list[tuple[str, date]] = []
    real = daily.erp_view
    monkeypatch.setattr(daily, "erp_view", lambda client, fields, qr, day, conn=None, *, invoice_key=None: (asked.append((invoice_key, day)), real(client, fields, qr, day, conn, invoice_key=invoice_key))[1])
    for day in world.days:
        daily.run_day(conn_b, client_b, world.extractor, run, world.view, last, inbox, day=day, invoice_keys=[t.invoice_key for t in world.truths[day]])
    assert asked == [(t.invoice_key, day) for day in world.days for t in world.truths[day]], "each invoice is looked at on its own day, not on the delivery's"
    assert decision_rows(conn_a) == decision_rows(conn_b) and len(decision_rows(conn_a)) == len(keys)
    assert [r[1:] for r in conn_a.execute("SELECT date_key, invoices, auto_post, review, posted FROM daily_ops ORDER BY 1")] == \
           [r[1:] for r in conn_b.execute("SELECT date_key, invoices, auto_post, review, posted FROM daily_ops ORDER BY 1")]
    names = lambda client: sorted(r["bill_no"] for r in client._list("Purchase Invoice", [], ["bill_no"]))
    assert names(client_a) == names(client_b)


# --- a failed read of the ERP (the coordinator's addition to U15) ---------------------------------------------------------------


class Flaky(ErpClient):
    """A client whose ``purchase_order`` fails with `error` on its `fail_on`-th call (counting from 1) while ``broken`` is set."""

    def __init__(self, inner: ErpClient, error: Exception, fail_on: int) -> None:
        self.__dict__.update(inner.__dict__)
        self.inner, self.error, self.fail_on, self.calls, self.broken = inner, error, fail_on, 0, True

    def purchase_order(self, name):
        self.calls += 1
        if self.broken and self.calls == self.fail_on:
            raise self.error
        return self.inner.purchase_order(name)


@pytest.mark.parametrize("error", [ErpUnreachable("sin respuesta"), ErpRejected("417", 417)], ids=["unreachable", "rejected"])
def test_a_failed_read_of_the_erp_stops_the_run_with_nothing_of_that_invoice_written_and_a_rerun_resumes(world, tmp_path, error, monkeypatch):
    read_the_printed_qr(monkeypatch, world)
    """Decision (L100): a read of the ERP that fails is not an invoice's verdict. The task fails (the failure handler marks the run), the invoices before it stay issued whole, nothing of the invoice whose read
    failed is written, and the rerun of the same day issues the rest and gives what an uninterrupted day gives. (``post_invoice`` is different: it catches the same errors because a draft that was
    not created is a fact about that invoice, ``post_failed``.)"""
    day = world.days[0]
    spec, keys = world.specs()[day], [t.invoice_key for t in world.truths[day]]
    reference_dir = tmp_path / "reference"
    ref_client, _ = fresh_erp(world, reference_dir)
    ref_conn = open_warehouse(reference_dir)
    run, inbox = load_files(world, ref_conn, spec, keys, reference_dir)
    daily.run_day(ref_conn, ref_client, world.extractor, run, world.view, spec, inbox)
    folder = tmp_path / "flaky"
    inner, _ = fresh_erp(world, folder)
    client = Flaky(inner, error, fail_on=3)
    conn = open_warehouse(folder)
    run, inbox = load_files(world, conn, spec, keys, folder)
    with pytest.raises(type(error)):
        daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox)
    issued = [r[0] for r in conn.execute("SELECT invoice_key FROM decisions ORDER BY 1")]
    assert issued == keys[:len(issued)] and 0 < len(issued) < len(keys), "the invoices before the failed one are issued, the failed one and the rest are not"
    failed = keys[len(issued)]
    for table in ("extracted_fields", "qr_reads", "decisions", "decision_reasons", "erp_posts"):
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE invoice_key = ?", (failed,)).fetchone()[0] == 0, table
    assert count(conn, "daily_ops") == 0, "the day's row is written when the day is finished"
    assert conn.execute("SELECT count(*) FROM erp_posts").fetchone()[0] == sum(1 for (d,) in conn.execute("SELECT decision FROM decisions") if d == "auto_post")
    client.broken = False                                    # the ERP answers again
    resumed = daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox)
    assert resumed.issued and resumed.invoices == len(keys) - len(issued)
    assert decision_rows(conn) == decision_rows(ref_conn) and count(conn, "daily_ops") == 1
    assert conn.execute("SELECT invoices, accepted FROM daily_ops").fetchone() == (len(keys), len(keys)), "the day's row counts the whole day, whoever issued each invoice"


def test_a_token_the_erp_does_not_accept_stops_the_run_and_says_so(world, tmp_path, monkeypatch):
    read_the_printed_qr(monkeypatch, world)
    inner, _ = fresh_erp(world, tmp_path)
    bad = Flaky(inner, ErpAuthError("401", 401), fail_on=1)
    conn = open_warehouse(tmp_path)
    spec, keys = world.specs()[world.days[0]], [t.invoice_key for t in world.truths[world.days[0]]]
    run, inbox = load_files(world, conn, spec, keys, tmp_path)
    with pytest.raises(ErpAuthError):
        daily.run_day(conn, bad, world.extractor, run, world.view, spec, inbox)
    assert count(conn, "decisions") == 0 and count(conn, "extracted_fields") == 0


def test_a_quarantined_file_is_issued_as_review_with_its_rule_and_no_page_is_read(world, tmp_path, monkeypatch):
    read_the_printed_qr(monkeypatch, world)
    client, _ = fresh_erp(world, tmp_path)
    conn = open_warehouse(tmp_path)
    day = world.days[0]
    spec, keys = world.specs()[day], [t.invoice_key for t in world.truths[day]]
    victim = next(p for p in world.files.iterdir() if p.stem == keys[0]).name
    run, inbox = load_files(world, conn, spec, keys, tmp_path, bad={victim: b""})
    result = daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox)
    row = conn.execute("SELECT decision, first_reason, reasons FROM decisions WHERE invoice_key = ?", (keys[0],)).fetchone()
    assert row == ("review", "file_rejected", 1) and result.quarantined == 1 and result.invoices == len(keys)
    assert conn.execute("SELECT count(*) FROM extracted_fields WHERE invoice_key = ?", (keys[0],)).fetchone()[0] == 0 and conn.execute("SELECT count(*) FROM qr_reads WHERE invoice_key = ?", (keys[0],)).fetchone()[0] == 0
    rule = conn.execute("SELECT rule_id FROM invoices WHERE invoice_key = ?", (keys[0],)).fetchone()[0]
    assert rule in {"V12", "V13", "V14", "V15"} and json.loads(conn.execute("SELECT detail_json FROM decision_reasons WHERE invoice_key = ?", (keys[0],)).fetchone()[0]) == {"rule": rule}
    assert conn.execute("SELECT quarantined, accepted FROM daily_ops").fetchone() == (1, len(keys) - 1)


def test_a_day_whose_row_is_written_is_not_completed_later_with_other_invoices(world, tmp_path, monkeypatch):
    """``invoice_keys`` is the whole day: a second call that brings more invoices of a day that has its row is refused before it issues anything (the row would count the first call only)."""
    read_the_printed_qr(monkeypatch, world)
    client, _ = fresh_erp(world, tmp_path)
    conn = open_warehouse(tmp_path)
    day = world.days[0]
    spec, keys = world.specs()[day], [t.invoice_key for t in world.truths[day]]
    run, inbox = load_files(world, conn, spec, keys, tmp_path)
    daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox, invoice_keys=keys[:2])
    before = snapshot(conn)
    with pytest.raises(daily.ScoringError, match="ya tiene su fila"):
        daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox, invoice_keys=keys)
    assert snapshot(conn) == before and conn.execute("SELECT invoices FROM daily_ops").fetchone() == (2,)
