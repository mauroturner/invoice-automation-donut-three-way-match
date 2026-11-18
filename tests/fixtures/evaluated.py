"""The state the star model, the dashboard design and the site files are made from (U17, U18): the three days of the quick world issued by a perfect reader, a registered model and its launch
seal, and the close evaluated once. Built once per session (``tests/conftest.py::evaluated``); a test that changes anything copies what it needs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from invoiceflow import daily, reporting
from invoiceflow.config import PipelineConfig, SealedConfig
from invoiceflow.registry import RunMeta, register_model, write_launch_seal
from tests.fixtures.daily_world import DailyWorld, build_world, fresh_erp, load_files, open_warehouse
from tests.fixtures.projects import ProjectFolder, make_project
from tests.fixtures.seals import launch_document


@dataclass
class Evaluated:
    view: SealedConfig
    cfg: PipelineConfig
    world: DailyWorld
    conn: sqlite3.Connection
    db: Path
    seal: Path
    run: RunMeta
    launch: dict
    project: ProjectFolder
    root: Path


class Reads:
    """A model of the tests that answers one CORD tree whatever it is shown."""

    def __init__(self, tree):
        self.tree = tree

    def extract(self, image):
        return self.tree


def build_evaluated(view: SealedConfig, root: Path) -> Evaluated:
    world = build_world(view, root / "world")
    folder = root / "run"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily, "read_qr", world.read_qr)
        client, _ = fresh_erp(world, folder)
        conn = open_warehouse(folder)
        for day in world.days:
            spec = world.specs()[day]
            run, inbox = load_files(world, conn, spec, [t.invoice_key for t in world.truths[day]], folder)
            daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox)
    project = make_project(root / "project")
    seal = root / "reports" / "model_seal.json"
    launch = launch_document(view)
    write_launch_seal(seal, launch)
    register_model(conn, {"model_version": launch["model_version"], "trained_as_of": "2026-10-01 00:00:00", "artifact_name": launch["artifact_name"], "artifact_sha256": launch["artifact_sha256"],
                          "tokenizer_sha256": launch["tokenizer_sha256"], "config_sha256": launch["config_sha256"], "chosen_epoch": launch["chosen_epoch"],
                          "choice_json": json.dumps({"choice": launch["choice"], "checkpoint_cord_val": launch["checkpoint_cord_val"], "oracle_validation": launch["oracle_validation"]}, sort_keys=True),
                          "validation_digest": launch["validation_digest"]})
    close = RunMeta("run-close", datetime(2026, 10, 6))
    reporting.evaluate(conn, project.cfg, view, close, world.master, world.all_truths(), seal_path=seal, chosen=Reads({"total": {"total_price": "1"}}), checkpoint=Reads({}))
    return Evaluated(view, project.cfg, world, conn, folder / "warehouse.sqlite", seal, close, launch, project, root)
