"""A stand-in ERP loaded with the master of the quick configuration, the invoices to post and what the flow does with one (U13): ``erp_view``, ``decide``, ``invoice_doc`` and ``post_invoice``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from invoiceflow.baselines import truth_fields, truth_qr
from invoiceflow.decide import Decision, decide
from invoiceflow.erp.client import ErpClient, PostOutcome, events_of, invoice_doc
from invoiceflow.erp.standin import standin_app
from invoiceflow.match import ErpView, erp_view

TOKEN = "key123:secret456"


@dataclass
class ErpWorld:
    client: ErpClient
    http: TestClient
    master: Any
    truths: list
    view: Any
    db: Path


def standin_client(path: Path, transport: httpx.BaseTransport | None = None) -> tuple[ErpClient, TestClient]:
    http = TestClient(standin_app(path, TOKEN), raise_server_exceptions=False, base_url="http://standin")
    return ErpClient(http if transport is None else httpx.Client(base_url="http://standin", transport=transport), TOKEN), http


def relevant_events(master, truths):
    """The part of the master that these invoices need: their orders and receipts, the suppliers and the items of those."""
    orders = {t.po_number for t in truths} | {t.header.orden_compra for t in truths if t.header.orden_compra}
    by_number = {po.po_number: po for po in master.orders}
    suppliers = {by_number[o].supplier_key for o in orders if o in by_number} | {t.supplier_key for t in truths}
    items = {line.item_code for o in orders if o in by_number for line in by_number[o].lines} | {line.code for t in truths for line in t.lines}
    out = []
    for event in events_of(master):
        document = event.document
        if event.kind == "supplier" and document.supplier_key in suppliers or event.kind == "item" and document.code in items:
            out.append(event)
        elif event.kind == "po" and document.po_number in orders or event.kind == "receipt" and document.po_number in orders:
            out.append(event)
    return out


def load_world(view, master, truths, tmp_path: Path) -> ErpWorld:
    client, http = standin_client(tmp_path / "erp" / "standin.sqlite")
    for event in relevant_events(master, truths):
        client.apply_master_event(event)
    return ErpWorld(client, http, master, list(truths), view, tmp_path / "erp" / "standin.sqlite")


def decide_and_post(world: ErpWorld, truth, *, conn=None) -> tuple[Decision, ErpView, PostOutcome | None, dict | None]:
    """What the flow does for one invoice with true fields: the view from the ERP, the decision and, if it is auto_post, the post."""
    fields, qr = truth_fields(truth), truth_qr(truth, world.view)
    view = erp_view(world.client, fields, qr, truth.received_on, conn, invoice_key=truth.invoice_key)
    decision = decide(fields, qr, view, world.view)
    if decision.decision != "auto_post":
        return decision, view, None, None
    document = invoice_doc(fields, qr.payload, view, view.supplier_by_cuit[qr.payload.cuit])
    return decision, view, world.client.post_invoice(document), document


def order_of_arrival(truths):
    return sorted(truths, key=lambda t: (t.received_on, t.invoice_key))


def day(text: str) -> date:
    return date.fromisoformat(text)
