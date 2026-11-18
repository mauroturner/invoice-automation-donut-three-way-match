"""The stand-in of the ERP: the shape of Frappe's REST API for the five doctypes the client uses, over SQLite (RF14, RF24; AC16; plan sections 12 and 18; decisions D16, D17, D27, D50).

``standin_app(db_path, token)`` is a FastAPI application that serves ``GET /api/resource/<DocType>`` (a list: ``filters`` in Frappe's two forms, ``fields``, ``limit_page_length`` at 20 by default and 0 for
all, ``limit_start``), ``GET /api/resource/<DocType>/<name>`` and ``POST /api/resource/<DocType>`` (a document in the body, ``{"data": <the document>}`` back with status 200, as Frappe answers),
for ``Supplier``, ``Item``, ``Purchase Order``, ``Purchase Receipt`` and ``Purchase Invoice``, and nothing else. Documents are JSON in one table; the child rows (``items``) get their ``name``, ``idx``,
``parent`` and ``amount``; a purchase invoice is created as a draft (``docstatus`` 0 is the only value accepted), its numbers are exact decimals (each line ``qty x rate`` to the cent, half up, and their sum).

* Every request needs ``Authorization: token <token>``, compared with ``hmac.compare_digest``; anything else, a route that does not exist included, is 401 before the route is looked at.
* 404 for a resource or a document that is not there, 409 for a name that is taken and for a **supplier invoice number that is already there for the same supplier** (ERPNext's rule «supplier invoice
  number is unique», which its settings turn on; a cancelled invoice does not count), 417 for a document that is not valid or links to what is not there, 413 for a body over a megabyte.
* An error is ``{"exc_type", "message"}`` with a fixed message: never a path, a query, a trace or what was sent.

A stand-in is not ERPNext: it does not compute taxes, does not submit, has no workflow and answers 409 where ERPNext's validation answers its own status (D50). It is the ERP of the suite and of the
quick mode; the real one answers the smoke test of U22.
"""

from __future__ import annotations

import hmac
import json
import re
import sqlite3
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response

MAX_BODY_BYTES = 1_000_000
DEFAULT_PAGE = 20
MAX_IN = 100
CENT = Decimal("0.01")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,139}")
OPERATORS = {"=": "=", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}

CHILD = {"Purchase Order": "Purchase Order Item", "Purchase Receipt": "Purchase Receipt Item", "Purchase Invoice": "Purchase Invoice Item"}
PARENT_FIELDS: dict[str, set[str]] = {
    "Supplier": {"name", "supplier_name", "tax_id", "docstatus"},
    "Item": {"name", "item_code", "item_name", "stock_uom", "standard_rate", "docstatus"},
    "Purchase Order": {"name", "supplier", "transaction_date", "currency", "total", "grand_total", "docstatus"},
    "Purchase Receipt": {"name", "supplier", "posting_date", "docstatus"},
    "Purchase Invoice": {"name", "supplier", "bill_no", "bill_date", "posting_date", "currency", "conversion_rate", "total", "grand_total", "docstatus"},
}
CHILD_FIELDS: dict[str, set[str]] = {
    "Purchase Order Item": {"name", "idx", "item_code", "item_name", "uom", "qty", "rate", "amount"},
    "Purchase Receipt Item": {"name", "idx", "item_code", "uom", "qty", "purchase_order"},
    "Purchase Invoice Item": {"name", "idx", "item_code", "uom", "qty", "rate", "amount", "purchase_order", "po_detail", "purchase_receipt"},
}
SERIES = {"Purchase Order": ("PUR-ORD", "transaction_date"), "Purchase Receipt": ("MAT-PRE", "posting_date"), "Purchase Invoice": ("ACC-PINV", "posting_date")}
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS docs (doctype TEXT NOT NULL, name TEXT NOT NULL, docstatus INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (doctype, name))",
    "CREATE TABLE IF NOT EXISTS counters (series TEXT PRIMARY KEY, n INTEGER NOT NULL)",
)


class ErpFault(Exception):
    """An error the stand-in answers with its status and a fixed message."""

    def __init__(self, status: int, exc_type: str, message: str) -> None:
        super().__init__(exc_type)
        self.status, self.exc_type, self.message = status, exc_type, message


