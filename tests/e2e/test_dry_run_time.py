"""The dry run of the daily time (U15; plan section 14, lesson B4 of project 06: the daily time was measured only with the real data): a daily run over invoices of ``validation_known`` in a temporary
copy (the test is not touched), with the real Donut on the GPU, the real QR readers and the stand-in ERP, and the seconds per invoice that it takes. Marks ``model`` and ``gpu``: it reads the pinned
checkpoint (the published one: the launch's fine-tuned model is U22's, and it writes the full invoice where the checkpoint writes a receipt, so this is the time of everything but the longer
decoding). The result is printed and recorded in decision D52; the plan asks the next unit to fix it if it is over 3.5 s."""

from __future__ import annotations

import dataclasses
import statistics
import time

import pytest

from invoiceflow import daily
from invoiceflow.model.train import load_base
from tests.fixtures.daily_world import DailyWorld, fresh_erp, load_files, open_warehouse
from tests.fixtures.erp_world import load_world
from tests.fixtures.seals import write_launch_seal
from tests.fixtures.stub_extractor import StubExtractor
from invoiceflow.deliveries import delivery_plan
from invoiceflow.synth.invoices import invoices

pytestmark = [pytest.mark.model, pytest.mark.gpu]
INVOICES = 12
WARMUP = 2     # pages the extractor reads before the clock starts (the first ones warm the GPU)
LIMIT_SECONDS = 3.5


def test_a_daily_run_with_the_real_model_takes_at_most_3_5_seconds_per_invoice(view, default_master, tmp_path, capsys):
    from invoiceflow.model.donut import DonutExtractor

    truths = sorted(invoices(view, default_master, "validation_known", None, seal_path=tmp_path / "none.json"), key=lambda t: t.invoice_key)[:INVOICES]
    seal = write_launch_seal(tmp_path / "seal" / "model_seal.json", view)
    files = tmp_path / "files"
    StubExtractor.from_truths(truths, view, files)
    erp = load_world(view, default_master, truths, tmp_path / "template")
    spec = next(s for s in delivery_plan(view) if s.kind == "daily")
    world = DailyWorld(view, default_master, [spec.day], {spec.day: truths}, files, None, erp.db, tmp_path, {})
    client, _ = fresh_erp(world, tmp_path)
    conn = open_warehouse(tmp_path)
    run, inbox = load_files(world, conn, spec, [t.invoice_key for t in truths], tmp_path)
    processor, model = load_base(view)
    height, width = view.model.input_size
    processor.image_processor.size = {"height": height, "width": width}
    import torch

    model.eval().to("cuda")
    extractor = DonutExtractor(model=model, processor=processor, task_token=view.model.task_cord, max_length=view.model.max_length, device="cuda", model_version="checkpoint",
                               no_grad=torch.no_grad)
    keys = [t.invoice_key for t in truths]
    from invoiceflow.intake import load_page

    for path in sorted(inbox.iterdir())[:WARMUP]:
        extractor.extract(load_page(path, view))
    start = time.perf_counter()
    result = daily.run_day(conn, client, extractor, run, view, spec, inbox, invoice_keys=keys)
    wall = time.perf_counter() - start
    per_invoice = statistics.fmean(result.seconds)
    with capsys.disabled():
        print(f"\ndry run: {result.invoices} invoices, mean {per_invoice:.2f} s, max {max(result.seconds):.2f} s, wall {wall / result.invoices:.2f} s per invoice, "
              f"{torch.cuda.get_device_name(0)}")
    assert result.invoices == INVOICES and per_invoice <= LIMIT_SECONDS
