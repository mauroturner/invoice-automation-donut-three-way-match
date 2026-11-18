"""The ERP client against the stand-in, the way the flow uses it (U13; AC11, AC15, AC16; plan section 12, decisions D16, D17, D50; risk items 3 and 2): the view of an invoice built from what the ERP
knows on the day, the draft posted once and read back equal, a second post that creates nothing, an ERP that refuses or does not answer that leaves the invoice in review without cutting the day, and
nothing of what the match sent to review reaching the ERP. The invoices are the launch partitions of the quick configuration with their true fields (the oracle's inputs)."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from invoiceflow.baselines import oracle, truth_fields, truth_qr
from invoiceflow.erp.client import ErpAuthError, ErpClient, PostOutcome, invoice_differences
from invoiceflow.match import erp_view
from invoiceflow.synth.invoices import invoices
from tests.fixtures.erp_world import TOKEN, ErpWorld, decide_and_post, load_world, order_of_arrival, standin_client

D = Decimal


@pytest.fixture(scope="module")
def truths(quick_view, quick_master, tmp_path_factory):
    seal = tmp_path_factory.mktemp("seal") / "none.json"
    return invoices(quick_view, quick_master, "validation_known", None, seal_path=seal) + invoices(quick_view, quick_master, "validation_new", None, seal_path=seal)


@pytest.fixture(scope="module")
def loaded_template(quick_view, quick_master, truths, tmp_path_factory) -> Path:
    """The stand-in's file with the master of these invoices in it, loaded once: each test starts from a copy."""
    return load_world(quick_view, quick_master, truths, tmp_path_factory.mktemp("erp_template")).db


@pytest.fixture
def world(loaded_template, quick_view, quick_master, truths, tmp_path) -> ErpWorld:
    target = tmp_path / "erp" / "standin.sqlite"
    target.parent.mkdir()
    shutil.copy(loaded_template, target)
    client, http = standin_client(target)
    return ErpWorld(client, http, quick_master, list(truths), quick_view, target)


def master_receipts_names(world, truth) -> list[str]:
    return sorted(r.receipt_number for r in world.master.receipts if r.po_number == truth.po_number)


def invoice_names(world) -> list[str]:
    return [row["name"] for row in world.client._list("Purchase Invoice", [], ["name"])]


def test_posting_twice_creates_one_invoice(world, truths):
    truth = next(t for t in order_of_arrival(truths) if t.planted == "none")
    decision, view, first, document = decide_and_post(world, truth)
    assert decision.decision == "auto_post" and first == PostOutcome("posted", first.erp_name, True) and first.erp_name.startswith("ACC-PINV-2026-")
    assert document["bill_no"] == f"{truth.header.pto_vta:05d}-{truth.header.nro:08d}" and document["docstatus"] == 0
    second = world.client.post_invoice(document)
    assert second == PostOutcome("already_posted", first.erp_name, True)
    assert invoice_names(world) == [first.erp_name], "one invoice in the ERP"
    third = world.client.post_invoice(document)
    assert third == second and invoice_names(world) == [first.erp_name]


def test_every_posted_invoice_reads_back_equal(world, truths):
    """C09: each auto_post is in the ERP as a draft with the supplier, number, date, currency, total and every line the decision had."""
    posted = 0
    for truth in order_of_arrival(truths):
        decision, view, outcome, document = decide_and_post(world, truth)
        assert decision.decision == truth.expected_decision, (truth.invoice_key, truth.planted)
        if outcome is None:
            continue
        posted += 1
        assert outcome.status == "posted" and outcome.readback_equal is True and outcome.differences == () and outcome.stage is None
        read = world.client.read_invoice(outcome.erp_name)
        assert invoice_differences(document, read) == [] and read["docstatus"] == 0 and read["doctype"] == "Purchase Invoice"
        assert read["supplier"] == view.supplier_by_cuit[truth.header.cuit_emisor] and read["bill_date"] == truth.header.fecha.isoformat()
        assert read["total"] == truth.totals.neto and [(i["item_code"], i["qty"], i["rate"], i["amount"]) for i in read["items"]] == [(x.code, x.qty, x.unit_price, x.amount) for x in truth.lines]
        assert all(i["purchase_order"] == truth.po_number and i["po_detail"].startswith(truth.po_number) and i["purchase_receipt"].startswith("REM-") for i in read["items"])
    assert posted >= 5 and len(invoice_names(world)) == posted


