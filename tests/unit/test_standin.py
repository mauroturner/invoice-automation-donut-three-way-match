"""The stand-in of the ERP (U13; AC16; plan section 12, decisions D16, D27, D50; risk items 2 and 3): FastAPI over SQLite, the shape of Frappe's REST API for the five doctypes the client uses,
the token compared with ``hmac.compare_digest``, 401 without it, 404 without the resource, 409 for a supplier invoice number that is already there, and never a path or a trace in an error."""

from __future__ import annotations

import inspect
import json
from decimal import Decimal

import pytest
import fastapi.testclient as testclient

from invoiceflow.erp import standin
from invoiceflow.erp.standin import standin_app

TOKEN = "key123:secret456"
AUTH = {"Authorization": f"token {TOKEN}"}


@pytest.fixture
def http(tmp_path):
    return testclient.TestClient(standin_app(tmp_path / "erp" / "standin.sqlite", TOKEN), raise_server_exceptions=False)


def post(http, doctype, doc, headers=AUTH):
    return http.post(f"/api/resource/{doctype}", content=json.dumps(doc), headers={**headers, "Content-Type": "application/json"})


def supplier(name="SUP-01", cuit="30000000007"):
    return {"name": name, "supplier_name": f"Proveedor {name} (ficticio)", "tax_id": cuit}


def item(code="A1", rate=100.0):
    return {"item_code": code, "item_name": f"Artículo {code}", "stock_uom": "u", "standard_rate": rate}


def po(name="OC-2026-000001", sup="SUP-01", items=None, day="2026-09-20", currency="ARS"):
    rows = items or [{"item_code": "A1", "qty": 10, "rate": 100.0}, {"item_code": "A2", "qty": 5, "rate": 20.5}]
    return {"name": name, "supplier": sup, "transaction_date": day, "currency": currency, "docstatus": 1, "items": rows}


def receipt(name="REM-000001", sup="SUP-01", day="2026-10-04", order="OC-2026-000001", items=None):
    rows = items or [{"item_code": "A1", "qty": 10, "purchase_order": order}]
    return {"name": name, "supplier": sup, "posting_date": day, "docstatus": 1, "items": rows}


def invoice(bill_no="00001-00000010", sup="SUP-01", day="2026-10-05", order="OC-2026-000001", items=None, docstatus=0):
    rows = items or [{"item_code": "A1", "qty": 10, "rate": 100.0, "purchase_order": order, "po_detail": f"{order}-1", "purchase_receipt": "REM-000001"}]
    return {"supplier": sup, "bill_no": bill_no, "bill_date": day, "posting_date": day, "currency": "ARS", "conversion_rate": 1, "docstatus": docstatus, "items": rows}


@pytest.fixture
def world(http):
    for doc in (supplier(), supplier("SUP-02", "30000000015")):
        assert post(http, "Supplier", doc).status_code == 200
    for code, rate in (("A1", 100.0), ("A2", 20.5)):
        assert post(http, "Item", item(code, rate)).status_code == 200
    assert post(http, "Purchase Order", po()).status_code == 200
    assert post(http, "Purchase Receipt", receipt()).status_code == 200
    return http


# --- the token ---------------------------------------------------------------------------------------------------------------


def test_every_request_without_the_token_is_401(world):
    """AC16: no token, another token, another scheme, an empty or a malformed header: 401 on every route and method, on a route that exists and on one that does not; the body says nothing of the server."""
    requests = [("GET", "/api/resource/Supplier"), ("GET", "/api/resource/Supplier/SUP-01"), ("POST", "/api/resource/Supplier"), ("GET", "/api/resource/Nothing"),
                ("GET", "/etc/passwd"), ("DELETE", "/api/resource/Supplier/SUP-01"), ("GET", "/")]
    bad = [{}, {"Authorization": ""}, {"Authorization": "token"}, {"Authorization": "token key123:wrong"}, {"Authorization": "Bearer key123:secret456"},
           {"Authorization": "token  key123:secret456"}, {"Authorization": "Token key123:secret456 "}, {"Authorization": f"token {TOKEN}x"}, {"Authorization": f"token {TOKEN[:-1]}"},
           {"Authorization": "basic a2V5MTIzOnNlY3JldDQ1Ng=="}]
    for method, path in requests:
        for headers in bad:
            response = world.request(method, path, headers=headers)
            assert response.status_code == 401, (method, path, headers)
            assert response.json() == {"exc_type": "AuthenticationError", "message": "Credenciales no válidas"}
            assert path not in response.text and "Traceback" not in response.text
    assert world.get("/api/resource/Supplier", headers=AUTH).status_code == 200


