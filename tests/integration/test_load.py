"""The warehouse and the load of a delivery (U12; AC21 in part; RF08; plan section 5, decisions D24, D28): a load is one transaction that registers the delivery, the validation, the run
and one row per file, runs the controls and rolls everything back, the schema included, if any of them has a row; the tables of the flow are insert-only in the database itself;
and the runner keeps the rules of projects 01 to 06 (autocommit, explicit transactions, no ``executescript``)."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from invoiceflow import warehouse
from invoiceflow.match import REASONS
from invoiceflow.warehouse import (
    CheckFailedError,
    DeliveryAlreadyLoadedError,
    LoadRefusedError,
    SchemaDriftError,
    apply_schema,
    connect,
    load_delivery,
    run_checks,
    split_statements,
)
from tests.fixtures.loads import CONFIG_SHA, SHA, STARTED, accepted, report_of, sha, spec_of

LOAD_CHECKS = ["chk_delivery_file_count", "chk_invoice_status_rule", "chk_run_matches_delivery", "chk_validation_complete", "chk_validation_passed"]
FLOW_TABLES = ["extracted_fields", "qr_reads", "decisions", "decision_reasons", "erp_posts", "daily_ops", "model_registry"]
NO_DECISION = "NULL, NULL, NULL, NULL"


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "wh" / "invoiceflow.sqlite")
    yield conn
    conn.close()


def db_path(conn) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2])


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else ""


def tables(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def load(conn, delivery_id="D2026-10-01", run_key="run-1", report=None, digest=SHA):
    spec = spec_of(delivery_id)
    return load_delivery(conn, spec, report or report_of(spec, run_key), run_key, started_at=STARTED, config_sha256=CONFIG_SHA, sha256=digest)


@pytest.fixture
def broken_check(tmp_path, monkeypatch):
    """The SQL folder copied with one more control of the load that always finds a row."""
    copy = tmp_path / "sql"
    shutil.copytree(warehouse.SQL_DIR, copy)
    (copy / "checks" / "load" / "chk_zz_always_fails.sql").write_text("SELECT 'x' AS broken\n", encoding="utf-8")
    monkeypatch.setattr(warehouse, "SQL_DIR", copy)
    return copy


def test_failed_check_rolls_back_a_first_load(db, broken_check):
    """The first load of a warehouse with a control that finds a row leaves no table behind (the schema is created inside the transaction) and no row."""
    before = file_hash(db_path(db))
    with pytest.raises(CheckFailedError, match="chk_zz_always_fails") as error:
        load(db)
    assert [c.check_id for c in error.value.checks if c.violations] == ["chk_zz_always_fails"]
    assert tables(db) == []
    assert file_hash(db_path(db)) == before, "the file is as it was, byte for byte"
    assert not db.in_transaction


def test_flow_tables_reject_update_delete_and_replace(db):
    """The triggers say it in the database, whoever writes: an update, a delete, a second insert of a key, INSERT OR REPLACE, REPLACE and an upsert are refused, and the row stays as it was."""
    load(db)
    db.execute("INSERT INTO decisions VALUES ('D-00001', 'review', 'duplicate', 1, 'OC-2026-000001', '30000000007', '1210.00', 'PES', 'run-1', 0.5)")
    db.execute("INSERT INTO erp_posts VALUES ('D-00001', 'PINV-1', 'posted', 1, 0.1, 'run-1')")
    cases = {
        "decisions": ("invoice_key = 'D-00001'", "total = '1.00'", "INSERT INTO decisions VALUES ('D-00001', 'review', 'duplicate', 1, NULL, NULL, '9.00', 'PES', 'run-1', 1.0)"),
        "erp_posts": ("invoice_key = 'D-00001'", "status = 'post_failed'", "INSERT INTO erp_posts VALUES ('D-00001', 'PINV-2', 'posted', 1, 0.1, 'run-1')"),
    }
    for table, (where, assignment, insert) in cases.items():
        snapshot = db.execute(f"SELECT * FROM {table}").fetchall()
        column = assignment.split(" = ")[0]
        for statement in (f"UPDATE {table} SET {assignment}", f"UPDATE {table} SET {assignment} WHERE {where}", f"DELETE FROM {table}", f"DELETE FROM {table} WHERE {where}", insert,
                          insert.replace("INSERT INTO", "INSERT OR REPLACE INTO"), insert.replace("INSERT INTO", "REPLACE INTO"),
                          f"{insert} ON CONFLICT DO UPDATE SET {assignment}", f"{insert} ON CONFLICT DO UPDATE SET {column} = {column}"):
            with pytest.raises(sqlite3.DatabaseError, match="solo inserción"):
                db.execute(statement)
        assert db.execute(f"SELECT * FROM {table}").fetchall() == snapshot, f"{table} is as it was"


def test_every_table_of_the_flow_has_its_three_triggers_and_so_do_the_load_tables_that_must_not_change(db):
    apply_schema(db)
    triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    for table in (*FLOW_TABLES, "validation_results", "invoices"):
        assert {f"{table}_no_update", f"{table}_no_delete", f"{table}_no_second_insert"} <= triggers, table
    for table in ("deliveries", "pipeline_runs"):
        assert not [t for t in triggers if t.startswith(table)], f"{table} is updated by the flow"
    assert set(tables(db)) >= {"deliveries", "pipeline_runs", "validation_results", "invoices", *FLOW_TABLES}


def test_each_flow_table_takes_a_row_once_and_refuses_it_twice(db):
    load(db)
    statements = {
        "extracted_fields": "INSERT INTO extracted_fields VALUES ('D-00001', 'header.nro', 0, '00000010', '10', 'tiny-1', 0.1)",
        "qr_reads": "INSERT INTO qr_reads (invoice_key, reader, status, invalid_reason, seconds) VALUES ('D-00001', 'pyzbar', 'invalid', 'host', 0.1)",
        "decisions": "INSERT INTO decisions VALUES ('D-00001', 'auto_post', NULL, 0, 'OC-1', '30000000007', '1210.00', 'PES', 'run-1', 1.0)",
        "daily_ops": "INSERT INTO daily_ops VALUES (20261001, 3, 2, 1, 1, 1, 1, 0, 1.5, 2.5, 'run-1')",
        "model_registry": f"INSERT INTO model_registry VALUES ('tiny-1', '2026-10-01 00:00:00', 'tiny-1', '{SHA}', '{SHA}', '{CONFIG_SHA}', 2, '{{}}', '{SHA}')",
    }
    for table, insert in statements.items():
        db.execute(insert)
        with pytest.raises(sqlite3.DatabaseError, match="solo inserción"):
            db.execute(insert)
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    db.execute("INSERT INTO decisions VALUES ('D-00002', 'review', 'duplicate', 2, NULL, NULL, NULL, NULL, 'run-1', 1.0)")
    db.execute("INSERT INTO decision_reasons VALUES ('D-00002', 'duplicate', 7, '{}')")
    with pytest.raises(sqlite3.DatabaseError, match="solo inserción"):
        db.execute("INSERT INTO decision_reasons VALUES ('D-00002', 'duplicate', 7, '{\"a\": 1}')")
    db.execute("INSERT INTO decision_reasons VALUES ('D-00002', 'no_po', 8, '{}')")
    assert db.execute("SELECT COUNT(*) FROM decision_reasons").fetchone()[0] == 2, "another reason of the same invoice is another row"


def test_the_schema_constrains_what_it_can_so_a_wrong_row_is_refused_at_the_door(db):
    load(db)
    db.execute(f"INSERT INTO decisions VALUES ('D-00002', 'auto_post', NULL, 0, {NO_DECISION}, 'run-1', 1.0)")
    wrong = [
        f"INSERT INTO decisions VALUES ('D-00001', 'maybe', NULL, 0, {NO_DECISION}, 'run-1', 1.0)",                              # a decision that is not one
        f"INSERT INTO decisions VALUES ('D-00001', 'review', NULL, 1, {NO_DECISION}, 'run-1', 1.0)",                             # review without a first reason
        f"INSERT INTO decisions VALUES ('D-00001', 'auto_post', 'duplicate', 1, {NO_DECISION}, 'run-1', 1.0)",                   # auto_post with a reason
        f"INSERT INTO decisions VALUES ('D-00001', 'review', 'because', 1, {NO_DECISION}, 'run-1', 1.0)",                        # a reason nobody defined
        f"INSERT INTO decisions VALUES ('D-00001', 'review', 'duplicate', 0, {NO_DECISION}, 'run-1', 1.0)",                      # review with no reason counted
        f"INSERT INTO decisions VALUES ('NOPE', 'auto_post', NULL, 0, {NO_DECISION}, 'run-1', 1.0)",                             # an invoice nobody received
        f"INSERT INTO decisions VALUES ('D-00001', 'auto_post', NULL, 0, {NO_DECISION}, 'no-run', 1.0)",                         # a run that does not exist
        "INSERT INTO decision_reasons VALUES ('D-00001', 'duplicate', 7, '{}')",                                                  # a reason of a decision that is not there
        "INSERT INTO extracted_fields VALUES ('D-00001', 'header.nro', 1, 'x', 'x', 'm', 0.1)",                                   # a header field on line 1
        "INSERT INTO extracted_fields VALUES ('D-00001', 'weird.field', 0, 'x', 'x', 'm', 0.1)",
        "INSERT INTO qr_reads (invoice_key, reader, status, seconds) VALUES ('D-00001', 'pyzbar', 'not_read', 0.1)",              # not_read with a reader
        "INSERT INTO qr_reads (invoice_key, reader, status, seconds) VALUES ('D-00001', NULL, 'ok', 0.1)",                        # ok with nothing in it
        "INSERT INTO erp_posts VALUES ('D-00002', NULL, 'posted', 1, 0.1, 'run-1')",                                              # posted with no ERP name
        "INSERT INTO erp_posts VALUES ('D-00002', 'PINV-1', 'maybe', 1, 0.1, 'run-1')",
        "INSERT INTO erp_posts VALUES ('D-00002', 'PINV-1', 'posted', 2, 0.1, 'run-1')",
        "INSERT INTO daily_ops VALUES (1, 0, 0, 0, 0, 0, 0, 0, NULL, NULL, 'run-1')",                                             # not a date key
        f"INSERT INTO model_registry VALUES ('m', 'x', 'm', 'short', '{SHA}', '{SHA}', 1, '{{}}', '{SHA}')",
        f"INSERT INTO model_registry VALUES ('m', 'x', 'm', '{SHA}', 'short', '{SHA}', 1, '{{}}', '{SHA}')",
        f"INSERT INTO model_registry VALUES ('m', 'x', 'm', '{SHA}', '{SHA}', 'short', 1, '{{}}', '{SHA}')",
        f"INSERT INTO model_registry VALUES ('m', 'x', 'm', '{SHA}', '{SHA}', '{SHA}', 0, '{{}}', '{SHA}')",
        f"INSERT INTO model_registry VALUES ('m', 'x', 'm', '{SHA}', '{SHA}', '{SHA}', 1, 'not json', '{SHA}')",
        f"INSERT INTO model_registry VALUES ('m', 'x', 'm', '{SHA}', '{SHA}', '{SHA}', 1, '{{}}', 'short')",
    ]
    for statement in wrong:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(statement)
    db.execute(f"INSERT INTO decisions VALUES ('D-00001', 'review', 'duplicate', 1, {NO_DECISION}, 'run-1', 1.0)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO decision_reasons VALUES ('D-00001', 'because', 7, '{}')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO decision_reasons VALUES ('D-00001', 'duplicate', 7, 'not json')")


def test_the_reasons_the_schema_accepts_are_exactly_the_matchs_eighteen_in_their_rank(db):
    load(db)
    db.execute(f"INSERT INTO decisions VALUES ('D-00001', 'review', 'file_rejected', 18, {NO_DECISION}, 'run-1', 1.0)")
    for rank, reason in enumerate(REASONS, start=1):
        db.execute("INSERT INTO decision_reasons VALUES ('D-00001', ?, ?, '{}')", (reason, rank))
    assert [r[0] for r in db.execute("SELECT reason FROM decision_reasons ORDER BY rank")] == list(REASONS)
    db.execute(f"INSERT INTO decisions VALUES ('D-00002', 'review', 'file_rejected', 1, {NO_DECISION}, 'run-1', 1.0)")
    for rank in (0, 19):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO decision_reasons VALUES ('D-00002', 'qr_invalid', ?, '{}')", (rank,))
    text = (warehouse.SQL_DIR / "schema" / "002_flow.sql").read_text(encoding="utf-8")
    assert text.count("'file_rejected', 'qr_not_read'") == 2 and "'post_failed')" in text


def test_the_review_queue_is_the_decisions_in_review_and_the_posts_that_failed(db):
    load(db)
    for key in ("D-00001", "D-00002"):
        db.execute(f"INSERT INTO decisions VALUES (?, 'auto_post', NULL, 0, {NO_DECISION}, 'run-1', 1.0)", (key,))
    db.execute(f"INSERT INTO decisions VALUES ('D-00003', 'review', 'file_rejected', 1, {NO_DECISION}, 'run-1', 0.1)")
    db.execute("INSERT INTO erp_posts VALUES ('D-00001', 'PINV-1', 'posted', 1, 0.1, 'run-1')")
    db.execute("INSERT INTO erp_posts VALUES ('D-00002', NULL, 'post_failed', NULL, 0.1, 'run-1')")
    queue = db.execute("SELECT invoice_key, reasons, opened_at, run_key FROM review_queue ORDER BY invoice_key").fetchall()
    assert queue == [("D-00002", 1, "2026-10-02 08:30:15", "run-1"), ("D-00003", 1, "2026-10-02 08:30:15", "run-1")]


# --- the load ----------------------------------------------------------------------------------------------------------------


def test_a_load_registers_the_delivery_the_validation_the_run_and_a_row_per_file(db):
    result = load(db)
    assert (result.delivery_id, result.run_key, result.accepted, result.quarantined) == ("D2026-10-01", "run-1", 2, 1)
    assert [c.check_id for c in result.checks] == LOAD_CHECKS and all(c.violations == 0 for c in result.checks)
    assert db.execute("SELECT * FROM deliveries").fetchall() == [("D2026-10-01", "daily", "2026-10-01", "2026-10-02 00:00:00", 3, SHA, "loaded", "run-1")]
    run = db.execute("SELECT run_key, started_at, finished_at, status, delivery_id, as_of, config_sha256, artifact_sha256, invoices, message FROM pipeline_runs").fetchall()
    assert run == [("run-1", "2026-10-02 08:30:15", None, "running", "D2026-10-01", "2026-10-02 00:00:00", CONFIG_SHA, None, 3, None)]
    rows = db.execute("SELECT invoice_key, delivery_id, file_name, file_sha256, file_format, bytes, pixels, status, rule_id, received_at FROM invoices ORDER BY invoice_key").fetchall()
    assert rows == [
        ("D-00001", "D2026-10-01", "D-00001.png", sha("D-00001.png"), "png", 1011, 4000, "accepted", None, "2026-10-01 00:00:00"),
        ("D-00002", "D2026-10-01", "D-00002.jpg", sha("D-00002.jpg"), "jpeg", 1011, 4000, "accepted", None, "2026-10-01 00:00:00"),
        ("D-00003", "D2026-10-01", "D-00003.png", None, "png", 123, None, "quarantined", "V14", "2026-10-01 00:00:00"),
    ]
    results = db.execute("SELECT rule_id, severity, passed, files_affected, examples_json FROM validation_results ORDER BY rule_id").fetchall()
    assert [r[0] for r in results] == ["I01", "V10", "V11", "V12", "V13", "V14", "V15", "V16"]
    assert ("V14", "advertencia", 1, 1, '["D-00003.png"]') in results and ("V10", "bloqueante", 1, 0, "[]") in results
    assert not db.in_transaction


def test_a_launch_has_no_files_and_still_loads(db):
    spec = spec_of("L2026-09-30", "launch")
    result = load_delivery(db, spec, report_of(spec, "run-0", files=()), "run-0", started_at=STARTED, config_sha256=CONFIG_SHA, sha256=SHA)
    assert (result.accepted, result.quarantined) == (0, 0)
    assert db.execute("SELECT kind, files FROM deliveries").fetchall() == [("launch", 0)]


def test_a_load_without_an_approved_validation_of_this_run_is_refused_before_writing_anything(db):
    spec = spec_of()
    cases = {
        "another run": report_of(spec, "other-run"),
        "another delivery": report_of(spec_of("D2026-10-02"), "run-1"),
        "a blocking rule failed": report_of(spec, "run-1", failed=("V11",)),
        "the first blocking rule failed": report_of(spec, "run-1", failed=("V10",)),
        "no blocking rule at all": report_of(spec, "run-1", drop=("V10", "V11")),
        "one blocking rule missing": report_of(spec, "run-1", drop=("V11",)),
        "two files of one key": report_of(spec, "run-1", files=(accepted("D-1.png"), accepted("D-1.jpg", fmt="jpeg"))),
    }
    for name, report in cases.items():
        with pytest.raises(LoadRefusedError):
            load(db, report=report)
        assert tables(db) == [], f"{name}: not even the schema"
    load(db)   # and a good one still loads after all those refusals
    assert db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 3


def test_the_fingerprints_of_a_load_are_64_hex_characters(db):
    spec = spec_of()
    for digest in ("", "abc", "A" * 64, "a" * 63):
        with pytest.raises(ValueError, match="64"):
            load(db, digest=digest)
    with pytest.raises(ValueError, match="64"):
        load_delivery(db, spec, report_of(spec, "run-1"), "run-1", started_at=STARTED, config_sha256="x", sha256=SHA)
    assert tables(db) == []


def test_a_warning_that_failed_does_not_stop_the_load(db):
    result = load(db, report=report_of(spec_of(), "run-1", failed=("V12", "V16")))
    assert result.accepted == 2 and db.execute("SELECT passed FROM validation_results WHERE rule_id = 'V16'").fetchone() == (0,)


def test_a_failed_load_leaves_the_file_as_it_was_byte_for_byte(db):
    load(db)
    before = file_hash(db_path(db))
    with pytest.raises(DeliveryAlreadyLoadedError, match="D2026-10-01"):
        load(db, run_key="run-2", report=report_of(spec_of(), "run-2"))
    assert file_hash(db_path(db)) == before
    with pytest.raises(sqlite3.IntegrityError):
        load(db, "D2026-10-02", run_key="run-1", report=report_of(spec_of("D2026-10-02"), "run-1"))   # a run that is already recorded
    assert file_hash(db_path(db)) == before
    assert db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1 and not db.in_transaction


def test_a_second_delivery_loads_after_the_first_and_its_files_are_new_rows(db):
    load(db)
    spec2 = spec_of("D2026-10-02")
    load_delivery(db, spec2, report_of(spec2, "run-2", files=(accepted("E-00001.png"), accepted("E-00002.png"))), "run-2", started_at=STARTED, config_sha256=CONFIG_SHA, sha256="c" * 64)
    assert db.execute("SELECT delivery_id, files FROM deliveries ORDER BY delivery_id").fetchall() == [("D2026-10-01", 3), ("D2026-10-02", 2)]
    assert db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 5
    assert all(c.violations == 0 for c in run_checks(db, "load"))


DAMAGE = [
    ("INSERT INTO deliveries VALUES ('Dx', 'daily', '2026-10-09', '2026-10-10 00:00:00', 4, '%s', 'loaded', 'run-1')" % SHA, "chk_delivery_file_count"),
    ("INSERT INTO invoices VALUES ('X-1', 'D2026-10-01', 'X-1.png', NULL, 'png', 1, 1, 'accepted', NULL, '2026-10-01 00:00:00')", "chk_invoice_status_rule"),
    ("INSERT INTO invoices VALUES ('X-1', 'D2026-10-01', 'X-1.png', '%s', 'png', 1, 1, 'accepted', 'V12', '2026-10-01 00:00:00')" % SHA, "chk_invoice_status_rule"),
    ("INSERT INTO invoices VALUES ('X-1', 'D2026-10-01', 'X-1.png', '%s', 'png', 1, 1, 'quarantined', 'V12', '2026-10-01 00:00:00')" % SHA, "chk_invoice_status_rule"),
    ("INSERT INTO invoices VALUES ('X-1', 'D2026-10-01', 'X-1.png', NULL, 'png', 1, 1, 'quarantined', NULL, '2026-10-01 00:00:00')", "chk_invoice_status_rule"),
    ("UPDATE pipeline_runs SET invoices = 9", "chk_run_matches_delivery"),
    ("UPDATE pipeline_runs SET finished_at = '2026-10-02 09:00:00'", "chk_run_matches_delivery"),
    ("TRIGGER:UPDATE validation_results SET passed = 0 WHERE rule_id = 'V10'", "chk_validation_passed"),
    ("TRIGGER:DELETE FROM validation_results WHERE rule_id = 'V11'", "chk_validation_complete"),
]


@pytest.mark.parametrize("damage, control", DAMAGE)
def test_each_control_finds_the_damage_that_it_is_for(db, damage, control):
    """Each control of the load, written to find one kind of damage, finds it in a warehouse that was loaded right and made wrong by hand (the insert-only triggers are dropped first for the damage
    that needs an update or a delete)."""
    load(db)
    assert all(c.violations == 0 for c in run_checks(db, "load"))
    if damage.startswith("TRIGGER:"):
        for trigger in ("validation_results_no_update", "validation_results_no_delete"):
            db.execute(f"DROP TRIGGER {trigger}")
        damage = damage.removeprefix("TRIGGER:")
    db.execute(damage)
    found = {c.check_id: c.violations for c in run_checks(db, "load")}
    assert found[control] >= 1, f"{control} found nothing after: {damage}"
    only = run_checks(db, "load", only=[control])[0]
    assert only.violations == found[control] and only.sample, "and a sample of the rows"


def test_run_checks_names_its_group_and_its_controls(db):
    load(db)
    with pytest.raises(ValueError, match="grupo"):
        run_checks(db, "nope")
    with pytest.raises(ValueError, match="chk_nope"):
        run_checks(db, "load", only=["chk_nope"])
    assert [c.check_id for c in run_checks(db, "load", only=["chk_validation_passed"])] == ["chk_validation_passed"]
    assert run_checks(db, "model") == [], "the checks of the star schema come with the star schema (U17)"


def test_a_control_file_must_be_one_select(db, tmp_path, monkeypatch):
    copy = tmp_path / "sql"
    shutil.copytree(warehouse.SQL_DIR, copy)
    (copy / "checks" / "load" / "chk_two.sql").write_text("SELECT 1 WHERE 0;\nSELECT 2 WHERE 0;\n", encoding="utf-8")
    monkeypatch.setattr(warehouse, "SQL_DIR", copy)
    with pytest.raises(ValueError, match="exactamente un SELECT"):
        load(db)
    assert tables(db) == []


# --- the runner --------------------------------------------------------------------------------------------------------------


def test_the_connection_is_autocommit_with_the_projects_pragmas(db):
    assert db.isolation_level is None
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 2, "FULL"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == warehouse.BUSY_TIMEOUT_MS


def test_connect_creates_the_folder(tmp_path):
    target = tmp_path / "a" / "b" / "wh.sqlite"
    connect(target).close()
    assert target.parent.is_dir()


def test_the_runner_never_calls_executescript():
    source = Path(warehouse.__file__).read_text(encoding="utf-8")
    assert "executescript" not in source.replace("``executescript()``", "")


def test_sql_files_are_split_in_statements_keeping_semicolons_inside_strings_comments_and_triggers():
    text = ("-- a comment; with a semicolon\nCREATE TABLE t (a TEXT DEFAULT 'x;y'); /* block; comment */\n"
            "CREATE TRIGGER g BEFORE INSERT ON t BEGIN SELECT RAISE(ABORT, 'no; way'); END;\nSELECT 1")
    statements = split_statements(text)
    assert len(statements) == 3
    assert statements[0].startswith("-- a comment") and "DEFAULT 'x;y'" in statements[0] and "CREATE TRIGGER g" in statements[1] and statements[1].endswith("END;") and statements[2] == "SELECT 1"
    assert split_statements("-- nothing\n;;\n/* nothing */") == []
    split_statements(text).append("x")
    assert len(split_statements(text)) == 3, "the cached split is a new list each time"


def test_apply_schema_is_idempotent_keeps_the_data_and_refuses_a_table_that_drifted(db):
    apply_schema(db)
    apply_schema(db)
    load(db)
    apply_schema(db)
    assert db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 3
    db.execute("DROP VIEW review_queue")
    db.execute("DROP TABLE model_registry")
    db.execute("CREATE TABLE model_registry (model_version TEXT, extra INTEGER)")
    with pytest.raises(SchemaDriftError, match="model_registry") as error:
        apply_schema(db)
    assert "extra" in str(error.value) and error.value.table == "model_registry" and "trained_as_of" in error.value.missing and error.value.unexpected == ("extra",)
    assert not db.in_transaction


def test_apply_schema_joins_the_callers_transaction_and_is_undone_with_it(db):
    db.execute("BEGIN IMMEDIATE")
    apply_schema(db)
    assert db.in_transaction
    db.execute("ROLLBACK")
    assert tables(db) == []


def test_a_second_writer_waits_for_the_first_and_then_gets_in(tmp_path):
    first, second = connect(tmp_path / "w.sqlite"), connect(tmp_path / "w.sqlite")
    second.execute("PRAGMA busy_timeout=50")
    first.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        second.execute("BEGIN IMMEDIATE")
    first.execute("ROLLBACK")
    second.execute("BEGIN IMMEDIATE")
    second.execute("ROLLBACK")
    first.close()
    second.close()


def test_validation_v16_reads_the_table_the_load_fills(db, quick_view, tmp_path):
    """``validate_delivery`` looks at ``invoices.file_sha256`` for V16: after a load, a file with the bytes of a loaded one is flagged (and processed all the same); before it, it is not."""
    from invoiceflow.deliveries import delivery_plan
    from invoiceflow.validate import validate_delivery
    from tests.fixtures.bad_files import png_bytes

    plan = delivery_plan(quick_view)
    data = png_bytes((64, 80), color=120)
    digest = hashlib.sha256(data).hexdigest()
    inbox = tmp_path / "d" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "b.png").write_bytes(data)
    entry = {"status": "published", "files": {"b.png": {"bytes": len(data), "sha256": digest}}, "truth": None}
    manifest = {"version": 1, "deliveries": {plan[0].delivery_id: {"status": "loaded"}, plan[1].delivery_id: {"status": "loaded"}, plan[2].delivery_id: entry}}

    def v16(run_key):
        report = validate_delivery(plan[2], inbox, db, quick_view, run_key=run_key, manifest=manifest, truth_path=None)
        assert [f.status for f in report.files] == ["accepted"], "processed all the same"
        return next((r.passed, r.examples) for r in report.results if r.rule_id == "V16")

    assert v16("before") == (True, ())
    load_delivery(db, plan[1], report_of(plan[1], "run-1", files=(accepted("a.png", digest),)), "run-1", started_at=STARTED, config_sha256=CONFIG_SHA, sha256=SHA)
    assert v16("after") == (False, ("b.png",))


def test_the_core_tables_refuse_a_row_that_is_not_one(db):
    load(db)
    wrong = [
        f"INSERT INTO deliveries VALUES ('Dz', 'weekly', '2026-10-09', '2026-10-10 00:00:00', 0, '{SHA}', 'loaded', 'run-1')",
        f"INSERT INTO deliveries VALUES ('Dz', 'daily', '2026-10-09', '2026-10-10 00:00:00', 0, '{SHA}', 'maybe', 'run-1')",
        "INSERT INTO deliveries VALUES ('Dz', 'daily', '2026-10-09', '2026-10-10 00:00:00', 0, 'short', 'loaded', 'run-1')",
        "INSERT INTO deliveries VALUES ('Dz', 'daily', '2026-10-09', '2026-10-10 00:00:00', -1, '%s', 'loaded', 'run-1')" % SHA,
        "INSERT INTO pipeline_runs (run_key, started_at, status) VALUES ('r', '2026-10-02 00:00:00', 'weird')",
        "INSERT INTO pipeline_runs (run_key, started_at, status, config_sha256) VALUES ('r', '2026-10-02 00:00:00', 'running', 'short')",
        "INSERT INTO pipeline_runs (run_key, started_at, status, artifact_sha256) VALUES ('r', '2026-10-02 00:00:00', 'running', 'short')",
        "INSERT INTO pipeline_runs (run_key, started_at, status, delivery_id) VALUES ('r', '2026-10-02 00:00:00', 'running', 'no-delivery')",
        "INSERT INTO validation_results VALUES ('run-1', 'V99', 'x', 'grave', 'entrega', 'per_run', 1, 0, '[]')",
        "INSERT INTO validation_results VALUES ('run-1', 'V99', 'x', 'bloqueante', 'entrega', 'sometimes', 1, 0, '[]')",
        "INSERT INTO validation_results VALUES ('run-1', 'V99', 'x', 'bloqueante', 'entrega', 'per_run', 2, 0, '[]')",
        "INSERT INTO validation_results VALUES ('run-1', 'V99', 'x', 'bloqueante', 'entrega', 'per_run', 1, 0, 'not json')",
        "INSERT INTO validation_results VALUES ('no-run', 'V99', 'x', 'bloqueante', 'entrega', 'per_run', 1, 0, '[]')",
        "INSERT INTO invoices VALUES ('X', 'D2026-10-01', 'X.png', 'short', 'png', 1, 1, 'accepted', NULL, '2026-10-01 00:00:00')",
        "INSERT INTO invoices VALUES ('X', 'D2026-10-01', 'X.png', NULL, 'png', 1, 1, 'maybe', NULL, '2026-10-01 00:00:00')",
        "INSERT INTO invoices VALUES ('X', 'D2026-10-01', 'X.png', NULL, 'png', -1, 1, 'quarantined', 'V12', '2026-10-01 00:00:00')",
        "INSERT INTO invoices VALUES ('X', 'no-delivery', 'X.png', NULL, 'png', 1, 1, 'quarantined', 'V12', '2026-10-01 00:00:00')",
    ]
    for statement in wrong:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(statement)


def test_a_load_is_bracketed_by_begin_immediate_and_commit_and_a_failed_one_by_begin_immediate_and_rollback(db, broken_check):
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    with pytest.raises(CheckFailedError):
        load(db)
    kinds = [s for s in statements if s in ("BEGIN IMMEDIATE", "BEGIN", "COMMIT", "ROLLBACK")]
    assert kinds == ["BEGIN IMMEDIATE", "ROLLBACK"], kinds
    db.set_trace_callback(None)
    (broken_check / "checks" / "load" / "chk_zz_always_fails.sql").unlink()
    statements.clear()
    db.set_trace_callback(statements.append)
    load(db)
    assert [s for s in statements if s in ("BEGIN IMMEDIATE", "BEGIN", "COMMIT", "ROLLBACK")] == ["BEGIN IMMEDIATE", "COMMIT"]


def test_apply_schema_on_its_own_is_one_transaction(db):
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    apply_schema(db)
    kinds = [s for s in statements if s in ("BEGIN IMMEDIATE", "BEGIN", "COMMIT", "ROLLBACK")]
    assert kinds == ["BEGIN", "COMMIT"], kinds


def test_a_blocking_rule_of_the_report_that_failed_stops_the_load_whatever_its_id(db):
    from dataclasses import replace

    from invoiceflow.validate import RuleResult

    spec = spec_of()
    report = report_of(spec, "run-1")
    extra = RuleResult("V99", "una regla bloqueante nueva", "bloqueante", "entrega", "per_run", False, 0, (), {})
    with pytest.raises(LoadRefusedError, match="V99"):
        load(db, report=replace(report, results=(*report.results, extra)))
    assert tables(db) == []
