"""The days of the test as the daily run sees them (U15): the invoices of each day rendered as files, the extractor of the tests that reads them (with the errors a test seeds), an ERP that
holds the whole master (the receipts of later days too: what the match may know is the match's business, AC11) and a warehouse into which a delivery is loaded the way ``load`` does."""

from __future__ import annotations

import dataclasses
import hashlib
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from invoiceflow import warehouse
from invoiceflow.config import SealedConfig
from invoiceflow.deliveries import DeliverySpec, delivery_plan
from invoiceflow.baselines import truth_qr
from invoiceflow.intake import inspect_file, load_page
from invoiceflow.registry import RunMeta
from invoiceflow.synth.invoices import invoices
from invoiceflow.synth.master import master as make_master
from tests.fixtures.erp_world import load_world, standin_client
from tests.fixtures.loads import report_of
from tests.fixtures.seals import write_launch_seal
from tests.fixtures.stub_extractor import EXTENSIONS, StubExtractor, page_key

CONFIG_SHA = "b" * 64


@dataclass
class DailyWorld:
    view: SealedConfig
    master: object
    days: list[date]
    truths: dict[date, list]            # day -> the truths of that day, in invoice_key order
    files: Path                         # every page, ``<invoice_key>.<ext>``
    extractor: StubExtractor
    erp_template: Path
    root: Path
    qr_by_page: dict

    def read_qr(self, image, view):
        """The QR of a page as the generator printed it (the pages of the quick configuration are drawn at 50 dpi, where no reader can see a QR: reading it is ``tests/unit/test_qr.py``'s)."""
        read = self.qr_by_page[page_key(image)]
        return dataclasses.replace(read, reader="pyzbar") if read.status == "ok" else read

    def specs(self) -> dict[date, DeliverySpec]:
        return {spec.day: spec for spec in delivery_plan(self.view) if spec.kind == "daily"}

    def all_truths(self) -> list:
        return [t for day in self.days for t in self.truths[day]]


def build_world(view: SealedConfig, root: Path, *, errors: dict[str, str] | None = None) -> DailyWorld:
    master = make_master(view)
    seal = write_launch_seal(root / "seal" / "model_seal.json", view)
    specs = [s for s in delivery_plan(view) if s.kind == "daily"]
    truths = {s.day: sorted(invoices(view, master, "test_known", s.day, seal_path=seal) + invoices(view, master, "test_new", s.day, seal_path=seal), key=lambda t: t.invoice_key)
              for s in specs}
    everything = [t for s in specs for t in truths[s.day]]
    files = root / "files"
    extractor = StubExtractor.from_truths(everything, view, files, errors=errors)
    erp = load_world(view, master, everything, root / "template")
    qr = {page_key(load_page(files / f"{t.invoice_key}{EXTENSIONS[t.file_format]}", view)): truth_qr(t, view) for t in everything}
    return DailyWorld(view, master, [s.day for s in specs], truths, files, extractor, erp.db, root, qr)


def fresh_erp(world: DailyWorld, folder: Path):
    """A stand-in with the master of the world, a copy of the template: its client and its HTTP front."""
    target = folder / "erp" / "standin.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(world.erp_template, target)
    return standin_client(target)


def open_warehouse(folder: Path) -> sqlite3.Connection:
    conn = warehouse.connect(folder / "warehouse.sqlite")
    warehouse.apply_schema(conn)
    return conn


def load_files(world: DailyWorld, conn: sqlite3.Connection, spec: DeliverySpec, keys: list[str], folder: Path, *, run_key: str | None = None,
               bad: dict[str, bytes] | None = None) -> tuple[RunMeta, Path]:
    """Copy the pages of `keys` into the inbox of the delivery `spec` and load the delivery into the warehouse with the report of their real inspection. `bad` maps a file name (with its extension)
    to bytes that replace its content (a file the intake quarantines). Returns the run and the inbox."""
    inbox = folder / "inbox" / spec.delivery_id
    inbox.mkdir(parents=True, exist_ok=True)
    for source in sorted(world.files.iterdir()):
        if source.stem in keys:
            (inbox / source.name).write_bytes((bad or {}).get(source.name, source.read_bytes()))
    checks = tuple(inspect_file(path, world.view) for path in sorted(inbox.iterdir()))
    run_key = run_key or f"run-{spec.delivery_id}"
    from datetime import datetime

    warehouse.load_delivery(conn, spec, report_of(spec, run_key, checks), run_key, started_at=datetime(spec.day.year, spec.day.month, spec.day.day), config_sha256=CONFIG_SHA,
                            sha256=hashlib.sha256(spec.delivery_id.encode()).hexdigest())
    return RunMeta(run_key, spec.as_of), inbox


def with_days(view: SealedConfig, last: date) -> SealedConfig:
    return dataclasses.replace(view, calendar=dataclasses.replace(view.calendar, test_last_day=last))


FLOW_TABLES = ("extracted_fields", "qr_reads", "decisions", "decision_reasons", "erp_posts", "daily_ops")


def snapshot(conn: sqlite3.Connection) -> dict[str, list]:
    return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall() for table in FLOW_TABLES}


def decision_rows(conn: sqlite3.Connection) -> list:
    """What was decided, without the run and the seconds (what one way of delivering must share with another)."""
    return conn.execute("SELECT d.invoice_key, d.decision, d.first_reason, d.reasons, d.po_number, d.supplier_cuit, d.total, d.currency, "
                        "(SELECT group_concat(reason || ':' || rank || ':' || detail_json, '|') FROM (SELECT * FROM decision_reasons r WHERE r.invoice_key = d.invoice_key ORDER BY rank)), "
                        "(SELECT status || ':' || coalesce(erp_name, '') FROM erp_posts p WHERE p.invoice_key = d.invoice_key) "
                        "FROM decisions d ORDER BY d.invoice_key").fetchall()