def test_replaying_a_day_creates_no_new_invoice(world, truths):
    outcomes = {t.invoice_key: decide_and_post(world, t) for t in order_of_arrival(truths)}
    posted = {key: o[2] for key, o in outcomes.items() if o[2] is not None}
    names = invoice_names(world)
    assert len(names) == len(posted) >= 5
    for truth in order_of_arrival(truths):   # the same documents, again, in a world where they are in the ERP
        document = outcomes[truth.invoice_key][3]
        if document is not None:
            again = world.client.post_invoice(document)
            assert again.status == "already_posted" and again.erp_name == posted[truth.invoice_key].erp_name and again.readback_equal is True
    assert invoice_names(world) == names, "no new invoice, no other name"


def test_nothing_the_match_sent_to_review_reaches_the_erp(world, truths):
    for truth in order_of_arrival(truths):
        decide_and_post(world, truth)
    posted_numbers = {i["bill_no"] for i in (world.client._get("Purchase Invoice", n) for n in invoice_names(world))}
    expected = {f"{t.header.pto_vta:05d}-{t.header.nro:08d}" for t in truths if t.expected_decision == "auto_post"}
    review = {f"{t.header.pto_vta:05d}-{t.header.nro:08d}" for t in truths if t.expected_decision == "review" and t.planted != "duplicate"}
    assert posted_numbers == expected and not posted_numbers & review
    assert any(t.expected_decision == "review" for t in truths)


def test_the_decisions_with_the_erp_are_those_of_the_oracle_that_had_no_erp(world, truths, quick_view, quick_master):
    """The view from the ERP (supplier by CUIT, order, receipts until the day, what was invoiced, the voucher) is the same the oracle makes from the master: the same decisions."""
    from_the_erp = {t.invoice_key: decide_and_post(world, t)[0] for t in order_of_arrival(truths)}
    assert from_the_erp == oracle(quick_view, quick_master, truths)


# --- an ERP that refuses, that does not answer, that answers something else -----------------------------------------------------------


def a_good_document(world, truths):
    truth = next(t for t in order_of_arrival(truths) if t.planted == "none")
    decision, view, outcome, document = decide_and_post(world, truth)
    return truth, view, document


def test_an_erp_that_refuses_a_document_leaves_that_invoice_post_failed_and_the_day_goes_on(world, truths):
    unplanted = [t for t in order_of_arrival(truths) if t.planted == "none"]
    fields, qr = truth_fields(unplanted[0]), truth_qr(unplanted[0], world.view)
    view = erp_view(world.client, fields, qr, unplanted[0].received_on)
    from invoiceflow.erp.client import invoice_doc

    bad = invoice_doc(fields, qr.payload, view, "NOBODY")
    refused = world.client.post_invoice(bad)
    assert refused == PostOutcome("post_failed", None, None, "rejected", ())
    assert invoice_names(world) == []
    good = decide_and_post(world, unplanted[1])
    assert good[2].status == "posted" and invoice_names(world) == [good[2].erp_name]


