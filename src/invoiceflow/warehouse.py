"""The SQLite warehouse: connection, SQL runner, schema, the load of a delivery and the controls (RF08; AC21 in part; plan section 5; decisions D24, D28); adapted from project 06.

Rules of the runner, the same as in projects 01 to 06:

- autocommit connection (``isolation_level=None``); transactions are explicit ``BEGIN IMMEDIATE``;
- ``journal_mode=DELETE`` (WAL does not behave on Docker Desktop mounts), ``synchronous=FULL``, ``foreign_keys=ON``;
- never ``executescript()``: it commits the open transaction first. SQL files are split into statements with ``sqlite3.complete_statement`` and run one by one with named parameters, so a
  failure can roll the whole load back; the schema is created inside the transaction too, so a first load that fails leaves no table behind;
- a failure never commits: the transaction is rolled back with ``ROLLBACK`` and the error that made it necessary is the one that reaches the caller (project 05's R4-store-validation-commit-on-error).

A load is all or nothing: the delivery's row, the validation's rows, the run's row (``running``: ``report`` closes it) and one row per file of the inbox, accepted or quarantined, and the
controls of ``sql/checks/load/`` are one transaction. It is refused, writing nothing, without a validation of this run that passed every blocking rule, and a delivery already loaded or a run
already recorded leaves the warehouse byte for byte as it was. The tables of ``sql/schema/002_flow.sql`` are insert-only in the database itself (three triggers each): the flow cannot rewrite what it issued.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from invoiceflow.deliveries import DeliverySpec
from invoiceflow.validate import BLOCKING, ValidationReport

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
CHECK_GROUPS = ("load", "model")
SAMPLE_LIMIT = 5
BUSY_TIMEOUT_MS = 30_000       # an overlapping writer waits this long for the write lock before failing (runbook R8)
CACHE_KIB = 65_536             # the page cache of a connection (negative ``cache_size`` is in KiB), in memory only
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    violations: int
    sample: list[tuple]


@dataclass(frozen=True)
class LoadResult:
    delivery_id: str
    run_key: str
    accepted: int
    quarantined: int
    checks: list[CheckResult]


class LoadRefusedError(RuntimeError):
    """The load was refused before anything was written: there is no validation of this run that passed (``load`` never runs without one), or the files cannot be told apart."""


class DeliveryAlreadyLoadedError(RuntimeError):
    """The delivery is already in the warehouse: loading it again would duplicate its invoices."""

    def __init__(self, delivery_id: str) -> None:
        super().__init__(f"La entrega {delivery_id} ya está cargada en el almacén; no se carga de nuevo")


class CheckFailedError(RuntimeError):
    """A control returned rows: the transaction was rolled back."""

    def __init__(self, checks: list[CheckResult], step: str = "La carga") -> None:
        self.checks, self.step = checks, step
        failing = [c for c in checks if c.violations]
        detail = "; ".join(f"{c.check_id}: {c.violations} filas, por ejemplo {c.sample[:2]}" for c in failing)
        super().__init__(f"{step} se deshizo; controles con filas que los violan: {detail}")


class SchemaDriftError(RuntimeError):
    """A table of the warehouse does not have the columns its SQL definition declares (runbook R11)."""

    def __init__(self, table: str, source: str, missing: tuple[str, ...], unexpected: tuple[str, ...], retyped: tuple[str, ...]) -> None:
        self.table, self.missing, self.unexpected, self.retyped = table, missing, unexpected, retyped
        parts = [text for text, columns in ((f"faltan las columnas {list(missing)}", missing), (f"sobran las columnas {list(unexpected)}", unexpected),
                                            (f"tienen otro tipo las columnas {list(retyped)}", retyped)) if columns]
        super().__init__(f"La tabla {table} no coincide con su definición en sql/schema/{source}: {'; '.join(parts)}. "
                         "El almacén lo creó una versión anterior: se reconstruye desde cero (docs/runbook.md, R11).")


# --- connection and SQL files -------------------------------------------------------------------------------------------


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating the folder and the file) the warehouse in autocommit mode with the project's pragmas."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA cache_size=-{CACHE_KIB}")   # memory only: nothing in the file or in a result depends on it
    return conn


def _has_sql(text: str) -> bool:
    """True if `text` has anything besides whitespace, semicolons and comments."""
    i, n = 0, len(text)
    while i < n:
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif text[i].isspace() or text[i] == ";":
            i += 1
        else:
            return True
    return False


def split_statements(sql_text: str) -> list[str]:
    """Split a SQL file into statements, keeping semicolons inside strings, comments and triggers (a new list: the split is cached by text)."""
    return list(_split_statements(sql_text))


@functools.lru_cache(maxsize=256)
def _split_statements(sql_text: str) -> tuple[str, ...]:
    statements: list[str] = []
    start = 0
    for position, character in enumerate(sql_text):
        if character == ";" and sqlite3.complete_statement(sql_text[start : position + 1]):
            chunk = sql_text[start : position + 1]
            if _has_sql(chunk):
                statements.append(chunk.strip())
            start = position + 1
    if _has_sql(sql_text[start:]):   # a last statement without its semicolon
        statements.append(sql_text[start:].strip())
    return tuple(statements)


def run_sql_file(conn: sqlite3.Connection, path: Path, params: Mapping[str, object] | None = None) -> None:
    """Execute the statements of a SQL file one by one, binding the named parameters each uses."""
    for statement in split_statements(Path(path).read_text(encoding="utf-8")):
        conn.execute(statement, dict(params or {}))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _declared_tables(path: Path) -> dict[str, dict[str, str]]:
    """``{table: {column: declared type}}`` of a schema file, read by creating its tables in memory (cached by the file's size and time)."""
    stat = Path(path).stat()
    return _declared_tables_cached(str(path), stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=16)
def _declared_tables_cached(path: str, size: int, mtime_ns: int) -> dict[str, dict[str, str]]:
    del size, mtime_ns   # only part of the cache key
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.execute("PRAGMA foreign_keys=OFF")
        for statement in split_statements(Path(path).read_text(encoding="utf-8")):
            scratch.execute(statement)
        names = [r[0] for r in scratch.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {t: {r[1]: (r[2] or "").upper() for r in scratch.execute(f'PRAGMA table_info("{t}")')} for t in names}
    finally:
        scratch.close()


def _check_no_drift(conn: sqlite3.Connection, path: Path) -> None:
    for table, declared in _declared_tables(path).items():
        if not _table_exists(conn, table):
            continue
        actual = {r[1]: (r[2] or "").upper() for r in conn.execute(f'PRAGMA table_info("{table}")')}
        missing = tuple(c for c in declared if c not in actual)
        unexpected = tuple(c for c in actual if c not in declared)
        retyped = tuple(c for c in declared if c in actual and declared[c] != actual[c])
        if missing or unexpected or retyped:
            raise SchemaDriftError(table, Path(path).name, missing, unexpected, retyped)


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create the tables of ``sql/schema`` that do not exist yet (idempotent; keeps the data). A table that exists with other columns than its file declares is not migrated:
    ``SchemaDriftError`` names it and its columns.

    Called in autocommit mode, the statements run in one transaction of their own, which is committed whatever happens (what an error leaves is what each statement before it did, as in
    autocommit), because a statement outside a transaction takes and gives back the file's lock by itself, which is most of the cost of a no-op ``CREATE ... IF NOT EXISTS`` when everything
    exists. Called inside the caller's transaction, it joins it."""
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN")
    try:
        for path in sorted((SQL_DIR / "schema").glob("*.sql")):
            _check_no_drift(conn, path)
            run_sql_file(conn, path)
    finally:
        if own and conn.in_transaction:
            conn.execute("COMMIT")


# --- controls --------------------------------------------------------------------------------------------------------------


def run_checks(conn: sqlite3.Connection, group: Literal["load", "model"], params: Mapping[str, object] | None = None, *, only: Iterable[str] | None = None) -> list[CheckResult]:
    """Run the controls of a group, in file-name order (or only those named in `only`); each file selects the rows that break its rule (none: it passes)."""
    if group not in CHECK_GROUPS:
        raise ValueError(f"grupo de controles desconocido: {group!r}; se esperaba uno de {', '.join(CHECK_GROUPS)}")
    bound = dict(params or {})
    paths = sorted((SQL_DIR / "checks" / group).glob("chk_*.sql"))
    if only is not None:
        wanted = list(only)
        unknown = [name for name in wanted if name not in {p.stem for p in paths}]
        if unknown:
            raise ValueError(f"no hay un control {', '.join(unknown)} en el grupo {group}")
        paths = [p for p in paths if p.stem in wanted]
    results = []
    for path in paths:
        statements = split_statements(path.read_text(encoding="utf-8"))
        if len(statements) != 1:
            raise ValueError(f"{path.name} debe tener exactamente un SELECT y tiene {len(statements)} sentencias")
        select = statements[0].rstrip().rstrip(";")
        violations = conn.execute(f"SELECT COUNT(*) FROM ({select})", bound).fetchone()[0]
        sample = conn.execute(f"SELECT * FROM ({select}) LIMIT {SAMPLE_LIMIT}", bound).fetchall() if violations else []
        results.append(CheckResult(path.stem, int(violations), sample))
    return results


def _fail_on(checks: list[CheckResult], what: str, step: str = "La carga") -> None:
    """``CheckFailedError`` if a control has rows. It is raised inside the caller's open transaction, so the log says which control failed."""
    failing = [c for c in checks if c.violations]
    for c in failing:
        logger.error("%s: control %s failed, violations=%d sample=%s", what, c.check_id, c.violations, c.sample)
    if failing:
        raise CheckFailedError(checks, step)


# --- load ------------------------------------------------------------------------------------------------------------------


def _stamp(value: datetime) -> str:
    """An instant as the warehouse stores it: ``YYYY-MM-DD HH:MM:SS``."""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _require_a_passed_validation(spec: DeliverySpec, report: ValidationReport, run_key: str) -> None:
    if report.run_key != run_key:
        raise LoadRefusedError(f"La carga {run_key} se rechazó: la validación que se le pasó es de la corrida {report.run_key}, no de esta")
    if report.delivery_id != spec.delivery_id:
        raise LoadRefusedError(f"La carga {run_key} se rechazó: la validación es de la entrega {report.delivery_id} y se carga {spec.delivery_id}")
    present = {result.rule_id for result in report.results}
    if not {"V10", "V11"} <= present:
        raise LoadRefusedError(f"La carga {run_key} se rechazó: la validación no tiene las reglas bloqueantes V10 y V11 (tiene {sorted(present)})")
    failed = [result.rule_id for result in report.results if result.severity == BLOCKING and not result.passed]
    if failed:
        raise LoadRefusedError(f"La carga {run_key} se rechazó: la validación no pasó {', '.join(failed)}")
    stems = [Path(check.name).stem for check in report.files]
    repeated = sorted({stem for stem in stems if stems.count(stem) > 1})
    if repeated:
        raise LoadRefusedError(f"La carga {run_key} se rechazó: hay archivos con el mismo nombre sin extensión (la clave de la factura): {repeated}")


def load_delivery(conn: sqlite3.Connection, spec: DeliverySpec, report: ValidationReport, run_key: str, *, started_at: datetime, config_sha256: str, sha256: str) -> LoadResult:
    """Record the delivery `spec`, the validation `report` of this run, the run (``running``) and a row of ``invoices`` for each file of the report, accepted or quarantined, in one
    transaction with the controls of ``sql/checks/load/``. `sha256` is the delivery's fingerprint (the manifest's), `config_sha256` the sealed configuration's.

    Refused before writing (``LoadRefusedError``) when `report` is not this run's, this delivery's and a validation that passed every blocking rule. A delivery already loaded
    (``DeliveryAlreadyLoadedError``), a run already recorded or a control with rows rolls everything back, apply_schema included, and the warehouse keeps exactly what it had."""
    if not (_HEX64.fullmatch(sha256) and _HEX64.fullmatch(config_sha256)):
        raise ValueError("sha256 y config_sha256 de la carga deben ser 64 caracteres hexadecimales en minúscula")
    _require_a_passed_validation(spec, report, run_key)
    accepted = sum(1 for check in report.files if check.status == "accepted")
    quarantined = len(report.files) - accepted
    received_at = f"{spec.day.isoformat()} 00:00:00"
    conn.execute("BEGIN IMMEDIATE")
    try:
        apply_schema(conn)
        if conn.execute("SELECT 1 FROM deliveries WHERE delivery_id = ?", (spec.delivery_id,)).fetchone() is not None:
            raise DeliveryAlreadyLoadedError(spec.delivery_id)
        conn.execute("INSERT INTO deliveries (delivery_id, kind, day, as_of, files, sha256, status, run_key) VALUES (?, ?, ?, ?, ?, ?, 'loaded', ?)",
                     (spec.delivery_id, spec.kind, spec.day.isoformat(), _stamp(spec.as_of), len(report.files), sha256, run_key))
        conn.execute("INSERT INTO pipeline_runs (run_key, started_at, status, delivery_id, as_of, config_sha256, invoices) VALUES (?, ?, 'running', ?, ?, ?, ?)",
                     (run_key, _stamp(started_at), spec.delivery_id, _stamp(spec.as_of), config_sha256, len(report.files)))
        conn.executemany("INSERT INTO validation_results (run_key, rule_id, name_es, severity, scope, aggregation, passed, files_affected, examples_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(run_key, r.rule_id, r.name_es, r.severity, r.scope, r.aggregation, int(r.passed), r.files_affected, json.dumps(list(r.examples))) for r in report.results])
        conn.executemany("INSERT INTO invoices (invoice_key, delivery_id, file_name, file_sha256, file_format, bytes, pixels, status, rule_id, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(Path(c.name).stem, spec.delivery_id, c.name, c.sha256, c.file_format, c.bytes, c.pixels, c.status, c.rule_id, received_at) for c in report.files])
        checks = run_checks(conn, "load")
        _fail_on(checks, f"load {run_key}")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        logger.warning("load %s rolled back: the warehouse keeps what it had", run_key)
        raise
    logger.info("load %s: delivery=%s accepted=%d quarantined=%d controls=%d", run_key, spec.delivery_id, accepted, quarantined, len(checks))
    return LoadResult(spec.delivery_id, run_key, accepted, quarantined, checks)
