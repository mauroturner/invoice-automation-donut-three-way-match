"""The ERP client: Frappe's REST API as the flow needs it, the idempotent post with its read-back and the loading of the master (RF14, RF24; AC15, AC16; plan section 12; decisions D16, D17, D50).

``ErpClient`` is built on an ``httpx.Client``: the real one for ERPNext (``[erp] mode = "http"``) or the ``TestClient`` of the stand-in in the same process (``"standin"``); both speak
``GET /api/resource/<DocType>?filters=[[...]]&fields=[...]``, ``GET /api/resource/<DocType>/<name>`` and ``POST /api/resource/<DocType>`` with ``{"data": ...}`` back and
``Authorization: token <key>:<secret>`` on every request. Numbers are read as ``Decimal`` (``parse_float``), never as ``float``, and sent as JSON numbers (a ``Decimal`` of at most 15 digits goes through
``float`` and back exactly). Every list is asked with ``limit_page_length=0`` (Frappe's default page is 20 rows).

* ``post_invoice`` is the one way an invoice is loaded, and it is idempotent: it looks for the supplier invoice number of the same supplier first and does not create what is there (``already_posted``);
  it creates only a **draft** (``docstatus`` 0, ``[erp] post_as = "draft"``: a document of any other status is refused before a request is made); it reads it back and compares the supplier, the
  number, the date, the currency, the total and every line with what it sent. What the ERP refuses, what it does not answer and what it answers differently are ``post_failed`` with the stage
  (``rejected``, ``unreachable``, ``read_back``); a bug in this code is not one of them and is raised.
* ``events_of`` and ``apply_master_event`` load the master (suppliers, items, orders, receipts) in the order it happened; ``invoice_doc`` builds the document of an invoice from what was read.

What was checked against Frappe's documentation (2026-11-18, D50) and what was not (the real answer of a duplicate supplier invoice number, the child-table filters and the naming of a document by the
caller) is in D50: the smoke test against ERPNext v15 (U22) settles them.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote

import httpx

from invoiceflow.config import ErpConfig, PathsConfig
from invoiceflow.match import ISO_CURRENCY, ErpView, _po_line, bill_no
from invoiceflow.normalize import InvoiceFields
from invoiceflow.qr import QrPayload

CENT = Decimal("0.01")
SUPPLIER, ITEM, ORDER, RECEIPT, INVOICE = "Supplier", "Item", "Purchase Order", "Purchase Receipt", "Purchase Invoice"
POST_STATUSES = ("posted", "already_posted", "post_failed")
JSON = {"Content-Type": "application/json"}
ARCA_CURRENCY = {iso: code for code, iso in ISO_CURRENCY.items()}


class ErpError(RuntimeError):
    """The ERP did not do what was asked. ``status`` is the HTTP status when there was an answer; the message never holds a body, a path or a token."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ErpAuthError(ErpError):
    """401 or 403: the token is not accepted."""


class ErpRejected(ErpError):
    """The ERP answered that it will not do it (409 for a name or a supplier invoice number that is taken, 417 for a document that is not valid, any other 4xx)."""


class ErpUnreachable(ErpError):
    """No answer (a connection that failed or timed out) or a 5xx."""


class ErpConfigError(ValueError):
    """``[erp]`` cannot be used as it is (no address, no token); the command line exits with 2 before writing."""

    EXIT_CODE = 2


@dataclass(frozen=True)
class PostOutcome:
    status: str                              # "posted", "already_posted" or "post_failed"
    erp_name: str | None                     # the name the ERP gave the draft (none when it never had one)
    readback_equal: bool | None              # None when nothing was read back
    stage: str | None = None                 # for post_failed: "rejected", "unreachable" or "read_back"
    differences: tuple[str, ...] = ()        # what the read-back found different, by field


@dataclass(frozen=True)
class ErpEvent:
    kind: str                                # "supplier", "item", "po" or "receipt"
    document: Any                            # the master's own object
    supplier_key: str | None = None          # of a receipt: the supplier of its order


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _once(rows: list[dict[str, Any]]) -> list[str]:
    """The names of `rows` without repeats, in order: a filter on a child table lists the parent once per matching row in ERPNext (D50), and a document must be read and counted once."""
    return list(dict.fromkeys(row["name"] for row in rows))


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 10**15 and value.as_tuple().exponent >= 0 else float(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} no se manda al ERP")


