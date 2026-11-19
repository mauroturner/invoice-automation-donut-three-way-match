"""The smoke test against ERPNext v15 in Docker (U22; AC36; plan section 12; decisions D16, D50). Mark ``erpnext``: not in the default suite, and run once.

``docker/erpnext/compose.yaml`` is the stack (MariaDB, two Redis, the configurator, the site and the backend, pinned by version and digest, every secret from the environment, the one port on 127.0.0.1).
The test starts it, completes ERPNext's setup wizard (a company, a fiscal year and the chart of accounts: a purchase invoice needs them), makes an API key for the Administrator and then uses the
**same ``ErpClient`` as the flow** over HTTP: it creates a supplier, two items, an order and a receipt, posts two purchase invoices as drafts, reads them back and posts the second again, which creates
nothing. What it settles about the four points D50 left open is asserted here, so that a different ERPNext would fail the test rather than the flow:

* (a) a filter on a child table in its four-element form is accepted and filters, and it lists the parent once per matching row (the client reads each document once);
* (b) a repeated supplier invoice number is **not** refused by ERPNext's default settings (a second draft is created); with ``check_supplier_invoice_uniqueness`` on it is a 417 ``ValidationError``,
  not the stand-in's 409, and the client treats both the same way;
* (c) a ``name`` sent by the caller is ignored for suppliers, orders, receipts and invoices (the series name the document), it is used for items (named by their code), and ``docstatus: 1`` is
  accepted on creation for orders and receipts;
* (d) with a company set up, the invoice needs neither a ``company`` nor an account: the company, the payable account and the expense account come by default, and the row of the order is accepted as ``po_detail``.

When everything passed it leaves ``reports/erpnext_smoke.json`` (the result the site's ``operation.json`` publishes). Everything it creates it removes, also when it fails: the stack with its volumes and its
network, and the image of ERPNext when this test pulled it (the images of MariaDB and Redis that were already there stay); it refuses to start when the compose project already has containers, volumes or
networks, and it checks at the end that the containers, volumes and networks of the machine are the ones it found. The build cache of Docker is not an image, a volume or a network and is not touched.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from invoiceflow.erp.client import ErpClient, ErpEvent
from invoiceflow.files import write_atomic
from invoiceflow.site_export import SMOKE_FILE, read_smoke
from invoiceflow.synth.cuit import synthetic_cuit

PROJECT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = PROJECT / "docker" / "erpnext"
COMPOSE = COMPOSE_DIR / "compose.yaml"
PROJECT_NAME = "invoiceflow-erpnext"                  # the compose file's ``name``: the label of everything the stack creates
SITE = "frontend"
IMAGE = "frappe/erpnext:v15.124.4@sha256:656ccfd15dd98a184f1874f5b3797d31386c3d54a18def64c9594c7588ab55d7"
EXTERNAL = (                                           # what the stack needs besides ERPNext: removed only if this test pulled it
    "mariadb:10.6@sha256:f1c56f035ed268a0d7c2c2e06dc879f6319b997cb434cf822a4cfd72a7aa184d",
    "redis:7.2.11-alpine@sha256:1a34bdba051ecd8a58ec8a3cc460acef697a1605e918149cc53d920673c1a0a7",
)
UP_TIMEOUT_S = 1800
STEP_TIMEOUT_S = 600
TEARDOWN_TIMEOUT_S = 300
D = Decimal

pytestmark = pytest.mark.erpnext


def docker(*args: str, env: dict[str, str] | None = None, timeout: int = STEP_TIMEOUT_S, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["docker", *args], cwd=COMPOSE_DIR, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout, check=check, encoding="utf-8")
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"docker {' '.join(args)} did not end in {timeout} s:\n" + str(error.stdout or "")[-1500:] + str(error.stderr or "")[-1500:])
    except subprocess.CalledProcessError as error:
        pytest.fail(f"docker {' '.join(args)} returned {error.returncode}:\n" + (error.stdout or "")[-2000:] + (error.stderr or "")[-2000:])


def image_exists(reference: str) -> bool:
    return docker("image", "inspect", reference, check=False).returncode == 0


def names(*command: str) -> set[str]:
    return set(docker(*command, check=False).stdout.split())


def machine_state() -> dict[str, set[str]]:
    """The containers, volumes and networks of the machine (everything, not only this project's)."""
    return {"containers": names("ps", "-a", "-q", "--no-trunc"), "volumes": names("volume", "ls", "-q"), "networks": names("network", "ls", "-q", "--no-trunc")}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def teardown(env: dict[str, str], *, pulled: list[str]) -> list[str]:
    """Remove the stack with its volumes and network and the images this test pulled; return what could not be done. Each step is tried even if another failed."""
    problems: list[str] = []
    done = docker("compose", "down", "--volumes", "--remove-orphans", env=env, check=False, timeout=TEARDOWN_TIMEOUT_S)
    if done.returncode != 0:
        problems.append("docker compose down --volumes returned " + str(done.returncode) + ":\n" + (done.stdout + done.stderr)[-2000:])
    for reference in pulled:
        done = docker("image", "rm", reference, check=False, timeout=TEARDOWN_TIMEOUT_S)
        if done.returncode != 0:
            problems.append(f"docker image rm {reference} returned {done.returncode}:\n" + (done.stdout + done.stderr)[-1000:])
    return problems


@pytest.fixture(scope="module")
def erpnext():
    """The stack up, its site set up and an API key for the Administrator: ``.client`` (the flow's ``ErpClient`` over HTTP), ``.http`` (the same connection, for what the test looks at by hand),
    ``.versions`` and ``.exec`` (a ``bench`` command in the backend)."""
    assert COMPOSE.is_file(), "falta docker/erpnext/compose.yaml: la pila de ERPNext v15 de la prueba de humo no existe (D16, D50)"
    label = f"label=com.docker.compose.project={PROJECT_NAME}"
    for kind, command in {"containers": ("ps", "-a", "-q"), "volumes": ("volume", "ls", "-q"), "networks": ("network", "ls", "-q")}.items():
        assert not names(*command, "--filter", label), f"el proyecto de compose {PROJECT_NAME} ya tiene {kind}: la prueba no los toca; quítelos antes"
    before = machine_state()
    pulled = [reference for reference in (IMAGE, *EXTERNAL) if not image_exists(reference)]
    port = free_port()
    env = {"INVOICEFLOW_ERPNEXT_DB_PASSWORD": secrets.token_hex(16), "INVOICEFLOW_ERPNEXT_ADMIN_PASSWORD": secrets.token_urlsafe(16), "INVOICEFLOW_ERPNEXT_PORT": str(port)}
    http = None
    try:
        docker("compose", "up", "-d", env=env, timeout=UP_TIMEOUT_S)
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 300
        while True:
            try:
                if httpx.get(f"{base}/api/method/ping", headers={"X-Frappe-Site-Name": SITE}, timeout=10).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("el backend de ERPNext no contestó /api/method/ping en 300 s:\n" + docker("compose", "logs", "--tail", "40", "backend", env=env, check=False).stdout[-2000:])
            time.sleep(3)

        def bench(*command: str) -> str:
            return docker("compose", "exec", "-T", "backend", "bench", "--site", SITE, *command, env=env).stdout

        args = {"currency": "ARS", "full_name": "Smoke Admin", "company_name": "Invoiceflow Smoke", "timezone": "America/Argentina/Buenos_Aires", "company_abbr": "IFS", "domains": ["Distribution"],
                "country": "Argentina", "fy_start_date": "2026-01-01", "fy_end_date": "2026-12-31", "language": "English", "chart_of_accounts": "Standard", "email": "smoke@example.com",
                "password": secrets.token_urlsafe(12)}
        assert json.loads(bench("execute", "frappe.desk.page.setup_wizard.setup_wizard.setup_complete", "--kwargs", repr({"args": args})).strip().splitlines()[-1]) == {"status": "ok"}
        keys = json.loads(bench("execute", "frappe.core.doctype.user.user.generate_keys", "--args", "['Administrator']").strip().splitlines()[-1])
        token = f"{keys['api_key']}:{keys['api_secret']}"
        http = httpx.Client(base_url=base, headers={"X-Frappe-Site-Name": SITE}, timeout=120)
        versions = httpx.get(f"{base}/api/method/frappe.utils.change_log.get_versions", headers={"X-Frappe-Site-Name": SITE, "Authorization": f"token {token}"}, timeout=60).json()["message"]
        yield SimpleNamespace(client=ErpClient(http, token), http=http, token=token, versions=versions, bench=bench, port=port)
    finally:
        if http is not None:
            http.close()
        problems = teardown(env, pulled=pulled)
        after = machine_state()
        for kind in before:
            if after[kind] != before[kind]:
                problems.append(f"{kind}: the machine does not have what it had (left: {sorted(after[kind] - before[kind])}, gone: {sorted(before[kind] - after[kind])})")
        assert not problems, "the teardown did not leave Docker as it found it:\n" + "\n".join(problems)


def raw(erpnext, method: str, doctype: str, name: str | None = None, **kwargs) -> httpx.Response:
    path = f"/api/resource/{doctype}" + (f"/{name}" if name else "")
    return erpnext.http.request(method, path, headers={"Authorization": f"token {erpnext.token}", **kwargs.pop("headers", {})}, **kwargs)


def test_two_invoices_post_as_drafts_read_back_and_do_not_duplicate(erpnext):
    client = erpnext.client
    cuit = synthetic_cuit(11)
    supplier = client.apply_master_event(ErpEvent("supplier", SimpleNamespace(supplier_key="SUP-SMOKE", name_es="Proveedor de Humo SA (ficticio)", cuit=cuit)))
    for code, description, price in (("SMK-A1", "Tornillo de humo", D("10.00")), ("SMK-A2", "Tuerca de humo", D("5.50"))):
        item = client.apply_master_event(ErpEvent("item", SimpleNamespace(code=code, description_es=description, unit="Nos", list_price=price)))
        assert item["name"] == code, "(c) an item is named by its code, as the caller asked"
    lines = (SimpleNamespace(item_code="SMK-A1", description_es="Tornillo de humo", unit="Nos", qty=D("100"), unit_price=D("10.00")),
             SimpleNamespace(item_code="SMK-A2", description_es="Tuerca de humo", unit="Nos", qty=D("50"), unit_price=D("5.50")))
    order = client.apply_master_event(ErpEvent("po", SimpleNamespace(po_number="OC-SMOKE-1", supplier_key=supplier["name"], po_date=date(2026, 10, 1), currency="PES", lines=lines)))
    receipt = client.apply_master_event(ErpEvent("receipt", SimpleNamespace(receipt_number="REM-SMOKE-1", po_number=order["name"], receipt_date=date(2026, 10, 5),
                                                  lines=(SimpleNamespace(item_code="SMK-A1", qty=D("100")), SimpleNamespace(item_code="SMK-A2", qty=D("50")))), supplier["name"]))

    # (c) names and status: the series name the supplier's order, receipt and invoice, and an order and a receipt are accepted already submitted
    assert supplier["name"] == "Proveedor de Humo SA (ficticio)" and supplier["name"] != "SUP-SMOKE" and supplier["tax_id"] == cuit
    assert order["name"].startswith("PUR-ORD-2026-") and order["name"] != "OC-SMOKE-1" and receipt["name"].startswith("MAT-PRE-2026-") and receipt["name"] != "REM-SMOKE-1"
    assert (order["docstatus"], receipt["docstatus"]) == (1, 1) and order["currency"] == "ARS" and str(receipt["posting_date"]) == "2026-10-05"

    # what the client reads: the supplier by its CUIT, the order and its rows, and the receipts up to a day (a) with a filter on the child table in its four-element form
    assert client.supplier_by_cuit(cuit) == {"name": supplier["name"], "supplier_name": supplier["name"], "tax_id": cuit}
    held = client.purchase_order(order["name"])
    rows = {row["item_code"]: row["name"] for row in held["items"]}
    assert sorted(rows) == ["SMK-A1", "SMK-A2"] and held["items"][0]["qty"] == D("100")
    assert client.receipts(order["name"], date(2026, 10, 4)) == [] and [r["name"] for r in client.receipts(order["name"], date(2026, 10, 31))] == [receipt["name"]]
    listed = raw(erpnext, "GET", "Purchase Receipt", params={"filters": json.dumps([["Purchase Receipt Item", "purchase_order", "=", order["name"]]]), "fields": '["name"]', "limit_page_length": 0}).json()["data"]
    assert [row["name"] for row in listed] == [receipt["name"]] * 2, "(a) ERPNext lists a parent once for each of its rows that matches: two rows, two entries"

    def draft(bill_no: str, code: str, qty: str, rate: str) -> dict:
        return {"supplier": supplier["name"], "bill_no": bill_no, "bill_date": date(2026, 10, 6), "posting_date": date(2026, 10, 6), "currency": "ARS", "conversion_rate": D(1), "docstatus": 0,
                "items": [{"item_code": code, "qty": D(qty), "rate": D(rate), "purchase_order": order["name"], "po_detail": rows[code], "purchase_receipt": receipt["name"]}]}

    first, second = draft("00001-00000101", "SMK-A1", "40", "10.00"), draft("00001-00000102", "SMK-A2", "20", "5.50")
    posted = [client.post_invoice(first), client.post_invoice(second)]
    assert [(p.status, p.readback_equal, p.stage, p.differences) for p in posted] == [("posted", True, None, ())] * 2
    assert all(p.erp_name.startswith("ACC-PINV-2026-") for p in posted)

    # they are drafts the ERP read back with what was decided; (d) the company and the accounts came by default and the row of the order was accepted as ``po_detail``
    for outcome, sent in zip(posted, (first, second), strict=True):
        read = client.read_invoice(outcome.erp_name)
        assert read["docstatus"] == 0 and read["bill_no"] == sent["bill_no"] and read["supplier"] == supplier["name"] and read["currency"] == "ARS"
        assert read["company"] == "Invoiceflow Smoke" and read["credit_to"].startswith("Creditors - ") and read["items"][0]["expense_account"]
        assert read["items"][0]["po_detail"] == sent["items"][0]["po_detail"] and read["items"][0]["purchase_receipt"] == receipt["name"]
        assert read["total"] == sent["items"][0]["qty"] * sent["items"][0]["rate"]

    # the second load creates nothing, however many times it is asked
    assert [client.post_invoice(second).status for _ in range(2)] == ["already_posted"] * 2 and client.post_invoice(first).status == "already_posted"
    names_held = {row["name"] for row in client._list("Purchase Invoice", [["supplier", "=", supplier["name"]]], ["name"])}
    assert names_held == {p.erp_name for p in posted} and client.find_invoice(supplier["name"], second["bill_no"])["name"] == posted[1].erp_name
    assert client.invoiced_quantities(order["name"]) == {"SMK-A1": D("40"), "SMK-A2": D("20")}, "(a) what was invoiced counts each invoice once"

    # (b) a repeated supplier invoice number: ERPNext's default settings do not refuse it; with the uniqueness check on it is a 417, and the client's own search is what keeps the flow idempotent
    body = json.dumps({**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in second.items() if k != "items"}, "conversion_rate": 1,
                       "items": [{**row, "qty": float(row["qty"]), "rate": float(row["rate"])} for row in second["items"]]})
    duplicate = raw(erpnext, "POST", "Purchase Invoice", content=body, headers={"Content-Type": "application/json"})
    assert duplicate.status_code == 200 and duplicate.json()["data"]["docstatus"] == 0, "by default ERPNext creates a second draft with the same supplier invoice number"
    assert raw(erpnext, "DELETE", "Purchase Invoice", duplicate.json()["data"]["name"]).status_code in (200, 202)
    assert raw(erpnext, "PUT", "Accounts Settings", "Accounts Settings", content=json.dumps({"check_supplier_invoice_uniqueness": 1}), headers={"Content-Type": "application/json"}).status_code == 200
    refused = raw(erpnext, "POST", "Purchase Invoice", content=body, headers={"Content-Type": "application/json"})
    assert (refused.status_code, refused.json()["exc_type"]) == (417, "ValidationError"), "with the check on, ERPNext answers 417 ValidationError (the stand-in answers 409)"
    assert {row["name"] for row in client._list("Purchase Invoice", [["supplier", "=", supplier["name"]]], ["name"])} == names_held

    result = {"synthetic": True, "image": re.sub(r"@.*", "", IMAGE), "image_digest": IMAGE.split("@", 1)[1], "erpnext_version": erpnext.versions["erpnext"]["version"],
              "frappe_version": erpnext.versions["frappe"]["version"], "tested_on": date.today().isoformat(),
              "documents": {"suppliers": 1, "items": 2, "orders": 1, "receipts": 1, "invoices": len(names_held)}, "readback_equal": all(p.readback_equal for p in posted),
              "repost_creates_none": True, "duplicate_bill_no": {"default_settings_creates_a_duplicate": True, "status_with_uniqueness_on": refused.status_code}}
    target = PROJECT / "reports" / SMOKE_FILE
    target.parent.mkdir(exist_ok=True)
    write_atomic(target, json.dumps(result, ensure_ascii=False, sort_keys=True, indent=1) + "\n")       # an interrupted run never leaves a truncated file (U22a/S1)
    assert read_smoke(target) == result
