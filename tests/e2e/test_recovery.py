"""A model-registry row that fails to be inserted after the seal was written (A5 of the evaluation, D62; N4 of the recheck; runbook R7): the sealed artifact and its seal stay, the run fails, a plain
``replay`` does not recover it (the delivery of the launch is already in the ERP: 409) and ``run --run-key <launch> --from fit_model`` does, registering the sealed artifact again from the seal.
Everything goes through the command line in quick mode, on a clean clone. This tests behaviour that already existed (no RED)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from invoiceflow import launch
from tests.e2e.cli_helpers import cli, config_of
from tests.fixtures.pipeline_state import clean_clone
from tests.integration.test_launch import candidates_without_training

LAUNCH_DAY = "L2026-09-30"


def runs_of(state) -> dict[str, dict]:
    folder = Path(state.cfg.paths.interim_dir) / "runs"
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))}


def registry_rows(state) -> int:
    conn = sqlite3.connect(f"{Path(state.cfg.paths.warehouse).resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT count(*) FROM model_registry").fetchone()[0]
    finally:
        conn.close()


def test_a_registry_row_that_fails_after_the_seal_is_recovered_with_run_from_fit_model_and_not_with_a_plain_replay(tmp_path, monkeypatch):
    state = clean_clone(tmp_path / "clone")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("INVOICEFLOW_HOME", str(state.root))
    monkeypatch.chdir(elsewhere)
    config = config_of(state)
    assert cli("make-fixture", "data/quick").code == 0
    candidates_without_training(monkeypatch)        # the fit is the session's quick launch (``quick_launch``) and ``tests/unit/test_train.py``'s: here only the order of the writes matters
    real_register = launch.register_model

    def full_disk(conn, row):
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(launch, "register_model", full_disk)
    failed = cli("replay", "--until", LAUNCH_DAY, "--config", config)
    seal, models = Path(state.cfg.paths.seal), Path(state.cfg.paths.models_dir)
    assert failed.code == 1 and "disk I/O error" in failed.err
    ((key, launch_run),) = runs_of(state).items()
    assert launch_run["status"] == "failed" and launch_run["tasks"].get("fit_model", {}).get("status") != "ok"
    sealed = seal.read_bytes()
    artifacts = sorted(p.name for p in models.iterdir())
    assert registry_rows(state) == 0 and len(artifacts) == 1 and artifacts[0].startswith("model-"), "the seal and the sealed artifact stay; the registry has no row"

    monkeypatch.setattr(launch, "register_model", real_register)
    plain = cli("replay", "--until", LAUNCH_DAY, "--config", config)
    assert plain.code == 1 and "409" in plain.err, "a plain replay starts the launch again and the ERP already has its delivery"
    assert registry_rows(state) == 0 and seal.read_bytes() == sealed and len(runs_of(state)) == 2, "it recovers nothing and leaves another failed run"

    recovered = cli("run", "--run-key", key, "--from", "fit_model", "--config", config)
    assert recovered.code == 0, recovered.err
    assert registry_rows(state) == 1 and seal.read_bytes() == sealed and sorted(p.name for p in models.iterdir()) == artifacts, "the sealed artifact is registered again, nothing is fitted or sealed"
    fit = runs_of(state)[key]["tasks"]["fit_model"]
    assert (fit["mode"], fit["seal"]) == ("adopt", "match") and runs_of(state)[key]["status"] != "failed"
