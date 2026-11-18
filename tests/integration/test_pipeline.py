"""The tasks of a run, their state, the ordered close and the refusals (U19; AC29, AC30; plan, section 22; D28, D56; risk item 4): what a run leaves, what refuses before it writes and what a failure
leaves behind. The runs are the session's (``tests/fixtures/pipeline_state.py``): the launch, three days and the close in quick mode, once; a test that changes anything works on a copy."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from invoiceflow import pipeline
from invoiceflow.export import verify_star_schema
from invoiceflow.lock import RunRefusedError
from invoiceflow.site_export import verify_site
from tests.fixtures.pipeline_state import copy_state, result_of, stage


def rows(state, sql: str, params=()):
    conn = sqlite3.connect(state.cfg.paths.warehouse)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def state_of(state, run_key: str) -> dict:
    return json.loads((Path(state.cfg.paths.interim_dir) / "runs" / f"{run_key}.json").read_text(encoding="utf-8"))


def tree(state) -> dict[str, tuple[int, bytes]]:
    return {p.relative_to(state.root).as_posix(): (p.stat().st_size, p.read_bytes()) for p in sorted(state.root.rglob("*")) if p.is_file()}


def test_every_run_of_the_replay_runs_the_thirteen_tasks_and_closes_ok_with_its_files(pipeline_replayed):
    state = pipeline_replayed
    assert len(state.results) == 5 and [d for d, in rows(state, "SELECT delivery_id FROM deliveries ORDER BY as_of")] == ["L2026-09-30", "D2026-10-01", "D2026-10-02", "D2026-10-05", "C2026-10-06"]
    for outcome in state.results[1:]:             # the first is the launch, made through the command line: its tasks are in its state file
        assert [step["task"] for step in outcome] == list(pipeline.TASK_IDS) and len(pipeline.TASK_IDS) == 13
    launch_run = sorted((Path(state.cfg.paths.interim_dir) / "runs").iterdir())[0]
    assert list(json.loads(launch_run.read_text(encoding="utf-8"))["tasks"]) == list(pipeline.TASK_IDS)
    runs = rows(state, "SELECT status, delivery_id, artifact_sha256 IS NOT NULL, invoices, peak_working_set_mb > 0, memory_counter FROM pipeline_runs ORDER BY as_of")
    assert [r[0] for r in runs] == ["ok"] * 5 and [r[2] for r in runs] == [1] * 5 and all(r[4] == 1 and r[5] for r in runs) and [r[3] for r in runs] == [0, 6, 6, 6, 0]
    assert verify_star_schema(Path(state.cfg.paths.outputs_dir)) == [] and verify_site(Path(state.cfg.paths.site_dir)) == [] and Path(state.cfg.paths.dashboard_html).is_file()
    reports = Path(state.cfg.paths.reports_dir)
    assert (reports / "evaluation_summary.json").is_file() and (reports / "evaluation_summary.md").is_file() and (reports / "validation" / "latest.json").is_file()
    assert pq.read_table(Path(state.cfg.paths.outputs_dir) / "dim_pipeline_run.parquet").column("status").to_pylist().count("ok") == 5
    manifest = json.loads((Path(state.cfg.paths.raw_dir) / "deliveries" / "manifest.json").read_text(encoding="utf-8"))["deliveries"]
    assert {k: v["status"] for k, v in manifest.items()} == {k: "loaded" for k in manifest} and len(manifest) == 5
    assert json.loads((Path(state.cfg.paths.interim_dir) / "runs" / sorted(p.name for p in (Path(state.cfg.paths.interim_dir) / "runs").iterdir())[-1]).read_text(encoding="utf-8"))["status"] == "ok"


def test_the_days_issue_what_the_launch_sealed_and_every_printed_qr_of_the_quick_mode_is_read(pipeline_replayed):
    """L101: at 50 dpi no reader sees the QR, so every quick invoice went to review for its QR; the quick mode draws at the resolution of the real one. The tiny model of the quick mode reads no
    invoice (two epochs on 24 pages), so every decision is review for its reading and nothing is posted: the posts are the tests of U13 and U15's, with a reading that works."""
    state = pipeline_replayed
    assert rows(state, "SELECT COUNT(*), SUM(decision = 'auto_post') FROM decisions")[0] == (18, 0)
    printed, read = rows(state, "SELECT SUM(qr_printed), SUM(qr_read_ok) FROM eval_invoice WHERE seal_state = 'sealed'")[0]
    assert printed > 0 and read == printed, "every QR the generator printed is read by a real reader at the quick resolution"
    assert rows(state, "SELECT COUNT(*) FROM qr_reads WHERE status = 'ok'")[0][0] == printed and rows(state, "SELECT COUNT(*) FROM qr_reads WHERE status = 'not_read'")[0][0] == 18 - printed,         "a page is `qr_not_read` only when the generator printed none (a planted qr_missing)"
    assert rows(state, "SELECT COUNT(*) FROM erp_posts")[0][0] == 0
    seal = json.loads(Path(state.cfg.paths.seal).read_text(encoding="utf-8"))
    assert set(seal) >= {"launch", "test"} and seal["test"]["metrics"]["erp"]["repeat_new"] == 0, "C09's repetition of the last day created no invoice"
    assert any(k.startswith("b2:") for k in seal["test"]["baselines"]), "B2 got the checkpoint's trees"