def test_the_token_is_compared_in_constant_time(world, monkeypatch):
    """Every request, the ones with a token and the ones without, goes through ``hmac.compare_digest`` (spied), and the module never compares the header with ``==``."""
    calls = []
    real = standin.hmac.compare_digest
    monkeypatch.setattr(standin.hmac, "compare_digest", lambda a, b: (calls.append((a, b)), real(a, b))[1])
    world.get("/api/resource/Supplier", headers=AUTH)
    world.get("/api/resource/Supplier")
    assert len(calls) == 2 and calls[0][0] == calls[0][1] == f"token {TOKEN}".encode() and calls[1][0] == b""
    source = inspect.getsource(standin)
    assert "supplied ==" not in source and "supplied !=" not in source and "== expected" not in source and "!= expected" not in source


def test_an_app_without_a_token_does_not_start(tmp_path):
    for token in ("", None, " "):
        with pytest.raises(ValueError, match="token"):
            standin_app(tmp_path / "x.sqlite", token)


# --- the shape of Frappe's API -----------------------------------------------------------------------------------------------


def test_a_list_answers_data_with_the_name_only_unless_it_is_asked_for_more(world):
    body = world.get("/api/resource/Supplier", headers=AUTH).json()
    assert body == {"data": [{"name": "SUP-01"}, {"name": "SUP-02"}]}
    fields = world.get("/api/resource/Supplier", params={"fields": json.dumps(["name", "tax_id"])}, headers=AUTH).json()
    assert fields == {"data": [{"name": "SUP-01", "tax_id": "30000000007"}, {"name": "SUP-02", "tax_id": "30000000015"}]}
    everything = world.get("/api/resource/Supplier", params={"fields": json.dumps(["*"])}, headers=AUTH).json()["data"][0]
    assert everything["name"] == "SUP-01" and everything["supplier_name"] == "Proveedor SUP-01 (ficticio)" and everything["docstatus"] == 0 and everything["doctype"] == "Supplier"


def test_a_document_is_read_by_its_name_with_its_child_rows_named_and_ordered(world):
    body = world.get("/api/resource/Purchase Order/OC-2026-000001", headers=AUTH).json()
    doc = body["data"]
    assert doc["name"] == "OC-2026-000001" and doc["supplier"] == "SUP-01" and doc["currency"] == "ARS" and doc["docstatus"] == 1 and doc["doctype"] == "Purchase Order"
    assert [(r["idx"], r["item_code"], r["qty"], r["rate"], r["name"]) for r in doc["items"]] == [(1, "A1", 10, 100.0, "OC-2026-000001-1"), (2, "A2", 5, 20.5, "OC-2026-000001-2")]
    assert all(r["parent"] == "OC-2026-000001" and r["parenttype"] == "Purchase Order" and r["doctype"] == "Purchase Order Item" for r in doc["items"])
    assert doc["items"][0]["amount"] == 1000.0 and doc["items"][1]["amount"] == 102.5 and doc["total"] == 1102.5


