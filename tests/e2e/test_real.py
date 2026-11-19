"""What the complete real replay left (U22; AC03, AC20, AC22, AC37; RNF02, RNF03, RNF12; plan sections 13, 14, 16, 19, 21 and 27; decisions D21, D31, D39, D51, D53, D54, D55).

Marks ``real_data``: not in the default suite. The tests read outputs and start no run: the first and only evaluation of the test is the replay itself (``python -m invoiceflow replay``, with the GPU,
in the real ``data/``), made once. Without its outputs these tests fail (they do not skip) and say which file is missing.

The criteria are never read here against their thresholds: whether C03 passed is the summary's to say. What is checked is that the seal, the warehouse, the summary and the site tell the same ten
verdicts, that the counts are the plan's, that every run closed in order, and the limits of RNF02 and RNF03 with the name of the counter.
"""

from __future__ import annotations

import getpass
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from invoiceflow import registry
from invoiceflow.dashboard.semantic_model import MODEL
from invoiceflow.evaluation import criteria_from_seal
from invoiceflow.export import verify_star_schema
from invoiceflow.site_export import read_site, verify_site
from invoiceflow.synth.cuit import non_synthetic_cuits
from invoiceflow.sysinfo import POSIX_COUNTER, WINDOWS_COUNTER

pytestmark = pytest.mark.real_data

DAILY_RUNS, INVOICES_PER_DAY, REPLAY_RUNS = 20, 30, 22          # one launch, the twenty working days of October 2026 and the close (D22)
KNOWN_PER_DAY, NEW_PER_DAY = 15, 15
MAX_LAUNCH_S, MAX_DAILY_S, MAX_CLOSE_S = 3 * 3600, 120, 10 * 60          # RNF02: the launch with the fine-tune, a daily run of 30 invoices, the close with CORD's test
MAX_PEAK_WORKING_SET_MB, MAX_PEAK_GPU_MB = 16 * 1024, 20 * 1024         # RNF03
MAX_PARQUET_BYTES = 25 * 1024 * 1024                                     # AC37
LAUNCH_AS_OF, CLOSE_AS_OF = "2026-10-01 00:00:00", "2026-10-29 00:00:00"
USER_NAMES = {name.lower().encode() for name in (getpass.getuser(), Path.home().name) if name}   # this machine's user, read here and never written in a test
HASH = re.compile(r"[0-9a-f]{32,}")
LOCAL_PATHS = (b":\\Users", b"C:\\", b"C:/", b"/Users/", b"\\Users\\", b"AppData")


def require(path: Path) -> Path:
    assert path.is_file(), f"falta {path.name}: la repetición real completa no corrió (python -m invoiceflow replay)"
    return path