def test_an_erp_that_does_not_answer_or_fails_is_post_failed_unreachable_and_never_raises(world, truths):
    truth, view, document = a_good_document(world, truths)
    for failure in (httpx.ConnectError("refused"), httpx.ReadTimeout("slow")):
        def handler(request, failure=failure):
            raise failure

        client = ErpClient(httpx.Client(base_url="http://erp", transport=httpx.MockTransport(handler)), TOKEN)
        assert client.post_invoice({**document, "bill_no": "00001-99999999"}) == PostOutcome("post_failed", None, None, "unreachable", ())
    server_error = ErpClient(httpx.Client(base_url="http://erp", transport=httpx.MockTransport(lambda r: httpx.Response(503, text="<html>maintenance</html>"))), TOKEN)
    assert server_error.post_invoice(document).stage == "unreachable"
    garbage = ErpClient(httpx.Client(base_url="http://erp", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not json"))), TOKEN)
    assert garbage.post_invoice(document).stage == "unreachable"


def test_a_token_the_erp_does_not_accept_is_raised_and_not_an_invoices_failure(world, truths):
    truth, view, document = a_good_document(world, truths)
    wrong = ErpClient(world.http, "key123:other")
    with pytest.raises(ErpAuthError) as error:
        wrong.post_invoice({**document, "bill_no": "00001-99999999"})
    assert "other" not in str(error.value) and error.value.status == 401


def test_a_read_back_that_differs_is_post_failed_and_says_where(world, truths):
    truth, view, document = a_good_document(world, truths)
    document = {**document, "bill_no": "00001-88888888"}

    def tampering(request: httpx.Request) -> httpx.Response:
        response = world.http.request(request.method, request.url.raw_path.decode(), headers=dict(request.headers), content=request.content)
        if request.method == "GET" and "/Purchase%20Invoice/ACC-PINV" in request.url.raw_path.decode():
            body = json.loads(response.text)
            body["data"]["items"][0]["qty"] += 1
            body["data"]["total"] = 1.0
            return httpx.Response(200, json=body)
        return httpx.Response(response.status_code, content=response.content)

    client = ErpClient(httpx.Client(base_url="http://standin", transport=httpx.MockTransport(tampering)), TOKEN)
    outcome = client.post_invoice(document)
    assert outcome.status == "post_failed" and outcome.stage == "read_back" and outcome.readback_equal is False and outcome.erp_name.startswith("ACC-PINV-")
    assert outcome.differences == ("total", "items[0].qty")
    again = client.post_invoice(document)
    assert again.status == "post_failed" and again.stage == "read_back" and again.erp_name == outcome.erp_name, "found, read, still different: the same answer and nothing created"
    assert outcome.erp_name in invoice_names(world) and len(invoice_names(world)) == 2, "the draft is in the ERP once, next to the one the helper posted first"


def test_only_a_draft_is_ever_sent_and_no_request_is_made_for_another_status(world, truths):
    truth, view, document = a_good_document(world, truths)
    seen = []
    client = ErpClient(httpx.Client(base_url="http://standin", transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200, json={"data": []}))), TOKEN)
    assert client.post_invoice({**document, "docstatus": 1}).stage == "rejected" and seen == []
    assert client.post_invoice({**document, "docstatus": 2}).stage == "rejected" and seen == []


def test_the_token_is_in_the_header_of_every_request_and_in_no_other_place(world, truths):
    seen = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        response = world.http.request(request.method, request.url.raw_path.decode(), headers=dict(request.headers), content=request.content)
        return httpx.Response(response.status_code, content=response.content)

    client = ErpClient(httpx.Client(base_url="http://standin", transport=httpx.MockTransport(spy)), TOKEN)
    truth = next(t for t in order_of_arrival(truths) if t.planted == "none")
    fields, qr = truth_fields(truth), truth_qr(truth, world.view)
    view = erp_view(client, fields, qr, truth.received_on)
    from invoiceflow.erp.client import invoice_doc

    client.post_invoice(invoice_doc(fields, qr.payload, view, view.supplier_by_cuit[qr.payload.cuit]))
    assert len(seen) >= 8
    for request in seen:
        assert request.headers["authorization"] == f"token {TOKEN}"
        assert TOKEN not in str(request.url) and TOKEN.encode() not in request.content and "secret456" not in str(request.url)
        assert request.url.path.startswith("/api/resource/")


# --- the view of an invoice -----------------------------------------------------------------------------------------------------------------------


def test_a_receipt_dated_after_the_day_is_not_asked_for_and_does_not_count(world, truths):
    """AC11: the receipts of the view are those dated on or before the day; the day before the last receipt the order is not whole."""
    truth = next(t for t in order_of_arrival(truths) if t.planted == "none")
    fields, qr = truth_fields(truth), truth_qr(truth, world.view)
    receipts = [r for r in world.master.receipts if r.po_number == truth.po_number]
    last = max(r.receipt_date for r in receipts)
    on_time = erp_view(world.client, fields, qr, last)
    assert [r.number for r in on_time.receipts_until_day] == sorted(r.receipt_number for r in receipts)
    assert sum((x.qty for r in on_time.receipts_until_day for x in r.lines), D(0)) == sum((x.qty for r in receipts for x in r.lines), D(0))
    before = erp_view(world.client, fields, qr, last - timedelta(days=1))
    assert all(r.date <= last - timedelta(days=1) for r in before.receipts_until_day)
    assert len(before.receipts_until_day) == len([r for r in receipts if r.receipt_date < last])
    assert before.day == last - timedelta(days=1)
    long_before = erp_view(world.client, fields, qr, min(r.receipt_date for r in receipts) - timedelta(days=1))
    assert long_before.receipts_until_day == ()