def test_filters_in_both_of_frappes_forms_and_on_the_rows_of_a_child_table(world):
    def names(doctype, filters, **extra):
        response = world.get(f"/api/resource/{doctype}", params={"filters": json.dumps(filters), **extra}, headers=AUTH)
        assert response.status_code == 200, response.text
        return [row["name"] for row in response.json()["data"]]

    assert names("Supplier", [["tax_id", "=", "30000000015"]]) == ["SUP-02"]
    assert names("Supplier", [["Supplier", "tax_id", "=", "30000000015"]]) == ["SUP-02"]
    assert names("Supplier", [["tax_id", "!=", "30000000015"]]) == ["SUP-01"]
    assert names("Supplier", [["tax_id", "in", ["30000000007", "30000000015"]]]) == ["SUP-01", "SUP-02"]
    assert names("Supplier", [["tax_id", "=", "nobody"]]) == []
    assert names("Purchase Receipt", [["Purchase Receipt Item", "purchase_order", "=", "OC-2026-000001"]]) == ["REM-000001"]
    assert names("Purchase Receipt", [["Purchase Receipt Item", "purchase_order", "=", "OC-OTHER"]]) == []
    assert names("Purchase Receipt", [["Purchase Receipt Item", "purchase_order", "=", "OC-2026-000001"], ["posting_date", "<=", "2026-10-04"]]) == ["REM-000001"]
    assert names("Purchase Receipt", [["Purchase Receipt Item", "purchase_order", "=", "OC-2026-000001"], ["posting_date", "<=", "2026-10-03"]]) == []
    assert names("Purchase Receipt", [["posting_date", ">=", "2026-10-05"]]) == [] and names("Purchase Receipt", [["posting_date", ">", "2026-10-03"]]) == ["REM-000001"]
    assert names("Purchase Receipt", [["posting_date", "<", "2026-10-04"]]) == [] and names("Purchase Receipt", [["docstatus", "=", 1]]) == ["REM-000001"]
    assert names("Purchase Receipt", [["posting_date", ">=", "2026-10-04"]]) == ["REM-000001"] and names("Purchase Receipt", [["posting_date", ">", "2026-10-04"]]) == []
    assert names("Purchase Receipt", [["posting_date", "<=", "2026-10-04"]]) == ["REM-000001"] and names("Purchase Receipt", [["posting_date", "<", "2026-10-05"]]) == ["REM-000001"]
    assert names("Purchase Receipt", [["Purchase Receipt Item", "qty", ">=", 10]]) == ["REM-000001"] and names("Purchase Receipt", [["Purchase Receipt Item", "qty", "<", 10]]) == []
    assert names("Purchase Receipt", [["Purchase Receipt Item", "qty", "!=", 10]]) == []
    assert names("Purchase Order", [["Purchase Order Item", "qty", ">", 9]]) == ["OC-2026-000001"] and names("Purchase Order", [["Purchase Order Item", "qty", ">", 10]]) == []


def test_a_filter_that_is_not_supported_is_417_and_says_nothing_of_the_query(world):
    for filters in ('[["Purchase Receipt Item", "nope", "=", 1]]', '[["Purchase Order Item", "qty", "=", 1]]', '[["nope", "=", 1]]', '[["tax_id", "LIKE", "x"]]', '[["tax_id"]]', 'not json', '{"a": 1}', '[["Other Doctype", "tax_id", "=", "x"]]', '[["tax_id", "=", {"a": 1}]]',
                    '[["tax_id\') OR 1=1 --", "=", "x"]]'):
        response = world.get("/api/resource/Supplier", params={"filters": filters}, headers=AUTH)
        assert response.status_code == 417, filters
        assert set(response.json()) == {"exc_type", "message"} and response.json()["exc_type"] == "ValidationError"
        assert "SELECT" not in response.text and "Traceback" not in response.text
    for filters in ('[["Purchase Receipt Item", "nope", "=", 1]]', '[["Purchase Receipt Item", "name\') OR 1=1 --", "=", 1]]', '[["Purchase Order Item", "qty", "=", 1]]'):
        assert world.get("/api/resource/Purchase Receipt", params={"filters": filters}, headers=AUTH).status_code == 417, filters
    assert world.get("/api/resource/Supplier", params={"fields": json.dumps(["nope"])}, headers=AUTH).status_code == 417
    assert world.get("/api/resource/Supplier", params={"fields": json.dumps(["name') FROM x --"])}, headers=AUTH).status_code == 417
    assert world.get("/api/resource/Supplier", params={"limit_page_length": "x"}, headers=AUTH).status_code == 417