def _number(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 10**15 and value.as_tuple().exponent >= 0 else float(value)
    raise TypeError(type(value).__name__)


def _dumps(value: Any) -> str:
    return json.dumps(value, default=_number, ensure_ascii=False, separators=(",", ":"))


def _reply(status: int, payload: Any) -> Response:
    return Response(content=_dumps(payload), status_code=status, media_type="application/json")


def _error(status: int, exc_type: str, message: str) -> Response:
    return _reply(status, {"exc_type": exc_type, "message": message})


def _invalid(message: str = "Documento no válido") -> ErpFault:
    return ErpFault(417, "ValidationError", message)


# --- reading -----------------------------------------------------------------------------------------------------------------


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")   # a stand-in is regenerated by the replay: atomic (rollback journal), not guaranteed to survive a power cut
    return conn


def _scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, dict | list):
        raise _invalid("Filtro no admitido")
    return float(value) if isinstance(value, Decimal) else value


def _condition(doctype: str, flt: Any) -> tuple[str, list[Any]]:
    """One filter of Frappe's list, ``[field, op, value]`` or ``[doctype, field, op, value]``, as SQL on the stored JSON; anything else is a 417 that says nothing of the query."""
    if not isinstance(flt, list) or len(flt) not in (3, 4):
        raise _invalid("Filtro no admitido")
    scope, field, op, value = (doctype, *flt) if len(flt) == 3 else flt
    if not (isinstance(scope, str) and isinstance(field, str) and isinstance(op, str)):
        raise _invalid("Filtro no admitido")
    child = CHILD.get(doctype)
    if scope == doctype and field in PARENT_FIELDS[doctype]:
        expr = field if field in ("name", "docstatus") else "json_extract(docs.data, ?)"
        wrap = None
        names = [] if field in ("name", "docstatus") else [f"$.{field}"]
    elif child is not None and scope == child and field in CHILD_FIELDS[child]:
        expr, wrap, names = "json_extract(row.value, ?)", True, [f"$.{field}"]
    else:
        raise _invalid("Filtro no admitido")
    lowered = op.lower()
    if lowered == "in":
        if not isinstance(value, list) or not value or len(value) > MAX_IN:
            raise _invalid("Filtro no admitido")
        values = [_scalar(v) for v in value]
        test = f"{expr} IN ({', '.join('?' for _ in values)})"
    elif lowered in OPERATORS:
        values = [_scalar(value)]
        test = f"{expr} {OPERATORS[lowered]} ?"
    else:
        raise _invalid("Filtro no admitido")
    if wrap:
        return f"EXISTS (SELECT 1 FROM json_each(docs.data, '$.items') AS row WHERE {test})", [*names, *values]
    return test, [*names, *values]


