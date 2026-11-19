"""The entry API: one run on request, and what a run left (RF24, RNF11; AC34; plan, section 18; decisions D26, D27).

``create_app(cfg)`` is a FastAPI application over the project the configuration names, with four routes and nothing else (no documentation page, no schema):

* ``POST /runs``: the next delivery, run by ``pipeline.run_all`` (the same tasks, in the same order and with the same functions as ``python -m invoiceflow run`` and the Airflow DAG), holding the
  writer lock of the data folder; the answer is ``{run_key, status, delivery_id, invoices, auto_post, review, by_reason}`` (``by_reason`` counts the first reason of each invoice sent to review).
  Another writer, a command line or a DAG task of this machine or of a container that shares the folder, makes it a 409 and nothing is written.
* ``GET /runs/{run_key}``: the run's row of ``pipeline_runs``; ``GET /runs/{run_key}/review``: its review queue (the invoices' keys, their reasons in the order of the plan and the fields the model
  read, never the file); ``GET /health``: ``{"status": "ok", "sealed": <the launch seal exists>}``.

Every request needs ``Authorization: Bearer <token>``, with the token in the environment variable ``[service] token_env`` names (without one the application does not start); it is compared with
``hmac.compare_digest`` before the route is looked at, so an unknown route and a wrong method are 401 too. An error is ``{"code": <fixed word>}``: no path of this machine, no trace, nothing of
the request or of what the pipeline said. The reads open the warehouse read-only and never create it.

``python -m invoiceflow serve`` runs it with uvicorn on ``127.0.0.1`` only; in Docker the ``api`` service of the compose file runs ``create_app_from_environment`` with uvicorn on the internal
network of the compose project, with no published port.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError

from invoiceflow import pipeline
from invoiceflow.config import PipelineConfig, load_config
from invoiceflow.lock import RunLockedError, RunRefusedError
from invoiceflow.sources import SourceError
from invoiceflow.validate import ValidationError

logger = logging.getLogger(__name__)

RUN_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
BUSY_SECONDS = 5


class ApiStartError(RuntimeError):
    """The application cannot start: it has no token."""


def _reply(status: int, payload: Any, headers: dict[str, str] | None = None) -> Response:
    return Response(content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")), status_code=status, media_type="application/json", headers=headers)


def _error(status: int, code: str, headers: dict[str, str] | None = None) -> Response:
    return _reply(status, {"code": code}, headers)


def _read_only(cfg: PipelineConfig) -> sqlite3.Connection | None:
    """The warehouse for reading, or None before the first load: asking must not create it."""
    path = Path(cfg.paths.warehouse)
    if not path.is_file():
        return None
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=BUSY_SECONDS)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (name,)).fetchone() is not None


def _run_row(conn: sqlite3.Connection | None, run_key: str) -> dict[str, Any] | None:
    if conn is None or not _has_table(conn, "pipeline_runs"):
        return None
    cursor = conn.execute("SELECT * FROM pipeline_runs WHERE run_key = ?", (run_key,))
    row = cursor.fetchone()
    return None if row is None else dict(zip((d[0] for d in cursor.description), row, strict=True))


def _counts(conn: sqlite3.Connection | None, run_key: str) -> tuple[int, int, dict[str, int]]:
    """The invoices the run issued, how many it sent to review and, of those, how many for each first reason."""
    if conn is None or not _has_table(conn, "decisions"):
        return 0, 0, {}
    decisions = dict(conn.execute("SELECT decision, COUNT(*) FROM decisions WHERE run_key = ? GROUP BY decision", (run_key,)).fetchall())
    reasons = dict(conn.execute("SELECT first_reason, COUNT(*) FROM decisions WHERE run_key = ? AND decision = 'review' GROUP BY first_reason ORDER BY first_reason", (run_key,)).fetchall())
    return sum(decisions.values()), decisions.get("review", 0), reasons


def _review_queue(conn: sqlite3.Connection | None, run_key: str) -> list[dict[str, Any]]:
    if conn is None or not _has_table(conn, "review_queue"):
        return []
    keys = [row[0] for row in conn.execute("SELECT invoice_key FROM review_queue WHERE run_key = ? ORDER BY invoice_key", (run_key,))]
    failed = {row[0] for row in conn.execute("SELECT invoice_key FROM erp_posts WHERE status = 'post_failed' AND run_key = ?", (run_key,))}
    queue = []
    for key in keys:
        reasons = [row[0] for row in conn.execute("SELECT reason FROM decision_reasons WHERE invoice_key = ? ORDER BY rank", (key,))]
        if key in failed and "post_failed" not in reasons:
            reasons.append("post_failed")
        fields = [{"field": field, "ordinal": ordinal, "value": norm if norm is not None else raw}
                  for field, ordinal, raw, norm in conn.execute("SELECT field, ordinal, value_raw, value_norm FROM extracted_fields WHERE invoice_key = ? ORDER BY field, ordinal", (key,))]
        queue.append({"invoice_key": key, "reasons": reasons, "fields": fields})
    return queue


def create_app(cfg: PipelineConfig, env: Mapping[str, str] | None = None) -> FastAPI:
    """The application over `cfg`; the token is the variable ``[service] token_env`` names in `env` (the environment of the process by default). Without it the application does not start."""
    env = os.environ if env is None else env
    token = env.get(cfg.service.token_env, "")
    if not token.strip():
        raise ApiStartError(f"la API necesita el token en la variable de entorno {cfg.service.token_env}: sin él no arranca")
    expected = f"Bearer {token}".encode()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        supplied = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(supplied, expected):
            return _error(401, "unauthorized", {"WWW-Authenticate": "Bearer"})
        return await call_next(request)

    @app.exception_handler(404)
    async def not_found(request: Request, error: Exception):
        return _error(404, "not_found")

    @app.exception_handler(405)
    async def method_not_allowed(request: Request, error: Exception):
        return _error(405, "method_not_allowed")

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, error: RequestValidationError):
        return _error(400, "bad_request")

    @app.exception_handler(sqlite3.Error)
    async def warehouse_unavailable(request: Request, error: sqlite3.Error):
        logger.warning("the warehouse could not be read: %s", error)
        return _error(503, "warehouse_unavailable")

    @app.exception_handler(Exception)
    async def crash(request: Request, error: Exception):
        logger.error("request failed", exc_info=error)
        return _error(500, "internal_error")

    def reading(run_key: str, answer):
        if not RUN_KEY.fullmatch(run_key):
            return _error(404, "run_not_found")
        conn = _read_only(cfg)
        try:
            if _run_row(conn, run_key) is None:
                return _error(404, "run_not_found")
            return _reply(200, answer(conn))
        finally:
            if conn is not None:
                conn.close()

    @app.get("/health")
    def health() -> Response:
        return _reply(200, {"status": "ok", "sealed": Path(cfg.paths.seal).is_file()})

    @app.post("/runs")
    def start_run() -> Response:
        now = datetime.now(UTC)
        run_key = pipeline.make_run_key(now)
        try:
            outcome = pipeline.run_all(cfg, now=now)
        except RunLockedError:
            return _error(409, "run_locked")
        except RunRefusedError:
            return _error(409, "run_refused")
        except (ValidationError, SourceError):
            return _error(422, "delivery_rejected")
        except Exception as error:   # a task failed: its record is in the state of the run and the traceback in this process' log, never in the answer
            logger.error("run %s failed", run_key, exc_info=error)
            return _error(500, "run_failed")
        delivered = next((step["result"].get("delivery_id") for step in outcome if step.get("task") == "deliver"), None)
        ran = (outcome[-1]["result"].get("run_status") if outcome else None) or "ok"
        try:
            conn = _read_only(cfg)
            try:
                row = _run_row(conn, run_key)
                invoices, review, by_reason = _counts(conn, run_key)
            finally:
                if conn is not None:
                    conn.close()
        except sqlite3.Error as error:   # the run is committed: a 503 would make a client that retries run the next delivery (A4 of the evaluation, D63); the numbers are in GET /runs/<key>
            logger.warning("run %s finished but its numbers could not be read: %s", run_key, error)
            return _reply(200, {"run_key": run_key, "status": ran, "delivery_id": delivered, "invoices": None, "auto_post": None, "review": None, "by_reason": None})
        status = row["status"] if row else ran
        return _reply(200, {"run_key": run_key, "status": status, "delivery_id": row["delivery_id"] if row else None, "invoices": invoices, "auto_post": invoices - review, "review": review,
                            "by_reason": by_reason})

    @app.get("/runs/{run_key}")
    def read_run(run_key: str) -> Response:
        return reading(run_key, lambda conn: _run_row(conn, run_key))

    @app.get("/runs/{run_key}/review")
    def read_review(run_key: str) -> Response:
        return reading(run_key, lambda conn: {"run_key": run_key, "invoices": _review_queue(conn, run_key)})

    return app


def create_app_from_environment() -> FastAPI:
    """The application of the process: the configuration of ``$INVOICEFLOW_CONFIG`` (or the default one) and the token of the environment. What uvicorn's ``--factory`` calls in Docker."""
    return create_app(load_config())


__all__ = ["ApiStartError", "create_app", "create_app_from_environment"]