def test_a_list_has_twenty_rows_a_page_unless_the_page_length_says_otherwise_and_zero_is_all(world):
    for number in range(25):
        assert post(world, "Supplier", supplier(f"S-{number:02d}", f"3000000{number:04d}")).status_code == 200
    def names(**params):
        return [r["name"] for r in world.get("/api/resource/Supplier", params=params, headers=AUTH).json()["data"]]

    assert len(names()) == 20
    assert len(names(limit_page_length=0)) == 27
    assert len(names(limit_page_length=5)) == 5 and names(limit_page_length=5, limit_start=5) == names(limit_page_length=10)[5:]
    assert names(limit_start=25, limit_page_length=0) == names(limit_page_length=0)[25:]
    assert names(limit_page_length=0) == sorted(names(limit_page_length=0)), "a stable order: by name"


def test_a_post_answers_200_with_the_created_document_as_a_draft_with_its_rows_named(world):
    response = post(world, "Purchase Invoice", invoice())
    assert response.status_code == 200
    doc = response.json()["data"]
    assert doc["doctype"] == "Purchase Invoice" and doc["docstatus"] == 0 and doc["name"].startswith("ACC-PINV-2026-") and doc["supplier"] == "SUP-01" and doc["bill_no"] == "00001-00000010"
    assert doc["items"][0]["name"] == f"{doc['name']}-1" and doc["items"][0]["idx"] == 1 and doc["items"][0]["parent"] == doc["name"]
    assert doc["items"][0]["amount"] == 1000.0 and doc["total"] == 1000.0 and doc["grand_total"] == 1000.0
    assert world.get(f"/api/resource/Purchase Invoice/{doc['name']}", headers=AUTH).json()["data"] == doc, "what is read is what was created"
    second = post(world, "Purchase Invoice", invoice("00001-00000011")).json()["data"]
    assert second["name"] != doc["name"] and second["name"].startswith("ACC-PINV-2026-")


def test_amounts_are_exact_decimals_through_the_json_numbers(world):
    rows = [{"item_code": "A1", "qty": 3, "rate": 0.1, "purchase_order": "OC-2026-000001"}, {"item_code": "A2", "qty": 1.5, "rate": 123456789012.34, "purchase_order": "OC-2026-000001"}]
    doc = post(world, "Purchase Invoice", invoice(items=rows)).json()
    data = json.loads(world.get(f"/api/resource/Purchase Invoice/{doc['data']['name']}", headers=AUTH).text, parse_float=Decimal)["data"]
    assert [i["amount"] for i in data["items"]] == [Decimal("0.3"), Decimal("185185183518.51")], "qty x rate rounded half up to the cent, as the ERP does"
    assert data["total"] == Decimal("185185183518.81")


def test_a_supplier_invoice_number_that_is_already_there_for_the_same_supplier_is_409(world):
    """The rule of ERPNext «supplier invoice number is unique», on in its settings (plan 12): another supplier may use the same number; a cancelled invoice (docstatus 2) does not count."""
    assert post(world, "Purchase Invoice", invoice()).status_code == 200
    again = post(world, "Purchase Invoice", invoice())
    assert again.status_code == 409 and again.json()["exc_type"] == "DuplicateEntryError" and set(again.json()) == {"exc_type", "message"}
    assert post(world, "Purchase Invoice", invoice(sup="SUP-02")).status_code == 200
    assert post(world, "Purchase Invoice", invoice("00001-00000012")).status_code == 200
    listed = world.get("/api/resource/Purchase Invoice", params={"limit_page_length": 0}, headers=AUTH).json()["data"]
    assert len(listed) == 3


def test_two_documents_of_a_doctype_cannot_share_a_name(world):
    for doctype, doc in (("Supplier", supplier()), ("Item", item()), ("Purchase Order", po()), ("Purchase Receipt", receipt())):
        assert post(world, doctype, doc).status_code == 409, doctype