def test_the_view_has_the_order_the_supplier_of_the_cuit_and_what_was_invoiced(world, truths):
    ordered = order_of_arrival(truths)
    first = next(t for t in ordered if t.planted == "none")
    fields, qr = truth_fields(first), truth_qr(first, world.view)
    view = erp_view(world.client, fields, qr, first.received_on)
    assert view.po.number == first.po_number and view.po.supplier == first.supplier_key and view.supplier_by_cuit == {first.header.cuit_emisor: first.supplier_key}
    assert [(x.item_code, x.qty, x.unit_price) for x in view.po.lines] == [(x.code, x.qty, x.unit_price) for x in first.lines] and all(x.row.startswith(first.po_number) for x in view.po.lines)
    master_po = next(po for po in world.master.orders if po.po_number == first.po_number)
    assert view.po.currency == {"PES": "ARS", "DOL": "USD"}[master_po.currency] and view.po.date == master_po.po_date, "the ERP says ISO codes and the order's own date"
    assert [x.description for x in view.po.lines] == [line.description_es for line in master_po.lines]
    assert world.client.purchase_order(first.po_number)["docstatus"] == 1 and world.client._get("Purchase Receipt", master_receipts_names(world, first)[0])["docstatus"] == 1
    item = world.client._get("Item", first.lines[0].code)
    item_master = next(i for i in world.master.items if i.code == first.lines[0].code)
    assert item["standard_rate"] == item_master.list_price and item["stock_uom"] == item_master.unit and item["item_name"] == item_master.description_es
    supplier = world.client._get("Supplier", first.supplier_key)
    assert supplier["tax_id"] == first.header.cuit_emisor and supplier["supplier_name"] == next(s.name_es for s in world.master.suppliers if s.supplier_key == first.supplier_key)
    master_receipts = [r for r in world.master.receipts if r.po_number == first.po_number and r.receipt_date <= first.received_on]
    assert [(r.number, r.date, [(x.item_code, x.qty) for x in r.lines]) for r in view.receipts_until_day] == [(r.receipt_number, r.receipt_date, [(x.item_code, x.qty) for x in r.lines]) for r in master_receipts]
    assert view.invoiced_before == {} and view.known_fiscal_keys == frozenset()
    decide_and_post(world, first)
    after = erp_view(world.client, fields, qr, first.received_on)
    assert after.invoiced_before == {x.code: x.qty for x in first.lines}
    key = (first.header.cuit_emisor, first.header.pto_vta, first.header.tipo, first.header.nro)
    assert after.known_fiscal_keys == frozenset({key}), "the ERP has that supplier invoice number of that supplier"


def test_an_order_the_page_does_not_cite_or_the_erp_does_not_have_is_no_order_and_asks_nothing_more(world, truths):
    import dataclasses

    truth = next(t for t in order_of_arrival(truths) if t.planted == "none")
    fields, qr = truth_fields(truth), truth_qr(truth, world.view)
    paths = []
    spy_client = ErpClient(httpx.Client(base_url="http://standin", transport=httpx.MockTransport(
        lambda r: (paths.append(r.url.path), (lambda resp: httpx.Response(resp.status_code, content=resp.content))(world.http.request(r.method, r.url.raw_path.decode(), headers=dict(r.headers), content=r.content)))[1])), TOKEN)
    none_cited = erp_view(spy_client, dataclasses.replace(fields, orden_compra=None), qr, truth.received_on)
    assert none_cited.po is None and none_cited.receipts_until_day == () and none_cited.invoiced_before == {}
    assert not [p for p in paths if "Purchase" in p and ("Order" in p or "Receipt" in p)], paths
    missing = erp_view(spy_client, dataclasses.replace(fields, orden_compra="OC-2026-999999"), qr, truth.received_on)
    assert missing.po is None and missing.receipts_until_day == ()
    unknown = erp_view(spy_client, dataclasses.replace(fields, cuit_emisor="30999999990"), type(qr)(None, "not_read", None, None), truth.received_on)
    assert unknown.supplier_by_cuit == {}