def _int_param(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise _invalid("Parámetro no admitido") from None
    if value < 0:
        raise _invalid("Parámetro no admitido")
    return value


def _json_param(request: Request, name: str, default: Any) -> Any:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        return json.loads(raw, parse_float=Decimal)
    except ValueError:
        raise _invalid("Parámetro no admitido") from None


def _list(conn: sqlite3.Connection, doctype: str, request: Request) -> list[dict[str, Any]]:
    filters = _json_param(request, "filters", [])
    fields = _json_param(request, "fields", ["name"])
    if not isinstance(filters, list) or not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
        raise _invalid("Parámetro no admitido")
    if fields != ["*"] and not all(f in PARENT_FIELDS[doctype] for f in fields):
        raise _invalid("Campo no admitido")
    page, start = _int_param(request, "limit_page_length", DEFAULT_PAGE), _int_param(request, "limit_start", 0)
    where, params = ["docs.doctype = ?"], [doctype]
    for flt in filters:
        sql, more = _condition(doctype, flt)
        where.append(sql)
        params += more
    query = f"SELECT docs.data FROM docs WHERE {' AND '.join(where)} ORDER BY docs.name"
    if page:
        query += f" LIMIT {page} OFFSET {start}"
    elif start:
        query += f" LIMIT -1 OFFSET {start}"
    docs = [json.loads(row[0], parse_float=Decimal) for row in conn.execute(query, params)]
    return docs if fields == ["*"] else [{f: doc.get(f) for f in fields} for doc in docs]


def _get(conn: sqlite3.Connection, doctype: str, name: str) -> dict[str, Any]:
    row = conn.execute("SELECT data FROM docs WHERE doctype = ? AND name = ?", (doctype, name)).fetchone()
    if row is None:
        raise ErpFault(404, "DoesNotExistError", "No encontrado")
    return json.loads(row[0], parse_float=Decimal)


# --- writing -----------------------------------------------------------------------------------------------------------------


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 140:
        raise _invalid()
    return value


def _day(value: Any) -> str:
    try:
        return date.fromisoformat(_text(value)).isoformat()
    except ValueError:
        raise _invalid() from None


def _amount(value: Any, *, positive: bool) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise _invalid()
    value = Decimal(value)
    if not value.is_finite() or (value <= 0 if positive else value < 0) or abs(value) >= Decimal(10) ** 15:
        raise _invalid()
    return value


def _exists(conn: sqlite3.Connection, doctype: str, name: str) -> bool:
    return conn.execute("SELECT 1 FROM docs WHERE doctype = ? AND name = ?", (doctype, name)).fetchone() is not None


def _link(conn: sqlite3.Connection, doctype: str, name: Any) -> str:
    if not isinstance(name, str) or not _exists(conn, doctype, name):
        raise ErpFault(417, "LinkValidationError", "Vínculo no válido")
    return name


def _rows(conn: sqlite3.Connection, doctype: str, body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    raw = body.get("items")
    if not isinstance(raw, list) or not raw or len(raw) > 500 or not all(isinstance(row, dict) for row in raw):
        raise _invalid()
    rows = []
    for idx, row in enumerate(raw, start=1):
        qty = _amount(row.get("qty"), positive=True)
        out: dict[str, Any] = {"name": f"{name}-{idx}", "idx": idx, "doctype": CHILD[doctype], "parent": name, "parenttype": doctype, "parentfield": "items",
                               "item_code": _link(conn, "Item", row.get("item_code")), "qty": qty}
        if doctype != "Purchase Receipt":
            rate = _amount(row.get("rate"), positive=False)
            out["rate"], out["amount"] = rate, (qty * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        if row.get("uom") is not None:
            out["uom"] = _text(row["uom"])
        if row.get("item_name") is not None:
            out["item_name"] = _text(row["item_name"])
        for link, linked in (("purchase_order", "Purchase Order"), ("purchase_receipt", "Purchase Receipt")):
            if row.get(link) is not None and (link == "purchase_order" or doctype == "Purchase Invoice"):
                out[link] = _link(conn, linked, row[link])
        if row.get("po_detail") is not None and doctype == "Purchase Invoice":
            out["po_detail"] = _text(row["po_detail"])
        rows.append(out)
    return rows


def _next_name(conn: sqlite3.Connection, doctype: str, body: dict[str, Any]) -> str:
    prefix, field = SERIES[doctype]
    series = f"{prefix}-{_day(body.get(field))[:4]}"
    conn.execute("INSERT INTO counters (series, n) VALUES (?, 1) ON CONFLICT (series) DO UPDATE SET n = n + 1", (series,))
    return f"{series}-{conn.execute('SELECT n FROM counters WHERE series = ?', (series,)).fetchone()[0]:05d}"


def _document(conn: sqlite3.Connection, doctype: str, body: Any) -> tuple[str, dict[str, Any]]:
    """The document to store for `body`, validated, with its name; nothing is written."""
    if not isinstance(body, dict):
        raise _invalid()
    docstatus = body.get("docstatus", 0)
    if isinstance(docstatus, bool) or docstatus not in ((0,) if doctype == "Purchase Invoice" else (0, 1)):
        raise _invalid("docstatus no válido")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    doc: dict[str, Any] = {"doctype": doctype, "docstatus": docstatus, "creation": now, "modified": now, "owner": "Administrator"}
    if doctype == "Supplier":
        doc.update(supplier_name=_text(body.get("supplier_name")), tax_id=_text(body.get("tax_id")))
        name = _text(body.get("name") or doc["supplier_name"])
    elif doctype == "Item":
        name = _text(body.get("item_code"))
        doc.update(item_code=name, item_name=_text(body.get("item_name") or name), stock_uom=_text(body.get("stock_uom") or "u"))
        if body.get("standard_rate") is not None:
            doc["standard_rate"] = _amount(body["standard_rate"], positive=False)
    else:
        name = None if body.get("name") is None else _text(body["name"])
        doc["supplier"] = _link(conn, "Supplier", body.get("supplier"))
        if doctype == "Purchase Order":
            doc.update(transaction_date=_day(body.get("transaction_date")), currency=_text(body.get("currency")))
        elif doctype == "Purchase Receipt":
            doc.update(posting_date=_day(body.get("posting_date")))
        else:
            doc.update(bill_no=_text(body.get("bill_no")), bill_date=_day(body.get("bill_date")), posting_date=_day(body.get("posting_date") or body.get("bill_date")),
                       currency=_text(body.get("currency")), conversion_rate=_amount(body.get("conversion_rate", 1), positive=True))
        if name is None:
            name = _next_name(conn, doctype, doc if doctype != "Purchase Order" else {"transaction_date": doc["transaction_date"]})
    if not NAME.fullmatch(name):
        raise _invalid()
    doc["name"] = name
    if doctype in CHILD:
        doc["items"] = _rows(conn, doctype, body, name)
        if doctype != "Purchase Receipt":
            doc["total"] = doc["grand_total"] = sum((row["amount"] for row in doc["items"]), Decimal(0))
    return name, doc


def _create(conn: sqlite3.Connection, doctype: str, body: Any) -> dict[str, Any]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        name, doc = _document(conn, doctype, body)
        if _exists(conn, doctype, name):
            raise ErpFault(409, "DuplicateEntryError", "Ya existe")
        if doctype == "Purchase Invoice":
            taken = conn.execute("SELECT 1 FROM docs WHERE doctype = 'Purchase Invoice' AND docstatus != 2 AND json_extract(data, '$.supplier') = ? AND json_extract(data, '$.bill_no') = ?",
                                 (doc["supplier"], doc["bill_no"])).fetchone()
            if taken is not None:
                raise ErpFault(409, "DuplicateEntryError", "Ya existe")
        conn.execute("INSERT INTO docs (doctype, name, docstatus, data) VALUES (?, ?, ?, ?)", (doctype, name, doc["docstatus"], _dumps(doc)))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return json.loads(_dumps(doc), parse_float=Decimal)


# --- the application ---------------------------------------------------------------------------------------------------------


def standin_app(db_path: Path, token: str | None) -> FastAPI:
    """The stand-in over the SQLite file `db_path` (created with its folder) that answers only to ``Authorization: token <token>``. Without a token it does not start."""
    if not isinstance(token, str) or not token.strip():
        raise ValueError("el sustituto del ERP necesita un token (sin él no arranca)")
    expected = f"token {token}".encode()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    setup = _connect(db_path)
    try:
        for statement in SCHEMA:
            setup.execute(statement)
    finally:
        setup.close()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        supplied = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(supplied, expected):
            return _error(401, "AuthenticationError", "Credenciales no válidas")
        return await call_next(request)

    @app.exception_handler(ErpFault)
    async def fault(request: Request, error: ErpFault):
        return _error(error.status, error.exc_type, error.message)

    @app.exception_handler(Exception)
    async def crash(request: Request, error: Exception):
        return _error(500, "InternalError", "Error interno")

    def known(doctype: str) -> str:
        if doctype not in PARENT_FIELDS:
            raise ErpFault(404, "DoesNotExistError", "No encontrado")
        return doctype

    @app.get("/api/resource/{doctype}")
    def list_documents(doctype: str, request: Request) -> Response:
        conn = _connect(db_path)
        try:
            return _reply(200, {"data": _list(conn, known(doctype), request)})
        finally:
            conn.close()

    @app.get("/api/resource/{doctype}/{name}")
    def read_document(doctype: str, name: str) -> Response:
        conn = _connect(db_path)
        try:
            return _reply(200, {"data": _get(conn, known(doctype), name)})
        finally:
            conn.close()

    @app.post("/api/resource/{doctype}")
    async def create_document(doctype: str, request: Request) -> Response:
        doctype = known(doctype)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return _error(413, "RequestTooLarge", "Pedido demasiado grande")
        try:
            body = json.loads(raw, parse_float=Decimal)
        except (ValueError, InvalidOperation):
            raise _invalid() from None
        conn = _connect(db_path)
        try:
            return _reply(200, {"data": _create(conn, doctype, body)})
        finally:
            conn.close()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def nothing_else(path: str) -> Response:
        """Whatever is not one of the routes above, whatever its method, is the same 404 that says nothing of the path."""
        return _error(404, "DoesNotExistError", "No encontrado")

    return app