def test_a_document_that_links_to_what_is_not_there_or_is_not_complete_is_417(world):
    cases = [("Purchase Invoice", {**invoice(), "supplier": "NOBODY"}), ("Purchase Invoice", {**invoice(), "items": []}), ("Purchase Invoice", {**invoice(), "items": None}),
             ("Purchase Invoice", {**invoice(), "items": [{"item_code": "ZZ", "qty": 1, "rate": 1}]}), ("Purchase Invoice", {**invoice(), "items": [{"item_code": "A1", "qty": "x", "rate": 1}]}),
             ("Purchase Invoice", {**invoice(), "items": [{"item_code": "A1", "qty": 0, "rate": 1}]}), ("Purchase Invoice", {**invoice(), "items": [{"item_code": "A1", "qty": 1, "rate": -1}]}),
             ("Purchase Invoice", {**invoice(), "items": [{"item_code": "A1", "qty": 1, "rate": 1, "purchase_order": "OC-NOPE"}]}),
             ("Purchase Invoice", {k: v for k, v in invoice().items() if k != "bill_no"}), ("Purchase Invoice", {**invoice(), "bill_date": "05/10/2026"}), ("Purchase Invoice", {**invoice(), "bill_no": 5}),
             ("Purchase Invoice", invoice(docstatus=1)), ("Purchase Invoice", {**invoice(), "docstatus": 2}), ("Purchase Order", {**po("OC-X"), "supplier": "NOBODY"}),
             ("Purchase Receipt", {**receipt("REM-X"), "items": [{"item_code": "ZZ", "qty": 1}]}), ("Supplier", {"name": "S"}), ("Item", {"name": "I"}), ("Supplier", ["not", "an", "object"])]
    for doctype, doc in cases:
        response = post(world, doctype, doc)
        assert response.status_code == 417, (doctype, doc)
        assert set(response.json()) == {"exc_type", "message"} and response.json()["exc_type"] in ("ValidationError", "LinkValidationError")
    assert world.get("/api/resource/Purchase Invoice", params={"limit_page_length": 0}, headers=AUTH).json() == {"data": []}, "nothing was written"


def test_the_exchange_rate_of_an_invoice_is_a_positive_number(world):
    for rate in (0, -1, "1", None):
        assert post(world, "Purchase Invoice", {**invoice(), "conversion_rate": rate}).status_code == 417, rate
    assert post(world, "Purchase Invoice", {**invoice(), "conversion_rate": 1000.5}).status_code == 200


def test_a_body_that_is_not_json_or_is_too_big_is_refused(world):
    assert world.post("/api/resource/Supplier", content=b"not json", headers={**AUTH, "Content-Type": "application/json"}).status_code == 417
    big = world.post("/api/resource/Supplier", content=b'{"x": "' + b"a" * 2_000_000 + b'"}', headers={**AUTH, "Content-Type": "application/json"})
    assert big.status_code == 413 and set(big.json()) == {"exc_type", "message"}


# --- what is not there ---------------------------------------------------------------------------------------------------------


def test_a_resource_or_a_document_that_is_not_there_is_404_without_its_path(world):
    for path in ("/api/resource/Nothing", "/api/resource/Nothing/x", "/api/resource/Supplier/NOBODY", "/api/resource/Purchase Order/OC-NOPE", "/nowhere", "/api/resource", "/api/resource/Supplier/a/b"):
        response = world.get(path, headers=AUTH)
        assert response.status_code == 404, path
        assert response.json() == {"exc_type": "DoesNotExistError", "message": "No encontrado"}
        assert "nowhere" not in response.text and "NOBODY" not in response.text


def test_the_methods_the_client_does_not_use_are_refused_and_nothing_changes(world):
    assert world.put("/api/resource/Supplier/SUP-01", content=b"{}", headers=AUTH).status_code in (404, 405)
    assert world.delete("/api/resource/Supplier/SUP-01", headers=AUTH).status_code in (404, 405)
    assert world.get("/api/resource/Supplier/SUP-01", headers=AUTH).json()["data"]["tax_id"] == "30000000007"