def _body(document: Mapping[str, Any]) -> str:
    return json.dumps(document, default=_encode, ensure_ascii=False)


def _require_a_draft(document: Mapping[str, Any]) -> None:
    if document.get("docstatus", 0) != 0:
        raise ErpRejected("solo se carga un borrador (docstatus 0; [erp] post_as = draft)")


class ErpClient:
    def __init__(self, http: httpx.Client, token: str) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ErpConfigError("el cliente del ERP necesita un token")
        self._http, self._headers = http, {"Authorization": f"token {token}", "Accept": "application/json"}

    # -- the requests ----------------------------------------------------------------------------------------------------------

    def _request(self, method: str, doctype: str, name: str | None = None, **kwargs: Any) -> Any:
        path = f"/api/resource/{quote(doctype)}" + (f"/{quote(name, safe='')}" if name is not None else "")
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise ErpUnreachable(f"el ERP no respondió ({type(error).__name__})") from None
        status = response.status_code
        if status in (401, 403):
            raise ErpAuthError(f"el ERP no acepta el token (estado {status})", status)
        if status >= 500:
            raise ErpUnreachable(f"el ERP falló (estado {status})", status)
        if status >= 400:
            raise ErpRejected(f"el ERP rechazó el pedido (estado {status})", status)
        try:
            return json.loads(response.text, parse_float=Decimal)["data"]
        except (ValueError, KeyError, TypeError):
            raise ErpUnreachable("el ERP contestó algo que no es {\"data\": …}", status) from None

    def _list(self, doctype: str, filters: list[list[Any]], fields: list[str]) -> list[dict[str, Any]]:
        data = self._request("GET", doctype, params={"filters": json.dumps(filters, default=_encode), "fields": json.dumps(fields), "limit_page_length": 0})
        if not isinstance(data, list):
            raise ErpUnreachable("el ERP contestó una lista que no es una lista")
        return data

    def _get(self, doctype: str, name: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", doctype, name)
        except ErpRejected as error:
            if error.status == 404:
                return None
            raise

    # -- what the match asks ---------------------------------------------------------------------------------------------------

    def supplier_by_cuit(self, cuit: str) -> dict[str, Any] | None:
        """The supplier whose ``tax_id`` is the 11 digits `cuit`, or None; two suppliers with one CUIT are a mistake of the master data and an ``ErpError``."""
        found = self._list(SUPPLIER, [["tax_id", "=", cuit]], ["name", "supplier_name", "tax_id"])
        if len(found) > 1:
            raise ErpError("hay más de un proveedor con el mismo CUIT en el ERP")
        return found[0] if found else None

    def purchase_order(self, name: str) -> dict[str, Any] | None:
        """The purchase order named `name` with its rows, or None."""
        return self._get(ORDER, name)

    def receipts(self, po_name: str, until: date) -> list[dict[str, Any]]:
        """The submitted purchase receipts of the order dated on or before `until`, each with its rows, by date and name."""
        rows = self._list(RECEIPT, [["Purchase Receipt Item", "purchase_order", "=", po_name], ["posting_date", "<=", until.isoformat()], ["docstatus", "=", 1]], ["name", "posting_date"])
        documents = [self._get(RECEIPT, name) for name in _once(rows)]
        return sorted((doc for doc in documents if doc is not None), key=lambda doc: (doc["posting_date"], doc["name"]))

    def find_invoice(self, supplier: str, bill_no: str) -> dict[str, Any] | None:
        """The purchase invoice of `supplier` with the supplier invoice number `bill_no` that is not cancelled, or None."""
        found = self._list(INVOICE, [["supplier", "=", supplier], ["bill_no", "=", bill_no], ["docstatus", "!=", 2]], ["name", "supplier", "bill_no", "docstatus"])
        return found[0] if found else None

    def invoiced_quantities(self, po_name: str) -> dict[str, Decimal]:
        """What was already invoiced against the order: the quantity of each item of its non-cancelled purchase invoices."""
        rows = self._list(INVOICE, [["Purchase Invoice Item", "purchase_order", "=", po_name], ["docstatus", "!=", 2]], ["name"])
        quantities: dict[str, Decimal] = {}
        for name in _once(rows):
            document = self._get(INVOICE, name)
            for item in (document or {}).get("items", []):
                if item.get("purchase_order") == po_name:
                    quantities[item["item_code"]] = quantities.get(item["item_code"], Decimal(0)) + _decimal(item["qty"])
        return quantities

    # -- what the flow writes --------------------------------------------------------------------------------------------------

    def create_invoice(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """Create `document` as a purchase invoice **draft**. A document that is not a draft is refused here, before any request."""
        _require_a_draft(document)
        return self._request("POST", INVOICE, content=_body({**document, "docstatus": 0}), headers=JSON)

    def read_invoice(self, name: str) -> dict[str, Any]:
        found = self._get(INVOICE, name)
        if found is None:
            raise ErpRejected("la factura que se creó no está en el ERP", 404)
        return found

    def post_invoice(self, document: Mapping[str, Any]) -> PostOutcome:
        """Load `document` once: ``already_posted`` if the ERP has that supplier invoice number of that supplier and it is what is sent, ``posted`` if it created it and read it back equal;
        ``post_failed`` with its stage when the ERP refuses, does not answer or holds something different. Calling it again with the same document creates nothing. A token the ERP does not
        accept (``ErpAuthError``) is not an invoice's failure and is raised."""
        try:
            _require_a_draft(document)
            existing = self.find_invoice(document["supplier"], document["bill_no"])
            name = existing["name"] if existing is not None else self.create_invoice(document)["name"]
            read = self.read_invoice(name)
        except ErpUnreachable:
            return PostOutcome("post_failed", None, None, "unreachable")
        except ErpRejected:
            return PostOutcome("post_failed", None, None, "rejected")
        differences = invoice_differences(document, read)
        if differences:
            return PostOutcome("post_failed", read["name"], False, "read_back", tuple(differences))
        return PostOutcome("already_posted" if existing is not None else "posted", read["name"], True)

    def apply_master_event(self, event: ErpEvent) -> None:
        """Load one supplier, item, order or receipt of the master (a launch or a daily delivery loads them in the order they happened)."""
        document = event.document
        if event.kind == "supplier":
            self._request("POST", SUPPLIER, content=_body({"name": document.supplier_key, "supplier_name": document.name_es, "tax_id": document.cuit}),
                          headers=JSON)
        elif event.kind == "item":
            self._request("POST", ITEM, content=_body({"item_code": document.code, "item_name": document.description_es, "stock_uom": document.unit, "standard_rate": document.list_price}),
                          headers=JSON)
        elif event.kind == "po":
            rows = [{"item_code": line.item_code, "item_name": line.description_es, "uom": line.unit, "qty": line.qty, "rate": line.unit_price} for line in document.lines]
            self._request("POST", ORDER, content=_body({"name": document.po_number, "supplier": document.supplier_key, "transaction_date": document.po_date,
                                                        "currency": ISO_CURRENCY.get(document.currency, document.currency), "docstatus": 1, "items": rows}),
                          headers=JSON)
        elif event.kind == "receipt":
            rows = [{"item_code": line.item_code, "qty": line.qty, "purchase_order": document.po_number} for line in document.lines]
            self._request("POST", RECEIPT, content=_body({"name": document.receipt_number, "supplier": event.supplier_key, "posting_date": document.receipt_date, "docstatus": 1, "items": rows}),
                          headers=JSON)
        else:
            raise ValueError(f"evento del maestro desconocido: {event.kind!r}")


# --- the master as events ------------------------------------------------------------------------------------------------------------------


def events_of(master: Any, until: date | None = None) -> list[ErpEvent]:
    """The master as the ERP would have learned it: every supplier and every item first, then each order and receipt on its date (an order before a receipt of the same day), up to `until` included
    when it is given. A receipt carries the key of the supplier of its order."""
    orders = {po.po_number: po for po in master.orders}
    events: list[ErpEvent] = [ErpEvent("supplier", supplier) for supplier in master.suppliers] + [ErpEvent("item", item) for item in master.items]
    for event in master.events:
        if until is not None and event.date > until:
            continue
        if event.kind == "po":
            events.append(ErpEvent("po", event.document))
        else:
            events.append(ErpEvent("receipt", event.document, orders[event.document.po_number].supplier_key))
    return events


# --- the invoice's document and its read-back --------------------------------------------------------------------------------------


def invoice_doc(fields: InvoiceFields, payload: QrPayload, view: ErpView, supplier: str) -> dict[str, Any]:
    """The purchase invoice draft for an invoice the flow decided to post: the supplier's name in the ERP, the number, the dates and the currency from the QR (the QR wins, D06), each line
    linked to its row of the order and to the receipt that received its item. A line that is not on the order cannot be here (the match refuses it)."""
    po = view.po
    items = []
    for line in fields.lines:
        ordered = _po_line(po, line)
        if ordered is None:
            raise ValueError("una línea que no es de la orden no se carga (el cruce la rechaza)")
        receipt = next((r for r in sorted(view.receipts_until_day, key=lambda r: (r.date, r.number)) if any(x.item_code == ordered.item_code for x in r.lines)), None)
        items.append({"item_code": ordered.item_code, "qty": line.cantidad, "rate": line.precio_unitario, "purchase_order": po.number, "po_detail": ordered.row,
                      "purchase_receipt": receipt.number if receipt else None})
    return {"supplier": supplier, "bill_no": bill_no(payload.pto_vta, payload.nro_cmp), "bill_date": payload.fecha, "posting_date": view.day,
            "currency": ISO_CURRENCY.get(payload.moneda, payload.moneda), "conversion_rate": payload.ctz, "docstatus": 0,
            "items": [{k: v for k, v in item.items() if v is not None} for item in items]}


def _day(value: Any) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def invoice_differences(sent: Mapping[str, Any], read: Mapping[str, Any]) -> list[str]:
    """The fields in which `read` (what the ERP holds) differs from `sent` (what was decided): the supplier, the supplier invoice number, the date, the currency, the total (the sum of each line's
    ``qty x rate`` to the cent) and, line by line, the item, the quantity, the rate and the links; empty when it is the same."""
    differences = [name for name in ("supplier", "bill_no", "currency") if sent.get(name) != read.get(name)]
    if _day(sent.get("bill_date")) != _day(read.get("bill_date")):
        differences.append("bill_date")
    lines, held = list(sent.get("items", [])), list(read.get("items", []))
    expected = sum(((_decimal(i["qty"]) * _decimal(i["rate"])).quantize(CENT, rounding=ROUND_HALF_UP) for i in lines), Decimal(0))
    if read.get("total") is None or _decimal(read["total"]) != expected:
        differences.append("total")
    if len(lines) != len(held):
        differences.append("items")
        return differences
    for index, (a, b) in enumerate(zip(lines, held, strict=True)):
        for key in ("item_code", "purchase_order", "po_detail", "purchase_receipt"):
            if a.get(key) != b.get(key):
                differences.append(f"items[{index}].{key}")
        for key in ("qty", "rate"):
            if _decimal(a[key]) != _decimal(b.get(key, "NaN")):
                differences.append(f"items[{index}].{key}")
    return differences


# --- making a client ------------------------------------------------------------------------------------------------------------


def make_client(erp: ErpConfig, paths: PathsConfig, *, env: Mapping[str, str] | None = None) -> ErpClient:
    """The client of ``[erp] mode``: ``standin`` is the stand-in over ``[paths] erp_db`` called in this process (its token from ``[erp] token_env`` or, if the variable is not set, one made for
    this process: nobody else can call it); ``http`` is ``[erp] base_url`` with the token of ``token_env`` (``ErpConfigError`` without it), with ``[erp] timeout_s``."""
    env = os.environ if env is None else env
    token = env.get(erp.token_env, "")
    if erp.mode == "http":
        if not erp.base_url:
            raise ErpConfigError("[erp] mode = http necesita [erp] base_url")
        if not token.strip():
            raise ErpConfigError(f"[erp] mode = http necesita el token en la variable de entorno {erp.token_env}")
        return ErpClient(httpx.Client(base_url=erp.base_url, timeout=erp.timeout_s), token)
    if erp.mode != "standin":
        raise ErpConfigError(f"[erp] mode: {erp.mode!r} no es standin ni http")
    from fastapi.testclient import TestClient

    from invoiceflow.erp.standin import standin_app

    token = token if token.strip() else f"standin:{secrets.token_hex(16)}"
    return ErpClient(TestClient(standin_app(paths.erp_db, token), raise_server_exceptions=False, base_url="http://standin"), token)
