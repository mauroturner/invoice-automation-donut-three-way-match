"""The entry API (U20, AC34; RF24; RNF11; plan, section 18; decisions D26, D27): every request needs the token, no error carries a path or a trace, a run takes the lock of the pipeline and a second
one is a 409, and the run it starts is the run the command line starts. Over the quick states of the session, copied; nothing here opens a socket (``TestClient`` calls the application)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import fastapi.testclient as testclient
import pytest

from invoiceflow import pipeline
from invoiceflow.api import ApiStartError, create_app
from invoiceflow.lock import sole_writer
from invoiceflow.sources import SourceError
from invoiceflow.validate import ValidationError
from invoiceflow.warehouse import apply_schema, connect
from tests.fixtures.pipeline_state import copy_state

TOKEN = "t0ken-of-the-tests-0123456789abcdef"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
PROJECT = Path(__file__).resolve().parents[2]
ENV = {**os.environ, "PYTHONPATH": str(PROJECT / "src")}


def client_of(cfg, token: str | None = TOKEN) -> testclient.TestClient:
    env = {} if token is None else {cfg.service.token_env: token}
    return testclient.TestClient(create_app(cfg, env=env), raise_server_exceptions=False, base_url="http://api")


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


def only_a_code(response) -> str:
    """An error is ``{"code": <fixed word>}`` and nothing else: no path, no trace, nothing of the request."""
    body = response.json()
    assert set(body) == {"code"} and re.fullmatch(r"[a-z_]+", body["code"]), body
    return body["code"]


@pytest.fixture()
def replayed(pipeline_replayed, tmp_path):
    return copy_state(pipeline_replayed, tmp_path / "replayed")


@pytest.fixture()
def launched(pipeline_launched, tmp_path):
    return copy_state(pipeline_launched, tmp_path / "launched")


# --- the token (D27) -----------------------------------------------------------------------------------------------------------------


REQUESTS = [("GET", "/health"), ("GET", "/runs/20261001T000000Z"), ("GET", "/runs/20261001T000000Z/review"), ("POST", "/runs"), ("GET", "/nothing"), ("DELETE", "/runs"), ("OPTIONS", "/runs"), ("GET", "/docs"),
            ("GET", "/openapi.json")]
WRONG = [None, "", "Bearer", "Bearer ", "Bearer wrong", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer {TOKEN}", f"Bearer  {TOKEN}", f"token {TOKEN}", f"Basic {TOKEN}", TOKEN]


def test_every_request_without_the_token_is_401_and_no_error_carries_a_path(replayed, monkeypatch):
    """AC34, D27: a request without ``Authorization: Bearer <token>``, or with another one, is 401 before the route is looked at (an unknown route and a wrong method included) and writes nothing; every
    error the API gives with the right token is a fixed code with no path of this machine and no trace, whatever went wrong inside."""
    client = client_of(replayed.cfg)
    before = tree(replayed.root)
    for method, path in REQUESTS:
        for header in WRONG:
            headers = {} if header is None else {"Authorization": header}
            response = client.request(method, path, headers=headers)
            assert response.status_code == 401 and only_a_code(response) == "unauthorized" and response.headers["www-authenticate"] == "Bearer", (method, path, header)
    assert tree(replayed.root) == before, "nothing is written by a request that is refused"
    errors = {
        ("GET", "/runs/20261001T000000Z"): (404, "run_not_found"), ("GET", "/runs/20261001T000000Z/review"): (404, "run_not_found"), ("GET", "/runs/" + "x" * 500): (404, "run_not_found"),
        ("GET", "/runs/..%2F..%2Fetc"): (404, "not_found"), ("GET", "/runs/a%20b/review"): (404, "run_not_found"), ("GET", "/nothing"): (404, "not_found"), ("DELETE", "/runs"): (405, "method_not_allowed"),
        ("GET", "/runs"): (405, "method_not_allowed"), ("GET", "/docs"): (404, "not_found"), ("GET", "/openapi.json"): (404, "not_found"),
    }
    for (method, path), (status, code) in errors.items():
        response = client.request(method, path, headers=BEARER)
        assert (response.status_code, only_a_code(response)) == (status, code), (method, path, response.text)
    hostile = f"disk full at {replayed.root}/data/quick/x.sqlite\nTraceback (most recent call last):\n  File \"{replayed.root}/src/invoiceflow/pipeline.py\", line 1"

    def explode(*args, **kwargs):
        raise RuntimeError(hostile)

    def reject(*args, **kwargs):
        raise ValidationError(SimpleNamespace(delivery_id="D2026-10-01", blocking_failed=("V10",)))

    def bad_source(*args, **kwargs):
        raise SourceError("V01", f"el archivo {replayed.root}/data/raw/x.parquet no tiene el tamaño aceptado")

    for raiser, status, code in ((explode, 500, "run_failed"), (reject, 422, "delivery_rejected"), (bad_source, 422, "delivery_rejected")):
        monkeypatch.setattr(pipeline, "run_all", raiser)
        response = client.post("/runs", headers=BEARER)
        assert (response.status_code, only_a_code(response)) == (status, code)
        assert str(replayed.root) not in response.text and "Traceback" not in response.text and "pipeline.py" not in response.text and "D2026" not in response.text


def test_the_token_is_compared_in_constant_time_and_a_wrong_one_never_reaches_a_route(replayed, monkeypatch):
    from invoiceflow import api

    seen = []
    real = api.hmac.compare_digest
    monkeypatch.setattr(api.hmac, "compare_digest", lambda a, b: (seen.append((a, b)), real(a, b))[1])
    called = []
    monkeypatch.setattr(pipeline, "run_all", lambda *a, **k: called.append(1))
    client = client_of(replayed.cfg)
    assert client.post("/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401 and called == []
    assert seen and all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in seen), "bytes, so that a non-ASCII header cannot raise"
    assert client.get("/health", headers={"Authorization": "Bearer ñandú".encode("latin-1")}).status_code == 401


def test_the_application_does_not_start_without_a_token_and_reads_the_variable_the_configuration_names(replayed):
    import dataclasses

    for env in ({}, {"INVOICEFLOW_API_TOKEN": ""}, {"INVOICEFLOW_API_TOKEN": "   "}):
        with pytest.raises(ApiStartError, match="INVOICEFLOW_API_TOKEN"):
            create_app(replayed.cfg, env=env)
    renamed = dataclasses.replace(replayed.cfg, service=dataclasses.replace(replayed.cfg.service, token_env="OTHER_API_TOKEN"))
    with pytest.raises(ApiStartError, match="OTHER_API_TOKEN"):
        create_app(renamed, env={"INVOICEFLOW_API_TOKEN": TOKEN})
    app = create_app(renamed, env={"OTHER_API_TOKEN": TOKEN})
    assert testclient.TestClient(app, base_url="http://api").get("/health", headers=BEARER).status_code == 200


def test_the_application_of_the_environment_reads_the_configuration_and_the_token_of_the_process(replayed, monkeypatch):
    from invoiceflow.api import create_app_from_environment

    monkeypatch.setenv("INVOICEFLOW_HOME", str(replayed.root))
    monkeypatch.setenv("INVOICEFLOW_CONFIG", str(replayed.root / "config" / "test.toml"))
    monkeypatch.setenv("INVOICEFLOW_API_TOKEN", TOKEN)
    client = testclient.TestClient(create_app_from_environment(), base_url="http://api")
    assert client.get("/health", headers=BEARER).json() == {"status": "ok", "sealed": True}
    monkeypatch.delenv("INVOICEFLOW_API_TOKEN")
    with pytest.raises(ApiStartError):
        create_app_from_environment()


# --- the health of the service ----------------------------------------------------------------------------------------------------------


def test_health_says_whether_the_model_is_sealed_and_changes_nothing(launched, tmp_path):
    from tests.fixtures.pipeline_state import clean_clone

    before = tree(launched.root)
    assert client_of(launched.cfg).get("/health", headers=BEARER).json() == {"status": "ok", "sealed": True}
    assert tree(launched.root) == before
    fresh = clean_clone(tmp_path / "fresh")
    assert client_of(fresh.cfg).get("/health", headers=BEARER).json() == {"status": "ok", "sealed": False}
    assert not (fresh.root / "data").exists(), "asking about a project with no data creates none"


# --- one run at a time (AC34) -------------------------------------------------------------------------------------------------------------


def test_a_second_run_while_one_holds_the_lock_is_409(replayed):
    """The API takes the lock of the pipeline: while a task or a run of this process holds it, ``POST /runs`` is 409 and writes nothing, and the reads still answer."""
    client = client_of(replayed.cfg)
    before = tree(replayed.root)
    with sole_writer(replayed.cfg.paths.interim_dir, "run", "held"):
        refused = client.post("/runs", headers=BEARER)
        assert refused.status_code == 409 and only_a_code(refused) == "run_locked"
        assert client.get("/health", headers=BEARER).status_code == 200 and client.get("/runs/held", headers=BEARER).status_code == 404
        held = {p for p in tree(replayed.root) if p not in before}
        assert held == {"data/quick/interim/pipeline.lock"}, "only the lock of the one that holds it"
    assert {p: c for p, c in tree(replayed.root).items() if p in before} == before
    assert client.post("/runs", headers=BEARER).status_code == 200, "when the lock is free the run is taken"


HOLDER = """
import sys, time
from invoiceflow.lock import sole_writer
from invoiceflow.sources import SourceError
with sole_writer(sys.argv[1], "run", "another-process"):
    print("held", flush=True)
    sys.stdin.readline()