def test_the_close_evaluates_the_test_once_and_the_close_run_issues_nothing(pipeline_replayed):
    state = pipeline_replayed
    close = state.results[-1]
    assert result_of(close, "evaluate")["state"] == "first" and result_of(close, "evaluate")["repeat_new"] == 0
    for task in ("extract", "read_qr", "match", "post"):
        assert result_of(close, task) == {k: v for k, v in result_of(close, task).items() if k in ("skipped", "seconds") or k.startswith("peak") or k == "memory_counter"} and result_of(close, task)["skipped"] == "close"
    assert rows(state, "SELECT COUNT(*) FROM eval_criteria WHERE seal_state = 'sealed'")[0][0] == 10
    first_runs = state.results[1:-1]
    assert all(result_of(o, "evaluate")["skipped"] in ("launch", "daily") for o in first_runs)


def test_closed_run_refuses_every_task_and_keeps_its_records(pipeline_replayed, tmp_path):
    state = copy_state(pipeline_replayed, tmp_path / "state")
    key = sorted(p.stem for p in (Path(state.cfg.paths.interim_dir) / "runs").iterdir())[1]
    before = tree(state)
    for task_id in pipeline.TASK_IDS:
        with pytest.raises(RunRefusedError, match="cerrada"):
            pipeline.run_task(task_id, key, state.cfg)
    assert tree(state) == before, "a closed run is not reopened and nothing of it is rewritten"


def test_a_task_of_a_run_that_did_not_load_its_own_delivery_is_refused_and_writes_nothing(pipeline_launched, tmp_path):
    state = copy_state(pipeline_launched, tmp_path / "state")
    before = tree(state)
    for task_id in pipeline.TASK_IDS[pipeline.TASK_IDS.index("load"):]:
        with pytest.raises(RunRefusedError, match="no tiene una entrega"):
            pipeline.run_task(task_id, "brand-new", state.cfg)
    assert tree(state) == before and not (Path(state.cfg.paths.interim_dir) / "runs" / "brand-new.json").exists()


def test_load_is_refused_without_a_passed_validation_of_this_run(pipeline_launched, tmp_path):
    state = copy_state(pipeline_launched, tmp_path / "state")
    for task_id in ("fetch_source", "deliver"):
        pipeline.run_task(task_id, "next", state.cfg)
    before = tree(state)
    with pytest.raises(RunRefusedError, match="no validó su entrega"):
        pipeline.run_task("load", "next", state.cfg)
    assert tree(state) == before
    pipeline.run_task("validate", "next", state.cfg)
    report = Path(state.cfg.paths.reports_dir) / "validation" / "history" / "next.json"
    document = json.loads(report.read_text(encoding="utf-8"))
    for r in document["results"]:
        if r["rule_id"] == "V10":
            r["passed"] = False
    report.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RunRefusedError, match="rechazó su entrega"):
        pipeline.run_task("load", "next", state.cfg)


def test_a_delivery_that_another_run_loaded_is_refused_to_a_run_that_did_not(pipeline_launched, tmp_path):
    state = copy_state(pipeline_launched, tmp_path / "state")
    for task_id in ("fetch_source", "deliver"):
        pipeline.run_task(task_id, "one", state.cfg)
    pipeline.run_task("validate", "one", state.cfg)
    pipeline.run_task("load", "one", state.cfg)
    state_file = Path(state.cfg.paths.interim_dir) / "runs" / "two.json"
    state_file.write_text(json.dumps({"run_key": "two", "started_at": "2026-10-02T00:00:00+00:00", "status": "running", "tasks": {"fetch_source": json.loads(
        (Path(state.cfg.paths.interim_dir) / "runs" / "one.json").read_text(encoding="utf-8"))["tasks"]["fetch_source"], "deliver": json.loads(
        (Path(state.cfg.paths.interim_dir) / "runs" / "one.json").read_text(encoding="utf-8"))["tasks"]["deliver"]}}), encoding="utf-8")
    with pytest.raises(RunRefusedError, match="la cargó la corrida one"):
        pipeline.run_task("fit_model", "two", state.cfg)


def test_a_run_of_an_older_delivery_does_not_calculate_and_a_run_cannot_change_its_delivery(pipeline_before_close, tmp_path):
    state = copy_state(pipeline_before_close, tmp_path / "state")
    runs = sorted(Path(state.cfg.paths.interim_dir).joinpath("runs").iterdir())
    assert len(runs) == 4
    older = json.loads(runs[1].read_text(encoding="utf-8"))
    older["status"] = "running"                                           # as if that run had been left open
    runs[1].write_text(json.dumps(older), encoding="utf-8")
    before = tree(state)
    with pytest.raises(RunRefusedError, match="calcula la última entrega"):
        pipeline.run_task("match", runs[1].stem, state.cfg)
    assert tree(state) == before
    held = pipeline.run_all(state.cfg, only=["fetch_source", "deliver"], run_key="held")
    assert held[-1]["result"]["delivery_id"] == "C2026-10-06"
    with pytest.raises(RunRefusedError, match="no cambia de entrega"):
        _second_delivery(state)


