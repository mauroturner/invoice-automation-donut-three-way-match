"""The summary after the seal (U16; AC24, D21): a warehouse that does not hold the sealed rows (a new one, a quick copy, the site's build) takes C01-C09 from the seal, each one rebuilt from its
metrics and compared with its own entry to 6 decimals; a seal the code cannot rebuild is never published."""

from __future__ import annotations

import copy
import json

import pytest

from invoiceflow import reporting
from invoiceflow.evaluation import criteria_from_seal
from invoiceflow.registry import SealError, write_launch_seal, write_test_seal
from tests.fixtures.seals import launch_document, make_test_part
from tests.integration.test_daily import open_warehouse


@pytest.fixture
def sealed(quick_view, tmp_path):
    seal = tmp_path / "reports" / "model_seal.json"
    launch = launch_document(quick_view)
    write_launch_seal(seal, launch)
    write_test_seal(seal, make_test_part(launch))
    return seal, launch


def test_a_warehouse_without_sealed_rows_takes_the_sealed_result_from_the_seal(quick_view, sealed, tmp_path):
    seal, launch = sealed
    conn = open_warehouse(tmp_path)
    assert conn.execute("SELECT count(*) FROM eval_criteria").fetchone()[0] == 0
    summary = reporting.build_summary(conn, quick_view, seal_path=seal)
    assert summary["source"] == "seal" and summary["synthetic"] is True and summary["config_sha256"] == launch["config_sha256"]
    ids = [c["criterion_id"] for c in summary["criteria"]]
    assert ids == [f"C{n:02d}" for n in range(1, 11)] and {c["criterion_id"]: c["passed"] for c in summary["criteria"]}["C03"] is False
    rebuilt = criteria_from_seal(json.loads(seal.read_text(encoding="utf-8")))
    assert [(c.criterion_id, c.value, c.passed, c.verdict_es) for c in rebuilt] == [(c["criterion_id"], c["value"], c["passed"], c["verdict_es"]) for c in summary["criteria"]]
    text = reporting.summary_markdown(summary)
    assert "no se cumple" in text.lower() and "(sintético)" in text and "C03" in text and text.count("|") > 20


def test_a_seal_that_cannot_be_rebuilt_from_its_own_metrics_is_not_taken(quick_view, sealed, tmp_path):
    seal, launch = sealed
    document = json.loads(seal.read_text(encoding="utf-8"))
    document["test"]["metrics"]["lines"]["known"]["rate"] = 0.5
    seal.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SealError, match="C02"):
        criteria_from_seal(document)
    with pytest.raises(SealError, match="C02"):
        reporting.build_summary(open_warehouse(tmp_path), quick_view, seal_path=seal)
    with pytest.raises(SealError, match="parte de la prueba"):
        criteria_from_seal({"launch": document["launch"]})


def test_before_the_close_the_summary_says_when_the_test_is_evaluated_and_shows_the_validation_figures_with_their_splits(quick_view, tmp_path):
    seal = tmp_path / "reports" / "model_seal.json"
    write_launch_seal(seal, launch_document(quick_view))
    summary = reporting.build_summary(open_warehouse(tmp_path), quick_view, seal_path=seal)
    assert summary["source"] == "pending" and all(c["passed"] is False or c["passed"] is None for c in summary["criteria"])
    text = reporting.summary_markdown(summary)
    assert "La prueba se evalúa con el cierre del 2026-10-06" in text and "cord_train+synth_train" in text and "validación" in text