"""


def test_a_run_of_another_process_holds_the_same_lock(replayed):
    """A run of the command line, or of the DAG in its container, is another process: the API is refused while it runs and takes the lock when it ends."""
    before = tree(replayed.root)
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(replayed.cfg.paths.interim_dir)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=ENV)
    try:
        assert holder.stdout.readline().strip() == "held"
        client = client_of(replayed.cfg)
        refused = client.post("/runs", headers=BEARER)
        assert refused.status_code == 409 and only_a_code(refused) == "run_locked"
        assert {p for p in tree(replayed.root) if p not in before} == {"data/quick/interim/pipeline.lock"}, "the refused request wrote no run: only the lock of the one that holds it is new"
    finally:
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(timeout=30)
    assert client.post("/runs", headers=BEARER).status_code == 200


def test_another_refusal_of_the_run_is_409_with_its_own_code_and_the_pipeline_error_is_not_echoed(replayed, monkeypatch):
    from invoiceflow.lock import RunRefusedError

    def refuse(*args, **kwargs):
        raise RunRefusedError(f"la corrida {replayed.root} está cerrada")

    monkeypatch.setattr(pipeline, "run_all", refuse)
    response = client_of(replayed.cfg).post("/runs", headers=BEARER)
    assert response.status_code == 409 and only_a_code(response) == "run_refused" and str(replayed.root) not in response.text


# --- the run it starts is the run of the command line --------------------------------------------------------------------------------------


def test_with_nothing_left_to_deliver_a_run_says_no_delivery_and_its_row_can_be_read(replayed):
    client = client_of(replayed.cfg)
    posted = client.post("/runs", headers=BEARER)
    body = posted.json()
    assert posted.status_code == 200 and set(body) == {"run_key", "status", "delivery_id", "invoices", "auto_post", "review", "by_reason"}
    assert body["status"] == "no_delivery" and body["delivery_id"] is None and (body["invoices"], body["auto_post"], body["review"], body["by_reason"]) == (0, 0, 0, {})
    assert re.fullmatch(r"\d{8}T\d{12}Z", body["run_key"]) and (Path(replayed.cfg.paths.interim_dir) / "runs" / f"{body['run_key']}.json").is_file(), "the key is the run's"
    row = client.get(f"/runs/{body['run_key']}", headers=BEARER)
    assert row.status_code == 200 and row.json()["run_key"] == body["run_key"] and row.json()["status"] == "no_delivery" and row.json()["delivery_id"] is None
    assert client.get(f"/runs/{body['run_key']}/review", headers=BEARER).json() == {"run_key": body["run_key"], "invoices": []}


def decisions_of(state, delivery_id: str) -> list[tuple]:
    conn = sqlite3.connect(Path(state.cfg.paths.warehouse))
    try:
        return conn.execute("SELECT d.invoice_key, d.decision, d.first_reason, d.reasons FROM decisions d JOIN invoices i ON i.invoice_key = d.invoice_key WHERE i.delivery_id = ? ORDER BY d.invoice_key",
                            (delivery_id,)).fetchall()
    finally:
        conn.close()


def test_a_run_of_the_api_is_the_run_of_the_pipeline_and_its_numbers_are_the_warehouses(pipeline_replayed, pipeline_launched, tmp_path):
    """The session ran the first daily delivery with ``pipeline.run_all`` (what the command line and the DAG's tasks run); the API, over the same launch, runs it and decides the same."""
    by_api = copy_state(pipeline_launched, tmp_path / "api")
    client = client_of(by_api.cfg)
    posted = client.post("/runs", headers=BEARER)
    body = posted.json()
    assert posted.status_code == 200 and body["status"] == "ok" and body["delivery_id"] == "D2026-10-01"
    rows = decisions_of(by_api, "D2026-10-01")
    assert rows == decisions_of(pipeline_replayed, "D2026-10-01") != [], "the same tasks in the same order give the same decisions"
    review = [r for r in rows if r[1] == "review"]
    assert body["invoices"] == len(rows) and body["auto_post"] == len(rows) - len(review) and body["review"] == len(review)
    assert body["by_reason"] == {reason: sum(1 for r in review if r[2] == reason) for reason in sorted({r[2] for r in review})} and sum(body["by_reason"].values()) == body["review"]
    row = client.get(f"/runs/{body['run_key']}", headers=BEARER).json()
    assert (row["status"], row["delivery_id"], row["invoices"]) == ("ok", "D2026-10-01", body["invoices"]) and {"started_at", "finished_at", "as_of", "message"} <= set(row)
    queue = client.get(f"/runs/{body['run_key']}/review", headers=BEARER).json()
    assert queue["run_key"] == body["run_key"] and sorted(i["invoice_key"] for i in queue["invoices"]) == sorted(r[0] for r in review)
    assert all(i["reasons"] and isinstance(i["reasons"], list) for i in queue["invoices"]) and "file" not in json.dumps(queue).lower().replace("file_rejected", "")


# --- the review queue, by hand --------------------------------------------------------------------------------------------------------------


@pytest.fixture()
def by_hand(tmp_path):
    from tests.fixtures.pipeline_state import clean_clone

    state = clean_clone(tmp_path / "by-hand")
    hand_warehouse(state)
    return state


def hand_warehouse(state) -> None:
    """A run with three issued invoices: one posted, one for review for two reasons (with two lines read and a header) and one whose post failed; and another run with one review."""
    conn = connect(state.cfg.paths.warehouse)
    try:
        apply_schema(conn)
        conn.execute("PRAGMA foreign_keys=OFF")
        sha = "a" * 64
        for run in ("r1", "r2"):
            conn.execute("INSERT INTO pipeline_runs (run_key, started_at, finished_at, status, delivery_id, as_of, invoices) VALUES (?, '2026-10-01 07:00:00', '2026-10-01 07:01:00', 'ok', NULL, NULL, 3)", (run,))
        conn.execute("INSERT INTO deliveries (delivery_id, kind, day, as_of, files, sha256, status, run_key) VALUES ('D2026-10-01', 'daily', '2026-10-01', '2026-10-01 07:00:00', 4, ?, 'loaded', 'r1')", (sha,))
        for key, run in (("inv-a", "r1"), ("inv-b", "r1"), ("inv-c", "r1"), ("inv-d", "r2")):
            conn.execute("INSERT INTO invoices (invoice_key, delivery_id, file_name, file_sha256, file_format, bytes, pixels, status, rule_id, received_at) VALUES (?, 'D2026-10-01', ?, ?, 'png', 10, 10, 'accepted', NULL, '2026-10-01 07:00:00')",
                         (key, f"/secret/place/{key}.png", sha))
        decisions = (("inv-a", "auto_post", None, 0, "r1"), ("inv-b", "review", "qr_not_read", 2, "r1"), ("inv-c", "auto_post", None, 0, "r1"), ("inv-d", "review", "qr_not_read", 1, "r2"))
        for key, decision, first, reasons, run in decisions:
            conn.execute("INSERT INTO decisions (invoice_key, decision, first_reason, reasons, po_number, supplier_cuit, total, currency, run_key, seconds_total) VALUES (?, ?, ?, ?, NULL, NULL, '100.00', 'ARS', ?, 1.0)",
                         (key, decision, first, reasons, run))
        for key, reason, rank in (("inv-b", "qr_not_read", 1), ("inv-b", "no_po", 8), ("inv-d", "qr_not_read", 1)):
            conn.execute("INSERT INTO decision_reasons (invoice_key, reason, rank, detail_json) VALUES (?, ?, ?, '{}')", (key, reason, rank))
        conn.execute("INSERT INTO erp_posts (invoice_key, erp_name, status, readback_equal, seconds, run_key) VALUES ('inv-c', NULL, 'post_failed', NULL, 0.5, 'r1')")
        for field, ordinal, raw, norm in (("header.invoice_number", 0, "0001-0000123", "0001-00000123"), ("line.qty", 0, "3", "3"), ("line.qty", 1, "5,0", "5.0"), ("line.desc", 1, "Tornillo", "Tornillo")):
            conn.execute("INSERT INTO extracted_fields (invoice_key, field, ordinal, value_raw, value_norm, model_version, seconds) VALUES ('inv-b', ?, ?, ?, ?, 'm1', 0.1)", (field, ordinal, raw, norm))
    finally:
        conn.close()


def test_the_review_queue_of_a_run_has_keys_reasons_and_fields_read_and_never_the_file(by_hand):
    client = client_of(by_hand.cfg)
    queue = client.get("/runs/r1/review", headers=BEARER)
    assert queue.status_code == 200
    by_key = {i["invoice_key"]: i for i in queue.json()["invoices"]}
    assert sorted(by_key) == ["inv-b", "inv-c"], "the decisions for review and the posts that failed, of this run only"
    assert by_key["inv-b"]["reasons"] == ["qr_not_read", "no_po"], "in the order of the plan's list (rank), not the alphabetical one"
    assert by_key["inv-c"]["reasons"] == ["post_failed"] and by_key["inv-c"]["fields"] == []
    assert by_key["inv-b"]["fields"] == [{"field": "header.invoice_number", "ordinal": 0, "value": "0001-00000123"}, {"field": "line.desc", "ordinal": 1, "value": "Tornillo"},
                                         {"field": "line.qty", "ordinal": 0, "value": "3"}, {"field": "line.qty", "ordinal": 1, "value": "5.0"}]
    assert "/secret/place" not in queue.text and "png" not in queue.text and "sha" not in queue.text.lower()
    assert [i["invoice_key"] for i in client.get("/runs/r2/review", headers=BEARER).json()["invoices"]] == ["inv-d"]
    row = client.get("/runs/r1", headers=BEARER).json()
    assert row["status"] == "ok" and row["invoices"] == 3 and row["finished_at"] == "2026-10-01 07:01:00"


def test_reading_runs_never_writes_and_a_project_without_a_warehouse_has_no_runs(by_hand, tmp_path):
    from tests.fixtures.pipeline_state import clean_clone

    before = tree(by_hand.root)
    client = client_of(by_hand.cfg)
    for path in ("/health", "/runs/r1", "/runs/r1/review", "/runs/nope", "/runs/nope/review"):
        client.get(path, headers=BEARER)
    assert tree(by_hand.root) == before
    empty = clean_clone(tmp_path / "empty")
    assert client_of(empty.cfg).get("/runs/r1", headers=BEARER).status_code == 404 and not (empty.root / "data").exists()


def test_a_warehouse_that_cannot_be_read_is_503_and_any_other_failure_of_a_read_is_500_without_what_went_wrong(by_hand, monkeypatch):
    from invoiceflow import api

    client = client_of(by_hand.cfg)
    secret = f"{by_hand.root}/data/quick/invoiceflow.sqlite"
    monkeypatch.setattr(api, "_has_table", lambda conn, name: (_ for _ in ()).throw(sqlite3.OperationalError(f"database is locked: {secret}")))
    busy = client.get("/runs/r1", headers=BEARER)
    assert busy.status_code == 503 and only_a_code(busy) == "warehouse_unavailable" and secret not in busy.text
    monkeypatch.setattr(api, "_has_table", lambda conn, name: (_ for _ in ()).throw(RuntimeError(f"Traceback at {secret}")))
    broken = client.get("/runs/r1/review", headers=BEARER)
    assert broken.status_code == 500 and only_a_code(broken) == "internal_error" and secret not in broken.text and "Traceback" not in broken.text


def test_the_run_key_of_the_answer_is_the_one_the_run_was_given(replayed, monkeypatch):
    """The API makes the run's key from the instant it passes to ``run_all``, so the key it answers is the key of the state file and of the row, whatever the resolution of the clock."""
    from datetime import UTC, datetime

    seen = {}

    def spy(cfg, *args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(pipeline, "run_all", spy)
    body = client_of(replayed.cfg).post("/runs", headers=BEARER).json()
    assert isinstance(seen["now"], datetime) and seen["now"].tzinfo is UTC and body["run_key"] == pipeline.make_run_key(seen["now"])


def test_the_numbers_of_a_run_are_its_decisions_and_by_reason_counts_only_the_reasons_of_those_for_review(by_hand, monkeypatch):
    """Run ``r1`` of the hand-made warehouse issued three invoices: two for ``auto_post`` (one of them failed to post: that is for the review queue, not for ``review``) and one for review."""
    monkeypatch.setattr(pipeline, "make_run_key", lambda source: "r1")
    monkeypatch.setattr(pipeline, "run_all", lambda cfg, *args, **kwargs: [])
    body = client_of(by_hand.cfg).post("/runs", headers=BEARER).json()
    assert (body["run_key"], body["status"], body["invoices"], body["auto_post"], body["review"], body["by_reason"]) == ("r1", "ok", 3, 2, 1, {"qr_not_read": 1})