def seconds(started: str, finished: str) -> float:
    return (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()


@pytest.fixture(scope="module")
def outputs(cfg):
    paths = cfg.paths
    seal_path, summary_path = require(Path(paths.seal)), require(Path(paths.reports_dir) / "evaluation_summary.json")
    warehouse = require(Path(paths.warehouse))
    read = {"seal": json.loads(seal_path.read_text(encoding="utf-8")), "summary": json.loads(summary_path.read_text(encoding="utf-8")), "site": read_site(Path(paths.site_dir)),
            "seal_path": seal_path, "powerbi": Path(paths.outputs_dir), "interim": Path(paths.interim_dir)}
    conn = sqlite3.connect(f"{warehouse.as_uri()}?mode=ro", uri=True)
    try:
        yield {"conn": conn, **read}
    finally:
        conn.close()


def _runs(conn):
    return conn.execute("SELECT p.run_key, p.started_at, p.finished_at, p.status, p.peak_working_set_mb, p.memory_counter, d.kind, p.delivery_id, p.peak_gpu_mb "
                        "FROM pipeline_runs p LEFT JOIN deliveries d ON d.delivery_id = p.delivery_id ORDER BY p.started_at").fetchall()


def _state(outputs, run_key: str) -> dict:
    return json.loads(require(outputs["interim"] / "runs" / f"{run_key}.json").read_text(encoding="utf-8"))


def test_real_counts_seal_and_summary_consistency(cfg, outputs):
    conn, seal, summary, site = outputs["conn"], outputs["seal"], outputs["summary"], outputs["site"]

    # AC03 and the plan's counts: one launch, the twenty days, the close; 30 invoices a day, 15 of known suppliers and 15 of new ones; every delivery loaded
    deliveries = conn.execute("SELECT delivery_id, kind, files, status FROM deliveries ORDER BY CASE kind WHEN 'launch' THEN 0 WHEN 'daily' THEN 1 ELSE 2 END, delivery_id").fetchall()
    assert [d[1] for d in deliveries] == ["launch"] + ["daily"] * DAILY_RUNS + ["close"] and {d[3] for d in deliveries} == {"loaded"}
    assert [d[2] for d in deliveries] == [0] + [INVOICES_PER_DAY] * DAILY_RUNS + [0]
    assert conn.execute("SELECT COUNT(*), COALESCE(SUM(status = 'accepted'), 0) + COALESCE(SUM(status = 'quarantined'), 0) FROM invoices").fetchone() == (DAILY_RUNS * INVOICES_PER_DAY,) * 2
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == DAILY_RUNS * INVOICES_PER_DAY
    assert conn.execute("SELECT COUNT(*), SUM(invoices) FROM daily_ops").fetchone() == (DAILY_RUNS, DAILY_RUNS * INVOICES_PER_DAY)
    assert conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0] == 1
    evaluated = dict(conn.execute("SELECT cohort, COUNT(*) FROM eval_invoice WHERE seal_state = 'sealed' GROUP BY cohort"))
    assert evaluated == {"test_known": KNOWN_PER_DAY * DAILY_RUNS, "test_new": NEW_PER_DAY * DAILY_RUNS}
    assert conn.execute("SELECT COUNT(*) FROM eval_invoice WHERE seal_state != 'sealed'").fetchone()[0] == 0, "the test is evaluated once: no post-seal rows"
    assert conn.execute("SELECT COUNT(*) FROM erp_posts p LEFT JOIN decisions d USING (invoice_key) WHERE d.decision IS NOT 'auto_post'").fetchone()[0] == 0, "nothing of `review` reaches the ERP"

    # The seal: both parts, one configuration, and the criteria told the same in the seal, the warehouse, the summary and the site (D21, D53)
    assert set(seal) == {"version", "launch", "test"} and seal["test"] is not None
    launch, test = seal["launch"], seal["test"]
    assert registry.read_seal(outputs["seal_path"]) == seal, "the seal is read back with the shape the code checks"
    runs = _runs(conn)
    observed = _state(outputs, runs[0][0])["tasks"]["fetch_source"]["source_sha256"]
    fingerprint = registry.config_fingerprint(registry.sealed_view(cfg, observed))
    assert launch["config_sha256"] == test["config_sha256"] == fingerprint == summary["config_sha256"] == site.summary["seal"]["launch"]["config_sha256"]
    assert registry.seal_state(outputs["seal_path"], fingerprint) == "match"
    assert launch["as_of"] == LAUNCH_AS_OF and test["as_of"] == CLOSE_AS_OF
    sealed = criteria_from_seal(seal)
    assert [c.criterion_id for c in sealed] == [f"C{n:02d}" for n in range(1, 11)] == [c["id"] for c in test["criteria"]]
    assert summary["source"] == "warehouse" and site.summary["evaluation_status"] == "sealed" and site.summary["sealed_from"] == "warehouse"
    assert [c["criterion_id"] for c in summary["criteria"]] == [c["criterion_id"] for c in site.summary["criteria"]] == [c.criterion_id for c in sealed]
    own = {r[0]: r[1:] for r in conn.execute("SELECT criterion_id, passed, verdict_es FROM eval_criteria WHERE seal_state = 'sealed'")}
    assert sorted(own) == [c.criterion_id for c in sealed]
    for criterion, in_summary, on_site, in_seal in zip(sealed, summary["criteria"], site.summary["criteria"], test["criteria"], strict=True):
        assert in_summary["verdict_es"] == on_site["verdict_es"] == in_seal["verdict_es"] == own[criterion.criterion_id][1] == criterion.verdict_es, criterion.criterion_id
        assert in_summary["passed"] == on_site["passed"] == in_seal["passed"] == criterion.passed, criterion.criterion_id
        assert own[criterion.criterion_id][0] == (None if criterion.passed is None else int(criterion.passed)), criterion.criterion_id
    assert site.summary["seal"]["test"] == {"run_key": test["run_key"], "as_of": test["as_of"]}
    assert launch["artifact_sha256"] == site.summary["seal"]["launch"]["artifact_sha256"] == conn.execute("SELECT artifact_sha256 FROM model_registry").fetchone()[0]

    # The runs: the launch, the twenty daily deliveries and the close, each ok and in order, then the runs without a delivery
    assert [r[6] for r in runs[:REPLAY_RUNS]] == ["launch"] + ["daily"] * DAILY_RUNS + ["close"] and {r[3] for r in runs[:REPLAY_RUNS]} == {"ok"}
    assert len(runs) > REPLAY_RUNS and {r[3] for r in runs[REPLAY_RUNS:]} == {"no_delivery"}, "the run without a delivery follows the 22 runs of the replay"
    assert (launch["run_key"], test["run_key"]) == (runs[0][0], runs[REPLAY_RUNS - 1][0])
    assert summary["config_sha256"] == fingerprint and conn.execute("SELECT COUNT(*) FROM deliveries WHERE status != 'loaded'").fetchone()[0] == 0

    # AC37 (RNF02, RNF03): the limits, from the records of the runs; the counter of every figure of memory is named
    assert not verify_site(Path(cfg.paths.site_dir)) and not verify_star_schema(outputs["powerbi"])
    launch_state = _state(outputs, runs[0][0])
    worked = sum(task["seconds"] for task in launch_state["tasks"].values())          # the record of the launch run spans a failed attempt and its repair (run 1 of U22): what it took is what its tasks took
    assert worked <= MAX_LAUNCH_S, "el lanzamiento con el ajuste pasa de 3 h (RNF02)"
    counter = WINDOWS_COUNTER if sys.platform == "win32" else POSIX_COUNTER
    assert {r[5] for r in runs[:REPLAY_RUNS]} == {counter} and max(r[4] for r in runs[:REPLAY_RUNS]) <= MAX_PEAK_WORKING_SET_MB, f"el pico de {counter} pasa de 16 GB (RNF03)"
    gpu = [r[8] for r in runs[:REPLAY_RUNS] if r[8] is not None]
    assert gpu and 0 < max(gpu) <= MAX_PEAK_GPU_MB, "el pico de torch.cuda.max_memory_allocated falta o pasa de 20 GB (RNF03)"
    fit = launch_state["tasks"]["fit_model"]
    assert fit["seconds"] <= MAX_LAUNCH_S and fit["memory_counter"] == counter and fit["peak_gpu_mb"] is not None
    parquets = sorted(outputs["powerbi"].glob("*.parquet"))
    assert {p.stem for p in parquets} == {t.name for t in MODEL.tables} and max(p.stat().st_size for p in parquets) <= MAX_PARQUET_BYTES

    # RNF12: nothing published carries a path of this machine, the name of its user or a CUIT outside the synthetic range
    published = [*Path(cfg.paths.site_dir).glob("*.json"), *parquets, Path(cfg.paths.reports_dir) / "evaluation_summary.json", Path(cfg.paths.reports_dir) / "evaluation_summary.md",
                 outputs["seal_path"], Path(cfg.paths.dashboard_html), outputs["powerbi"] / "manifest.json"]
    for path in published:
        data = require(path).read_bytes()
        assert not any(needle in data for needle in LOCAL_PATHS) and not any(name in data.lower() for name in USER_NAMES), path.name
        text = data.decode("utf-8", "replace") if path.suffix != ".parquet" else " ".join([v.decode("utf-8", "replace") for v in (pq.read_schema(path).metadata or {}).values()] +
                                                                                            [str(v) for column in pq.read_table(path).columns for v in column.to_pylist() if isinstance(v, str)])
        assert non_synthetic_cuits(HASH.sub(" ", text)) == [], path.name


def test_every_daily_run_takes_at_most_the_required_seconds(outputs):
    """RNF02: a daily run of 30 invoices (its thirteen tasks, from the run record) takes at most 120 s. Kept apart so that a miss of this one limit does not hide what the other test checks."""
    runs = _runs(outputs["conn"])
    assert [r[6] for r in runs[1:1 + DAILY_RUNS]] == ["daily"] * DAILY_RUNS
    slow = [(r[7], round(seconds(r[1], r[2]))) for r in runs[1:1 + DAILY_RUNS] if seconds(r[1], r[2]) > MAX_DAILY_S]
    assert slow == [], "una corrida diaria pasa de 120 s (RNF02)"


def test_the_close_with_the_test_of_cord_takes_at_most_the_required_seconds(outputs):
    """RNF02: the close, which opens CORD's test split and evaluates the synthetic test, takes at most 10 minutes. Kept apart for the same reason as the daily limit."""
    runs = _runs(outputs["conn"])
    assert runs[REPLAY_RUNS - 1][6] == "close"
    assert seconds(runs[REPLAY_RUNS - 1][1], runs[REPLAY_RUNS - 1][2]) <= MAX_CLOSE_S, "el cierre con la prueba de CORD pasa de 10 min (RNF02)"