def test_an_internal_error_is_500_without_a_trace_and_without_a_path(tmp_path, monkeypatch):
    http = testclient.TestClient(standin_app(tmp_path / "x.sqlite", TOKEN), raise_server_exceptions=False)

    def boom(*args, **kwargs):
        raise RuntimeError(f"disk exploded at {tmp_path}")

    monkeypatch.setattr(standin, "_list", boom)
    response = http.get("/api/resource/Supplier", headers=AUTH)
    assert response.status_code == 500 and response.json() == {"exc_type": "InternalError", "message": "Error interno"}
    assert str(tmp_path) not in response.text and "disk exploded" not in response.text


def test_the_documents_survive_the_app_and_the_apps_are_independent(tmp_path):
    path = tmp_path / "erp" / "standin.sqlite"
    first = testclient.TestClient(standin_app(path, TOKEN))
    assert first.post("/api/resource/Supplier", content=json.dumps(supplier()), headers={**AUTH, "Content-Type": "application/json"}).status_code == 200
    again = testclient.TestClient(standin_app(path, "other:token"))
    assert again.get("/api/resource/Supplier", headers={"Authorization": "token other:token"}).json() == {"data": [{"name": "SUP-01"}]}
    assert again.get("/api/resource/Supplier", headers=AUTH).status_code == 401, "the token is the one the app was given, not the file's"
    elsewhere = testclient.TestClient(standin_app(tmp_path / "other.sqlite", TOKEN))
    assert elsewhere.get("/api/resource/Supplier", headers=AUTH).json() == {"data": []}


def test_the_stand_in_serves_only_the_five_doctypes_of_the_client(world):
    for doctype in ("Supplier", "Item", "Purchase Order", "Purchase Receipt", "Purchase Invoice"):
        assert world.get(f"/api/resource/{doctype}", headers=AUTH).status_code == 200, doctype
    for doctype in ("User", "DocType", "Sales Invoice", "File"):
        assert world.get(f"/api/resource/{doctype}", headers=AUTH).status_code == 404, doctype


def test_a_zero_rate_is_allowed_a_zero_quantity_is_not_and_the_cent_rounds_half_up(world):
    free = [{"item_code": "A1", "qty": 2, "rate": 0, "purchase_order": "OC-2026-000001"}]
    doc = post(world, "Purchase Invoice", invoice("00001-00000020", items=free)).json()["data"]
    assert doc["items"][0]["amount"] == 0 and doc["total"] == 0
    half = [{"item_code": "A1", "qty": 3, "rate": 0.335, "purchase_order": "OC-2026-000001"}, {"item_code": "A2", "qty": 1, "rate": 0.125, "purchase_order": "OC-2026-000001"}]
    data = json.loads(post(world, "Purchase Invoice", invoice("00001-00000021", items=half)).text, parse_float=Decimal)["data"]
    assert [i["amount"] for i in data["items"]] == [Decimal("1.01"), Decimal("0.13")] and data["total"] == Decimal("1.14"), "1.005 and 0.125 round up, not to the even cent"
    assert post(world, "Purchase Invoice", invoice("00001-00000022", items=[{"item_code": "A1", "qty": 0, "rate": 1}])).status_code == 417


def test_the_names_a_caller_may_give_are_plain_and_a_series_name_counts_up_by_year(world):
    for name in ("a b", "x/y", "../x", "", "-x", "x" * 141, "x;y"):
        assert post(world, "Purchase Order", {**po(), "name": name}).status_code == 417, name
    first = post(world, "Purchase Order", {k: v for k, v in po().items() if k != "name"}).json()["data"]["name"]
    second = post(world, "Purchase Order", {k: v for k, v in po().items() if k != "name"}).json()["data"]["name"]
    other_year = post(world, "Purchase Order", {k: v for k, v in {**po(), "transaction_date": "2025-01-02"}.items() if k != "name"}).json()["data"]["name"]
    assert (first, second, other_year) == ("PUR-ORD-2026-00001", "PUR-ORD-2026-00002", "PUR-ORD-2025-00001")