def test_a_voucher_that_is_not_complete_is_not_looked_for_anywhere(world, truths):
    import dataclasses

    truth = next(t for t in order_of_arrival(truths) if t.planted == "none")
    fields = dataclasses.replace(truth_fields(truth), pto_vta=None)
    qr = type(truth_qr(truth, world.view))(None, "not_read", None, None)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE qr_reads (invoice_key TEXT, status TEXT, cuit TEXT, pto_vta INTEGER, tipo_cmp INTEGER, nro_cmp INTEGER)")
    view = erp_view(world.client, fields, qr, truth.received_on, conn)
    assert view.known_fiscal_keys == frozenset() and view.supplier_by_cuit == {truth.header.cuit_emisor: truth.supplier_key}


def test_a_voucher_the_warehouse_already_has_is_known_unless_it_is_the_invoice_being_decided(world, truths):
    truth = next(t for t in order_of_arrival(truths) if t.planted == "none")
    fields, qr = truth_fields(truth), truth_qr(truth, world.view)
    p = qr.payload
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE qr_reads (invoice_key TEXT, status TEXT, cuit TEXT, pto_vta INTEGER, tipo_cmp INTEGER, nro_cmp INTEGER)")
    assert erp_view(world.client, fields, qr, truth.received_on, conn).known_fiscal_keys == frozenset()
    conn.execute("INSERT INTO qr_reads VALUES ('other-00001', 'ok', ?, ?, ?, ?)", (p.cuit, p.pto_vta, p.tipo_cmp, p.nro_cmp))
    key = (p.cuit, p.pto_vta, p.tipo_cmp, p.nro_cmp)
    assert erp_view(world.client, fields, qr, truth.received_on, conn, invoice_key="mine-00001").known_fiscal_keys == frozenset({key})
    assert erp_view(world.client, fields, qr, truth.received_on, conn, invoice_key="other-00001").known_fiscal_keys == frozenset(), "its own row is not another invoice"
    conn.execute("UPDATE qr_reads SET status = 'invalid'")
    assert erp_view(world.client, fields, qr, truth.received_on, conn, invoice_key="mine-00001").known_fiscal_keys == frozenset(), "only a valid QR names a voucher"
    assert erp_view(world.client, fields, qr, truth.received_on, sqlite3.connect(":memory:")).known_fiscal_keys == frozenset(), "a warehouse without the table knows nothing"


def test_a_list_longer_than_a_page_is_read_whole(tmp_path):
    """Frappe answers 20 rows unless the page length says otherwise: 25 receipts of one order are 25."""
    from types import SimpleNamespace

    client, http = standin_client(tmp_path / "x.sqlite")
    from invoiceflow.erp.client import ErpEvent

    client.apply_master_event(ErpEvent("supplier", SimpleNamespace(supplier_key="S", name_es="S (ficticio)", cuit="30000000007")))
    client.apply_master_event(ErpEvent("item", SimpleNamespace(code="A1", description_es="A", unit="u", list_price=D("1.00"))))
    from datetime import date

    lines = (SimpleNamespace(item_code="A1", description_es="A", unit="u", qty=D("25"), unit_price=D("1.00")),)
    client.apply_master_event(ErpEvent("po", SimpleNamespace(po_number="OC-1", supplier_key="S", po_date=date(2026, 9, 1), currency="PES", lines=lines)))
    for number in range(25):
        receipt = SimpleNamespace(receipt_number=f"REM-{number:02d}", po_number="OC-1", receipt_date=date(2026, 9, 2) + timedelta(days=number % 3), lines=(SimpleNamespace(item_code="A1", qty=D("1")),))
        client.apply_master_event(ErpEvent("receipt", receipt, "S"))
    got = client.receipts("OC-1", date(2026, 9, 10))
    assert len(got) == 25 and [r["posting_date"] for r in got] == sorted(r["posting_date"] for r in got)
    assert len(client.receipts("OC-1", date(2026, 9, 2))) == len([n for n in range(25) if n % 3 == 0])
