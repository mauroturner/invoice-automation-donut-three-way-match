"""The ERP client without the master (U13; AC15, AC16; plan section 12, decisions D16, D17, D50): the read-back comparison field by field, the document of an invoice, the master as events, the
voucher number as the page prints it, and the client of each mode (the stand-in in this process, ERPNext over HTTP, what is missing)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from invoiceflow.config import ErpConfig
from invoiceflow.erp.client import ErpClient, ErpConfigError, ErpEvent, events_of, invoice_differences, invoice_doc, make_client
from invoiceflow.match import PoLine, bill_no, order_of, receipt_of
from tests.fixtures.match_cases import ORDER_NO, invoice, line, make_view, order, qr_ok, receipt

D = Decimal


def sent(**changes):
    document = {"supplier": "SUP-01", "bill_no": "00001-00000010", "bill_date": date(2026, 10, 5), "currency": "ARS", "docstatus": 0,
                "items": [{"item_code": "A1", "qty": D("10"), "rate": D("100.00"), "purchase_order": "OC-1", "po_detail": "OC-1-1", "purchase_receipt": "REM-1"},
                          {"item_code": "A2", "qty": D("2.5"), "rate": D("20.10"), "purchase_order": "OC-1", "po_detail": "OC-1-2", "purchase_receipt": "REM-1"}]}
    document.update(changes)
    return document


def held(**changes):
    document = {"name": "ACC-PINV-2026-00001", "supplier": "SUP-01", "bill_no": "00001-00000010", "bill_date": "2026-10-05", "currency": "ARS", "total": D("1050.25"), "docstatus": 0,
                "items": [{"item_code": "A1", "qty": 10, "rate": D("100.0"), "amount": 1000, "purchase_order": "OC-1", "po_detail": "OC-1-1", "purchase_receipt": "REM-1"},
                          {"item_code": "A2", "qty": D("2.5"), "rate": D("20.1"), "amount": D("50.25"), "purchase_order": "OC-1", "po_detail": "OC-1-2", "purchase_receipt": "REM-1"}]}
    document.update(changes)
    return document


def test_a_read_back_that_is_the_same_has_no_differences_whatever_the_number_types(view):
    assert invoice_differences(sent(), held()) == []
    assert invoice_differences(sent(bill_date="2026-10-05"), held()) == [], "a date as text or as a date"


@pytest.mark.parametrize("change, expected", [
    ({"supplier": "SUP-02"}, ["supplier"]), ({"bill_no": "00001-00000011"}, ["bill_no"]), ({"bill_date": "2026-10-06"}, ["bill_date"]), ({"currency": "USD"}, ["currency"]),
    ({"total": D("1050.26")}, ["total"]), ({"total": None}, ["total"]), ({"items": []}, ["items"]),
])
def test_each_header_difference_is_named(change, expected):
    assert invoice_differences(sent(), held(**change)) == expected


def test_each_line_difference_is_named_by_its_position():
    base = held()
    for index, key, value, name in ((0, "item_code", "A9", "items[0].item_code"), (0, "qty", 11, "items[0].qty"), (1, "rate", D("20.11"), "items[1].rate"), (1, "purchase_order", "OC-2", "items[1].purchase_order"),
                                    (0, "po_detail", "OC-1-9", "items[0].po_detail"), (1, "purchase_receipt", "REM-2", "items[1].purchase_receipt")):
        items = [dict(i) for i in base["items"]]
        items[index][key] = value
        found = invoice_differences(sent(), {**base, "items": items})
        assert name in found, (name, found)
    missing = [dict(i) for i in base["items"]]
    del missing[1]["rate"]
    assert "items[1].rate" in invoice_differences(sent(), {**base, "items": missing})
    assert invoice_differences(sent(), {**base, "items": base["items"][:1]}) == ["items"], "a line fewer is a different number of lines"


def test_the_total_that_is_compared_is_the_sum_of_each_lines_amount_rounded_to_the_cent_half_up():
    document = sent(items=[{"item_code": "A1", "qty": D("3"), "rate": D("0.335"), "purchase_order": "OC-1", "po_detail": "OC-1-1", "purchase_receipt": "REM-1"}])
    read = held(total=D("1.01"), items=[{"item_code": "A1", "qty": 3, "rate": D("0.335"), "purchase_order": "OC-1", "po_detail": "OC-1-1", "purchase_receipt": "REM-1"}])
    assert invoice_differences(document, read) == [], "3 x 0.335 = 1.005, half up: 1.01"
    assert invoice_differences(document, {**read, "total": D("1.00")}) == ["total"]


def test_the_voucher_number_is_the_point_of_sale_in_five_digits_and_the_number_in_eight():
    assert bill_no(1, 10) == "00001-00000010" and bill_no(12345, 99999999) == "12345-99999999" and bill_no(0, 0) == "00000-00000000"


def test_the_document_of_an_invoice_follows_the_qr_the_order_and_the_receipts(view):
    from invoiceflow.match import Receipt, ReceiptLine

    fields = invoice((line("A1", "10", "100.00"),))
    qr = qr_ok(fields, ctz=D("1"), moneda="PES")
    po = order((PoLine("A1", "Tornillo hexagonal", D("10"), D("100.00"), f"{ORDER_NO}-1"),))
    other_item = Receipt("REM-0", ORDER_NO, date(2026, 10, 1), (ReceiptLine("B2", D("3")),))
    first, second = receipt("4", date(2026, 10, 3), number="REM-1"), receipt("6", date(2026, 10, 4), number="REM-2")
    delivery_day = date(2026, 10, 6)
    document = invoice_doc(fields, qr.payload, make_view(po=po, receipts_until_day=(second, first, other_item), day=delivery_day), "SUP-01")
    assert document == {"supplier": "SUP-01", "bill_no": "00001-00000010", "bill_date": date(2026, 10, 5), "posting_date": delivery_day, "currency": "ARS", "conversion_rate": D("1"),
                        "docstatus": 0, "items": [{"item_code": "A1", "qty": D("10"), "rate": D("100.00"), "purchase_order": ORDER_NO, "po_detail": f"{ORDER_NO}-1", "purchase_receipt": "REM-1"}]}
    dollars = invoice_doc(fields, qr_ok(fields, moneda="DOL", ctz=D("1000.5")).payload, make_view(po=po, receipts_until_day=()), "SUP-01")
    assert dollars["currency"] == "USD" and dollars["conversion_rate"] == D("1000.5") and "purchase_receipt" not in dollars["items"][0]
    with pytest.raises(ValueError, match="orden"):
        invoice_doc(invoice((line("ZZ"),)), qr.payload, make_view(po=po), "SUP-01")


def test_a_document_of_the_erp_is_read_as_an_order_and_a_receipt_with_exact_decimals():
    doc = {"name": "OC-1", "supplier": "SUP-01", "currency": "ARS", "transaction_date": "2026-09-20",
           "items": [{"name": "OC-1-1", "item_code": "A1", "item_name": "Tornillo", "qty": 10, "rate": D("100.0")}]}
    po = order_of(doc)
    assert (po.number, po.supplier, po.currency, po.date) == ("OC-1", "SUP-01", "ARS", date(2026, 9, 20)) and po.lines == (PoLine("A1", "Tornillo", D("10"), D("100.0"), "OC-1-1"),)
    r = receipt_of({"name": "REM-1", "posting_date": "2026-10-04", "items": [{"item_code": "A1", "qty": 4, "purchase_order": "OC-1"}, {"item_code": "A2", "qty": 9, "purchase_order": "OC-2"}]}, "OC-1")
    assert (r.number, r.order, r.date, [(x.item_code, x.qty) for x in r.lines]) == ("REM-1", "OC-1", date(2026, 10, 4), [("A1", D("4"))]), "only the rows that receive this order"


def test_the_master_is_loaded_as_events_in_the_order_it_happened(quick_master):
    events = events_of(quick_master)
    kinds = [e.kind for e in events]
    assert kinds[:len(quick_master.suppliers)] == ["supplier"] * len(quick_master.suppliers)
    assert kinds[len(quick_master.suppliers):len(quick_master.suppliers) + len(quick_master.items)] == ["item"] * len(quick_master.items)
    rest = events[len(quick_master.suppliers) + len(quick_master.items):]
    assert [e.kind for e in rest] == [e.kind for e in quick_master.events] and [e.document for e in rest] == [e.document for e in quick_master.events]
    supplier_of = {po.po_number: po.supplier_key for po in quick_master.orders}
    assert all(e.supplier_key == supplier_of[e.document.po_number] for e in rest if e.kind == "receipt") and all(e.supplier_key is None for e in events if e.kind != "receipt")
    cut = quick_master.events[len(quick_master.events) // 2].date
    assert any(e.date == cut for e in quick_master.events)
    until = events_of(quick_master, cut)
    assert [e.document for e in until if e.kind in ("po", "receipt")] == [e.document for e in quick_master.events if e.date <= cut], "the cut day is in"
    assert len(until) < len(events)
    assert [e.kind for e in until[:len(quick_master.suppliers)]] == ["supplier"] * len(quick_master.suppliers), "the suppliers and the items do not depend on the date"
    with pytest.raises(ValueError, match="evento"):
        ErpClient(httpx.Client(base_url="http://x", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {}}))), "k:s").apply_master_event(ErpEvent("weird", None))


def test_a_client_without_a_token_is_not_built():
    for token in ("", " ", None):
        with pytest.raises(ErpConfigError, match="token"):
            ErpClient(httpx.Client(base_url="http://x"), token)


# --- the mode of [erp] ----------------------------------------------------------------------------------------------------------


def erp(**changes) -> ErpConfig:
    values = dict(mode="standin", base_url="", token_env="INVOICEFLOW_ERP_TOKEN", post_as="draft", timeout_s=30)
    values.update(changes)
    return ErpConfig(**values)


def test_the_standin_mode_is_the_stand_in_in_this_process_with_the_token_of_the_environment_or_one_of_its_own(cfg, tmp_path):
    import dataclasses

    paths = dataclasses.replace(cfg.paths, erp_db=tmp_path / "erp" / "standin.sqlite")
    own = make_client(erp(), paths, env={})
    assert own.supplier_by_cuit("30000000007") is None and (tmp_path / "erp" / "standin.sqlite").exists()
    named = make_client(erp(), paths, env={"INVOICEFLOW_ERP_TOKEN": "key:secret"})
    assert named._headers["Authorization"] == "token key:secret"
    other = make_client(erp(), dataclasses.replace(paths, erp_db=tmp_path / "other.sqlite"), env={})
    assert own._headers["Authorization"] != other._headers["Authorization"], "a token made for each process"
    assert own._headers["Authorization"].startswith("token standin:") and len(own._headers["Authorization"]) > 30


def test_the_http_mode_needs_an_address_and_a_token_and_never_reads_the_stand_in(cfg, tmp_path):
    import dataclasses

    paths = dataclasses.replace(cfg.paths, erp_db=tmp_path / "must-not-exist" / "standin.sqlite")
    with pytest.raises(ErpConfigError, match="base_url") as no_address:
        make_client(erp(mode="http"), paths, env={"INVOICEFLOW_ERP_TOKEN": "k:s"})
    with pytest.raises(ErpConfigError, match="INVOICEFLOW_ERP_TOKEN"):
        make_client(erp(mode="http", base_url="https://erp.example"), paths, env={})
    with pytest.raises(ErpConfigError, match="INVOICEFLOW_ERP_TOKEN"):
        make_client(erp(mode="http", base_url="https://erp.example"), paths, env={"INVOICEFLOW_ERP_TOKEN": "  "})
    assert ErpConfigError.EXIT_CODE == 2 and no_address.value.EXIT_CODE == 2
    client = make_client(erp(mode="http", base_url="https://erp.example", timeout_s=7, token_env="MY_TOKEN"), paths, env={"MY_TOKEN": "key:secret"})
    assert str(client._http.base_url).rstrip("/") == "https://erp.example" and client._http.timeout.read == 7 and client._headers["Authorization"] == "token key:secret"
    assert not (tmp_path / "must-not-exist").exists()
    with pytest.raises(ErpConfigError, match="mode"):
        make_client(erp(mode="odoo"), paths, env={})


def test_the_default_environment_is_the_processs(cfg, tmp_path, monkeypatch):
    import dataclasses

    monkeypatch.setenv("INVOICEFLOW_ERP_TOKEN", "fromenv:value")
    client = make_client(erp(), dataclasses.replace(cfg.paths, erp_db=tmp_path / "e.sqlite"))
    assert client._headers["Authorization"] == "token fromenv:value"


def mock(handler):
    return ErpClient(httpx.Client(base_url="http://erp", transport=httpx.MockTransport(handler)), "k:s")


def test_a_403_is_a_token_the_erp_does_not_accept_like_a_401():
    from invoiceflow.erp.client import ErpAuthError

    for status in (401, 403):
        with pytest.raises(ErpAuthError) as error:
            mock(lambda r, status=status: httpx.Response(status, json={"exc_type": "PermissionError"})).purchase_order("OC-1")
        assert error.value.status == status and "PermissionError" not in str(error.value)


def test_two_suppliers_with_one_cuit_are_a_mistake_of_the_master_data_and_say_so():
    from invoiceflow.erp.client import ErpError, ErpRejected

    rows = {"data": [{"name": "A", "tax_id": "30000000007"}, {"name": "B", "tax_id": "30000000007"}]}
    with pytest.raises(ErpError, match="CUIT") as error:
        mock(lambda r: httpx.Response(200, json=rows)).supplier_by_cuit("30000000007")
    assert not isinstance(error.value, ErpRejected)
    assert mock(lambda r: httpx.Response(200, json={"data": rows["data"][:1]})).supplier_by_cuit("30000000007") == rows["data"][0]
    assert mock(lambda r: httpx.Response(200, json={"data": []})).supplier_by_cuit("30000000007") is None


def test_what_was_invoiced_counts_only_the_lines_of_the_order_asked_for():
    def handler(request):
        if request.url.path.endswith("Purchase%20Invoice") or request.url.path.endswith("Purchase Invoice"):
            return httpx.Response(200, json={"data": [{"name": "I1"}, {"name": "I2"}]})
        document = {"I1": {"items": [{"item_code": "A1", "qty": 4, "purchase_order": "OC-1"}, {"item_code": "A1", "qty": 9, "purchase_order": "OC-2"}, {"item_code": "A2", "qty": 1.5, "purchase_order": "OC-1"}]},
                    "I2": {"items": [{"item_code": "A1", "qty": 2, "purchase_order": "OC-1"}]}}[request.url.path.rsplit("/", 1)[1]]
        return httpx.Response(200, text=__import__("json").dumps({"data": document}))

    assert mock(handler).invoiced_quantities("OC-1") == {"A1": D("6"), "A2": D("1.5")}


def test_create_invoice_itself_refuses_anything_but_a_draft_before_a_request():
    from invoiceflow.erp.client import ErpRejected

    seen = []
    client = mock(lambda r: seen.append(r) or httpx.Response(200, json={"data": {}}))
    for status in (1, 2):
        with pytest.raises(ErpRejected, match="borrador"):
            client.create_invoice({**sent(), "docstatus": status})
    assert seen == []
    client.create_invoice({k: v for k, v in sent().items() if k != "docstatus"})
    assert len(seen) == 1 and b'"docstatus":0' in seen[0].content.replace(b" ", b"") and seen[0].headers["authorization"] == "token k:s"