def _second_delivery(state):
    """A run that holds the close asked to deliver again once the plan has moved on: the delivery it holds is no longer the pending one."""
    manifest = Path(state.cfg.paths.raw_dir) / "deliveries" / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["deliveries"]["C2026-10-06"]["status"] = "loaded"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    pipeline.run_task("deliver", "held", state.cfg)


def test_failure_handler_write_errors_do_not_mask_the_task_error(pipeline_launched, tmp_path, monkeypatch):
    state = copy_state(pipeline_launched, tmp_path / "state")
    for task_id in ("fetch_source", "deliver", "validate", "load"):
        pipeline.run_task(task_id, "boom", state.cfg)

    def explode(ctx):
        raise ValueError("the task's own error")

    def also_explode(*args, **kwargs):
        raise OSError("the handler's write error")

    monkeypatch.setattr(pipeline, "fit_model", explode)
    monkeypatch.setattr(pipeline, "TASKS", tuple(pipeline.TaskSpec(t.task_id, explode) if t.task_id == "fit_model" else t for t in pipeline.TASKS))
    monkeypatch.setattr(pipeline, "_write_state", also_explode)
    monkeypatch.setattr(pipeline, "_record", also_explode)
    monkeypatch.setattr(pipeline, "_refresh_exports", also_explode)
    with pytest.raises(ValueError, match="the task's own error"):
        pipeline.run_task("fit_model", "boom", state.cfg)


def test_a_failed_task_marks_the_run_failed_and_the_same_task_run_again_resumes_it(pipeline_launched, tmp_path, monkeypatch):
    state = copy_state(pipeline_launched, tmp_path / "state")
    for task_id in ("fetch_source", "deliver", "validate", "load"):
        pipeline.run_task(task_id, "resume", state.cfg)
    real = pipeline.TASKS

    def explode(ctx):
        raise ValueError("a failure after load")

    monkeypatch.setattr(pipeline, "TASKS", tuple(pipeline.TaskSpec(t.task_id, explode) if t.task_id == "fit_model" else t for t in real))
    with pytest.raises(ValueError, match="a failure after load"):
        pipeline.run_task("fit_model", "resume", state.cfg)
    assert state_of(state, "resume")["status"] == "failed" and rows(state, "SELECT status, message FROM pipeline_runs WHERE run_key = 'resume'")[0][0] == "failed"
    assert "a failure after load" in rows(state, "SELECT message FROM pipeline_runs WHERE run_key = 'resume'")[0][0] and str(state.root) not in rows(state, "SELECT message FROM pipeline_runs WHERE run_key = 'resume'")[0][0]
    monkeypatch.setattr(pipeline, "TASKS", real)
    outcome = pipeline.run_all(state.cfg, start="fit_model", run_key="resume")
    assert outcome[-1]["task"] == "report" and rows(state, "SELECT status FROM pipeline_runs WHERE run_key = 'resume'")[0][0] == "ok" and state_of(state, "resume")["status"] == "ok"


def test_a_run_with_no_delivery_left_ends_as_no_delivery_and_the_lock_is_free(pipeline_replayed, tmp_path):
    state = copy_state(pipeline_replayed, tmp_path / "state")
    outcome = pipeline.run_all(state.cfg)
    assert [s["task"] for s in outcome] == ["fetch_source", "deliver"] and outcome[-1]["result"]["delivered"] is False
    assert rows(state, "SELECT status, delivery_id FROM pipeline_runs ORDER BY started_at DESC LIMIT 1")[0] == ("no_delivery", None) and not (Path(state.cfg.paths.interim_dir) / "pipeline.lock").exists()


def test_the_test_is_not_evaluated_under_another_configuration_before_its_seal_and_nothing_is_written(pipeline_before_close, tmp_path):
    state = copy_state(pipeline_before_close, tmp_path / "state")
    config = state.root / "config" / "test.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[evaluation]\nresamples = 101\n", encoding="utf-8")
    from invoiceflow.config import load_config

    other = load_config(config)
    before = tree(state)
    with pytest.raises(RunRefusedError, match="no es la sellada"):
        pipeline.run_task("fetch_source", None, other)
    assert tree(state) == before


def test_a_replay_runs_the_pending_deliveries_up_to_the_one_asked_for(pipeline_launched, tmp_path):
    state = copy_state(pipeline_launched, tmp_path / "state")
    runs = pipeline.replay(state.cfg, until="D2026-10-02")
    assert len(runs) == 2 and rows(state, "SELECT COUNT(*) FROM deliveries")[0][0] == 3
    with pytest.raises(pipeline.UsageError, match="anterior a la siguiente entrega pendiente"):
        pipeline.replay(state.cfg, until="D2026-10-01")
    with pytest.raises(pipeline.UsageError, match="no es una entrega del plan"):
        pipeline.replay(state.cfg, until="D2030-01-01")
    assert stage(state, 0) is state
