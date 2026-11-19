"""Runbook R11, «Reconstruir desde cero», followed literally on a copy of the quick replay (B3 of the evaluation; D62; plan section 25; AC39): what R11 says to move aside is moved, ``replay`` runs, the
sealed artifact is registered again from the seal without a fit, and the close reproduces the seal. A rebuild followed only in part never exits 0 having done nothing: it is refused before
anything is written, and the refusal names R11."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from invoiceflow import launch
from tests.e2e.cli_helpers import cli, config_of
from tests.fixtures.pipeline_state import copy_state

# R11, step 1: what is moved aside (never the seal, the sealed artifact, CORD or ARCA's specification)
MOVED = ("warehouse", "interim_dir", "deliveries", "erp_db")


def paths_of(state) -> dict[str, Path]:
    p = state.cfg.paths
    return {"warehouse": Path(p.warehouse), "interim_dir": Path(p.interim_dir), "deliveries": Path(p.raw_dir) / "deliveries", "erp_db": Path(p.erp_db)}


def move_aside(state, names, aside: Path) -> None:
    for name in names:
        path = paths_of(state)[name]
        shutil.move(str(path), str(aside / name))


def digest(root: Path) -> str:
    """The SHA-256 of every file under `root`, names included."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def rows(db: Path, sql: str) -> list:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


ISSUED = ("SELECT invoice_key, decision, first_reason FROM decisions ORDER BY invoice_key",
          "SELECT invoice_key, field, ordinal, value_norm FROM extracted_fields ORDER BY invoice_key, field, ordinal",
          "SELECT criterion_id, value, baseline_value, ci_low, ci_high, passed, verdict_es FROM eval_criteria WHERE seal_state = 'sealed' ORDER BY criterion_id",
          "SELECT baseline, cohort, auto_post, bad_post, bad_post_rate, ci_low, ci_high FROM eval_baseline WHERE seal_state = 'sealed' ORDER BY baseline, cohort",
          "SELECT model_version, trained_as_of, artifact_name, artifact_sha256, tokenizer_sha256, config_sha256, chosen_epoch, choice_json, validation_digest FROM model_registry")


def test_r11_as_written_rebuilds_from_the_seal_without_a_fit_and_the_close_reproduces_the_seal(pipeline_replayed, tmp_path, monkeypatch):
    state = copy_state(pipeline_replayed, tmp_path / "state")
    seal, models = Path(state.cfg.paths.seal), Path(state.cfg.paths.models_dir)
    before = {"seal": seal.read_bytes(), "models": digest(models), "rows": [rows(Path(state.cfg.paths.warehouse), sql) for sql in ISSUED],
              "analysis": seal.with_name("post_seal_analysis.json").read_bytes()}
    aside = tmp_path / "aside"
    aside.mkdir()
    move_aside(state, MOVED, aside)
    monkeypatch.setattr(launch, "fine_tune", lambda *a, **k: pytest.fail("R11 rebuilds with the sealed artifact: nothing is fitted"))
    result = cli("replay", "--config", config_of(state))
    assert result.code == 0, result.err
    assert seal.read_bytes() == before["seal"] and digest(models) == before["models"], "the seal and the sealed artifact are as they were"
    assert [rows(Path(state.cfg.paths.warehouse), sql) for sql in ISSUED] == before["rows"], "the same issue, the same sealed evaluation, the same registry row"
    assert seal.with_name("post_seal_analysis.json").read_bytes() == before["analysis"]
    runs = sorted((Path(state.cfg.paths.interim_dir) / "runs").glob("*.json"))
    states = [json.loads(p.read_text(encoding="utf-8")) for p in runs]
    fits = [s["tasks"]["fit_model"]["mode"] for s in states if s.get("tasks", {}).get("fit_model", {}).get("mode") in ("adopt", "launch")]
    assert fits == ["adopt"], "the launch run registered the sealed artifact again"
    closes = [s["tasks"]["evaluate"] for s in states if s.get("tasks", {}).get("evaluate", {}).get("state")]
    assert [(c["state"], c["seal"]) for c in closes] == [("repeat", "unchanged")]


@pytest.mark.parametrize("moved, says", [
    (("warehouse", "interim_dir"), "manifiesto de entregas"),
    (("warehouse", "interim_dir", "deliveries"), "ERP sustituto"),
])
def test_r11_followed_in_part_is_refused_before_anything_is_written_and_names_r11(pipeline_replayed, tmp_path, moved, says):
    """B3 of the evaluation: with the delivery manifest kept, ``replay`` exited 0 saying nothing was pending; with the ERP kept, the launch's delivery failed with 409."""
    state = copy_state(pipeline_replayed, tmp_path / "state")
    aside = tmp_path / "aside"
    aside.mkdir()
    move_aside(state, moved, aside)
    before = digest(state.root)
    for args in (("replay",), ("run",)):
        result = cli(*args, "--config", config_of(state))
        assert result.code == 2 and says in result.err and "R11" in result.err and "No se escribió nada" in result.err, (args, result.err)
    assert digest(state.root) == before
