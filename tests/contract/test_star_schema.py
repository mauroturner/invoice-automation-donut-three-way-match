"""The star model for Power BI (U17; RF21, AC26; D37; plan, section 19): ten dimensions and ten facts with their keys, the 17 relationships (one to many, single direction, active), the marts that
fill them from the warehouse as of the run, the controls that undo them and the Parquet files with the schema of the specification."""

from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from invoiceflow import reporting, texts, warehouse
from invoiceflow.dashboard.semantic_model import ARROW_TYPES, MODEL, Relationship, arrow_schema, validate_model
from invoiceflow.export import STAR_MANIFEST, export_star_schema, verify_star_schema
from invoiceflow.registry import RunMeta
from invoiceflow.warehouse import CheckFailedError, connect

DIMENSIONS = ("dim_date", "dim_supplier", "dim_reason", "dim_field", "dim_cohort", "dim_scan", "dim_planted", "dim_baseline", "dim_model", "dim_pipeline_run")
FACTS = ("fact_invoice", "fact_invoice_reason", "fact_field_read", "fact_qr_read", "fact_daily_ops", "fact_baseline", "fact_cord_eval", "fact_choice", "fact_evaluation_summary", "fact_validation")
# plan, section 19: relationship id -> (dimension, fact, fact column)
RELATIONSHIPS = {
    "R01": ("dim_date", "fact_invoice", "date_key"), "R02": ("dim_date", "fact_daily_ops", "date_key"), "R03": ("dim_supplier", "fact_invoice", "supplier_key"),
    "R04": ("dim_reason", "fact_invoice", "first_reason_key"), "R05": ("dim_reason", "fact_invoice_reason", "reason_key"), "R06": ("dim_field", "fact_field_read", "field_key"),
    "R07": ("dim_cohort", "fact_invoice", "cohort_key"), "R08": ("dim_cohort", "fact_field_read", "cohort_key"), "R09": ("dim_cohort", "fact_qr_read", "cohort_key"),
    "R10": ("dim_cohort", "fact_baseline", "cohort_key"), "R11": ("dim_scan", "fact_invoice", "scan_key"), "R12": ("dim_scan", "fact_field_read", "scan_key"),
    "R13": ("dim_scan", "fact_qr_read", "scan_key"), "R14": ("dim_planted", "fact_invoice", "planted_key"), "R15": ("dim_baseline", "fact_baseline", "baseline_key"),
    "R16": ("dim_model", "fact_cord_eval", "model_key"), "R17": ("dim_pipeline_run", "fact_validation", "run_key")}


def read(out: Path, name: str):
    return pq.read_table(out / f"{name}.parquet")


def rows(db: Path, sql: str) -> list[tuple]:
    conn = connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_every_relationship_is_one_to_many_single_and_keys_exist():
    tables = {t.name: t for t in MODEL.tables}
    assert len(MODEL.relationships) == 17 and {r.rel_id: (r.dim_table, r.fact_table, r.fact_column) for r in MODEL.relationships} == RELATIONSHIPS
    for r in MODEL.relationships:
        dim, fact = tables[r.dim_table], tables[r.fact_table]
        assert (r.cardinality, r.cross_filter, r.active) == ("one_to_many", "single", True), r.rel_id
        assert dim.kind == "dimension" and fact.kind == "fact" and dim.key == (r.dim_column,), r.rel_id
        by_name = {c.name: c for c in fact.columns}
        assert r.fact_column in by_name and by_name[r.fact_column].dtype == next(c for c in dim.columns if c.name == r.dim_column).dtype, r.rel_id
        assert set(dim.key) <= set(dim.column_names) and set(fact.key) <= set(fact.column_names)
    assert validate_model(MODEL) == []


def test_the_model_is_the_plans_ten_dimensions_and_ten_facts_with_their_grain():
    assert [t.name for t in MODEL.tables] == [*DIMENSIONS, *FACTS]
    kinds = {t.name: t.kind for t in MODEL.tables}
    assert all(kinds[n] == "dimension" for n in DIMENSIONS) and all(kinds[n] == "fact" for n in FACTS)
    grain = {t.name: t.key for t in MODEL.tables}
    assert grain["fact_invoice"] == ("invoice_key", "seal_state") and grain["dim_supplier"] == ("supplier_key",) and grain["fact_daily_ops"] == ("date_key",)
    assert grain["fact_field_read"] == ("cohort_key", "field_key", "scan_key", "seal_state") and grain["fact_qr_read"] == ("cohort_key", "scan_key", "qr_cm", "reader", "seal_state")
    for t in MODEL.tables:
        assert all(c.description_es for c in t.columns) and t.description_es and len({c.name for c in t.columns}) == len(t.columns), t.name
        assert all(c.dtype in ARROW_TYPES for c in t.columns)
    assert {c.name for c in MODEL.table("fact_cord_eval").columns} >= {"model_key", "field", "ted_mean", "leaf_recall", "receipts", "split", "fit_splits", "seal_state"}
    assert {c.name for c in MODEL.table("fact_choice").columns} == {"epoch", "cord_val_ted", "val_known_line_exact", "val_new_line_exact", "kept", "chosen", "fit_splits"}


def test_validate_model_names_each_incoherence():
    def with_table(name, **changes):
        return dataclasses.replace(MODEL, tables=tuple(dataclasses.replace(t, **changes) if t.name == name else t for t in MODEL.tables))

    def with_relationship(index, **changes):
        rels = list(MODEL.relationships)
        rels[index] = dataclasses.replace(rels[index], **changes)
        return dataclasses.replace(MODEL, relationships=tuple(rels))

    cases = [(dataclasses.replace(MODEL, tables=(*MODEL.tables, MODEL.tables[0])), "repetidas"), (with_table("dim_supplier", key=("nope",)), "no es una columna"),
             (with_relationship(0, cardinality="many_to_many"), "uno a varios"), (with_relationship(0, cross_filter="both"), "unidireccional"),
             (with_relationship(0, active=False), "activa"), (with_relationship(0, fact_column="file_format"), "no es una columna del tipo"),
             (with_relationship(0, dim_column="date"), "clave entera"), (with_relationship(0, fact_table="dim_scan"), "no une una dimensión con un hecho")]
    for model, text in cases:
        assert any(text in p for p in validate_model(model)), (text, validate_model(model))
    assert validate_model(dataclasses.replace(MODEL, relationships=(*MODEL.relationships, Relationship("R99", "dim_date", "date_key", "fact_qr_read", "qr_cm")))) != []


def test_each_parquet_has_the_schema_of_the_specification_with_unique_keys_and_every_foreign_key_in_its_dimension(star_export):
    out = star_export.out
    assert sorted(p.name for p in out.glob("*.parquet")) == sorted(f"{t.name}.parquet" for t in MODEL.tables) and (out / STAR_MANIFEST).is_file() and verify_star_schema(out) == []
    data = {t.name: read(out, t.name) for t in MODEL.tables}
    for t in MODEL.tables:
        assert data[t.name].schema.remove_metadata() == arrow_schema(t), t.name
        keys = list(zip(*[data[t.name].column(k).to_pylist() for k in t.key], strict=True))
        assert len(keys) == len(set(keys)) and all(None not in k for k in keys), t.name
    for r in MODEL.relationships:
        dimension = set(data[r.dim_table].column(r.dim_column).to_pylist())
        values = data[r.fact_table].column(r.fact_column).to_pylist()
        assert set(values) <= dimension and None not in values, r.rel_id
    for name in (*DIMENSIONS, "fact_invoice", "fact_invoice_reason", "fact_field_read", "fact_qr_read", "fact_daily_ops", "fact_baseline", "fact_cord_eval", "fact_choice", "fact_evaluation_summary",
                 "fact_validation"):
        assert data[name].num_rows > 0, name


def test_the_facts_are_the_rows_of_the_warehouse_tables_they_are_made_from(star_export):
    db = star_export.db
    counts = {
        "fact_invoice": "SELECT COUNT(*) FROM eval_invoice WHERE seal_state = 'sealed'", "fact_invoice_reason": "SELECT COUNT(*) FROM decision_reasons", "fact_daily_ops": "SELECT COUNT(*) FROM daily_ops",
        "fact_evaluation_summary": "SELECT COUNT(*) FROM eval_criteria", "fact_validation": "SELECT COUNT(*) FROM validation_results", "fact_choice": "SELECT 2",
        "fact_baseline": "SELECT COUNT(*) FROM eval_baseline WHERE cohort != 'all'", "dim_cohort": "SELECT 4", "dim_scan": "SELECT 3", "dim_baseline": "SELECT 5", "dim_model": "SELECT 2",
        "dim_planted": "SELECT 14", "dim_reason": "SELECT 19", "dim_field": "SELECT 20", "dim_pipeline_run": "SELECT COUNT(*) FROM pipeline_runs"}
    for name, sql in counts.items():
        assert len(read(star_export.out, name)) == rows(db, sql)[0][0], name
    invoices = read(star_export.out, "fact_invoice").to_pandas()
    expected = rows(db, "SELECT SUM(posted), SUM(bad_post), SUM(straight_through), SUM(lines), SUM(line_exact) FROM eval_invoice WHERE seal_state = 'sealed'")[0]
    assert (int(invoices["posted"].sum()), int(invoices["bad_post"].sum()), int(invoices["straight_through"].sum()), int(invoices["lines"].sum()), int(invoices["lines_exact"].sum())) == expected
    ops = read(star_export.out, "fact_daily_ops").to_pandas()
    assert ops["invoices"].sum() == len(invoices) == ops["auto_post"].sum() + ops["review"].sum() and sorted(ops["date_key"]) == [20261001, 20261002, 20261005]
    calendar = read(star_export.out, "dim_date").to_pandas()
    assert calendar["date_key"].max() == 20261005 and calendar["date_key"].is_monotonic_increasing and set(calendar["weekday_es"]) <= {"lunes", "martes", "miércoles", "jueves", "viernes"}
    assert set(calendar["period"]) == {"history", "test"} and (calendar[calendar["date_key"] > 20260930]["period"] == "test").all(), "a day that had not closed at the run's instant is not in the marts"
    reads = read(star_export.out, "fact_qr_read").to_pandas()
    assert reads["printed"].sum() == rows(db, "SELECT SUM(qr_printed) FROM eval_invoice WHERE seal_state = 'sealed'")[0][0] and reads["read_ok"].sum() <= reads["printed"].sum()
    fields = read(star_export.out, "fact_field_read").to_pandas()
    assert fields["leaves"].sum() == rows(db, "SELECT COUNT(*) FROM extracted_fields")[0][0] and (fields["correct"] <= fields["leaves"]).all()


def test_the_files_carry_the_run_the_source_and_the_attribution_and_no_local_path_and_stay_under_the_size_limit(star_export):
    for path in star_export.paths:
        meta = {k.decode(): v.decode() for k, v in (pq.read_schema(path).metadata or {}).items()}
        assert meta["invoiceflow.run_key"] == "run-close" and meta["invoiceflow.as_of"] == "2026-10-06 00:00:00" and meta["invoiceflow.synthetic"] == "true"
        assert meta["invoiceflow.artifact_sha256"] == star_export.state.launch["artifact_sha256"]
        assert "CC BY 4.0" in meta["invoiceflow.attribution"] and "sintéticos" in meta["invoiceflow.attribution"]
        assert path.stat().st_size <= 25 * 1024 * 1024
        text = path.read_bytes()
        assert str(star_export.folder).encode() not in text and b"C:\\" not in text and b"/Users/" not in text
    manifest = json.loads((star_export.out / STAR_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["run_key"] == "run-close" and [f["name"] for f in manifest["files"]] == [f"{t.name}.parquet" for t in MODEL.tables]
    assert str(star_export.folder) not in json.dumps(manifest)


def test_the_evaluation_facts_say_which_seal_they_are_under_and_the_rest_is_the_flows(star_export):
    summary = read(star_export.out, "fact_evaluation_summary").to_pandas()
    assert set(summary["seal_state"]) == {"sealed"} and list(summary["criterion_id"]) == [f"C{n:02d}" for n in range(1, 11)] and summary["verdict_es"].str.len().gt(0).all()
    cord = read(star_export.out, "fact_cord_eval").to_pandas()
    assert set(cord["model_key"]) == {"chosen", "checkpoint"} and set(cord["field"]) == {"all"} and set(cord["split"]) == {"test"} and set(cord["receipts"]) == {4}
    fits = dict(zip(cord["model_key"], cord["fit_splits"], strict=True))
    assert fits == {"chosen": "cord_train+synth_train", "checkpoint": "checkpoint"}
    choice = read(star_export.out, "fact_choice").to_pandas()
    assert choice["chosen"].tolist() == [False, True] and set(choice["fit_splits"]) == {"cord_train+synth_train"}
    baseline = read(star_export.out, "fact_baseline").to_pandas()
    assert set(baseline["cohort_key"]) == {"test_known", "test_new"} and {"flow", "b0", "b1", "b3"} <= set(baseline["baseline_key"])
    dim_model = read(star_export.out, "dim_model").to_pandas()
    assert set(dim_model["seal_state"]) == {"sealed"} and dim_model.set_index("model_key").loc["chosen", "chosen_epoch"] == 2


def test_the_evaluation_fact_tells_every_criterion_as_the_published_summary_does(star_export):
    """N2 of the recheck (B1, A1): the Parquet file the DAX measure «Veredicto» reads carries the published sentence of each criterion (C08 per invoice), the sealed one beside it as
    ``sealed_verdict_es`` and the figure the threshold tests as ``threshold_figure_es``: the same texts as the summary, never a second copy of them."""
    published = {c["criterion_id"]: c for c in reporting.build_summary(star_export.conn, star_export.view, seal_path=star_export.state.seal)["criteria"]}
    facts = read(star_export.out, "fact_evaluation_summary").to_pandas().set_index("criterion_id")
    assert list(facts.index) == list(published) == [f"C{n:02d}" for n in range(1, 11)] and set(facts["seal_state"]) == {"sealed"}
    for cid, criterion in published.items():
        row = facts.loc[cid]
        assert row["verdict_es"] == criterion["verdict_es"], cid
        assert row["sealed_verdict_es"] == criterion.get("sealed_verdict_es", criterion["verdict_es"]), cid
        assert row["threshold_figure_es"] == texts.threshold_figure(criterion), cid
    c08 = facts.loc["C08"]
    assert c08["verdict_es"] != c08["sealed_verdict_es"] and "por factura" in c08["verdict_es"] and "Es la cifra sellada" in c08["verdict_es"] and "por factura" not in c08["sealed_verdict_es"]
    assert "puntos (límite inferior, por factura)" in c08["threshold_figure_es"] and facts.loc["C10", "threshold_figure_es"] == "sin umbral"


def test_a_control_with_rows_undoes_the_whole_rebuild_and_the_marts_keep_what_they_had(star_export, tmp_path):
    copy = tmp_path / "warehouse.sqlite"
    shutil.copy(star_export.db, copy)
    conn = connect(copy)
    before = conn.execute("SELECT COUNT(*) FROM star_fact_invoice").fetchone()[0]
    conn.execute("DROP TABLE star_dim_cohort")
    conn.execute("CREATE TABLE star_dim_cohort AS SELECT 'old' AS cohort_key, 'old' AS label_es")
    from invoiceflow import export

    original = export._load_references

    def corrupt(conn, view, seal_path, as_of=None):
        original(conn, view, seal_path, as_of)
        conn.execute("DELETE FROM ref_cohort WHERE cohort_key = 'test_new'")       # a fact row's key is no longer in its dimension

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(export, "_load_references", corrupt)
        with pytest.raises(CheckFailedError):
            export.build_powerbi_marts(conn, star_export.run, star_export.view, seal_path=star_export.state.seal)
    assert conn.execute("SELECT COUNT(*) FROM star_fact_invoice").fetchone()[0] == before and conn.execute("SELECT cohort_key FROM star_dim_cohort").fetchall() == [("old",)]
    assert not conn.in_transaction


def test_every_relationship_has_its_control_and_each_control_catches_a_missing_key(star_export, tmp_path):
    checks = sorted((warehouse.SQL_DIR / "checks" / "model").glob("chk_*.sql"))
    assert len(checks) >= 17 + 3
    copy = tmp_path / "warehouse.sqlite"
    shutil.copy(star_export.db, copy)
    conn = connect(copy)
    assert all(r.violations == 0 for r in warehouse.run_checks(conn, "model"))
    conn.execute("UPDATE star_fact_invoice SET cohort_key = 'no_such_cohort' WHERE rowid = 1")
    conn.execute("UPDATE star_fact_baseline SET baseline_key = 'b9' WHERE rowid = 1")
    failing = [r.check_id for r in warehouse.run_checks(conn, "model") if r.violations]
    assert any("R07" in name for name in failing) and any("R15" in name for name in failing) and len(failing) == 2


def _copy_of_the_warehouse(star_export, tmp_path) -> sqlite3.Connection:
    copy = tmp_path / "warehouse.sqlite"
    shutil.copy(star_export.db, copy)
    return connect(copy)


def test_a_leaf_the_model_wrote_that_did_not_normalize_is_not_counted_as_normalized(star_export, tmp_path):
    from invoiceflow.export import build_powerbi_marts

    conn = _copy_of_the_warehouse(star_export, tmp_path)
    before = conn.execute("SELECT SUM(correct) FROM star_fact_field_read").fetchone()[0]
    conn.execute("DROP TRIGGER extracted_fields_no_update")                       # in the copy only: the real table refuses an update
    conn.execute("UPDATE extracted_fields SET value_norm = NULL WHERE field = 'header.fecha' AND invoice_key IN (SELECT invoice_key FROM eval_invoice WHERE cohort = 'test_known' AND scan_profile = 'clean')")
    build_powerbi_marts(conn, star_export.run, star_export.view, seal_path=star_export.state.seal)
    damaged = conn.execute("SELECT leaves, correct, recall FROM star_fact_field_read WHERE field_key = 'header.fecha' AND cohort_key = 'test_known' AND scan_key = 'clean'").fetchone()
    assert damaged[0] > 0 and damaged[1] == 0 and damaged[2] == 0.0
    whole = conn.execute("SELECT SUM(leaves), SUM(correct) FROM star_fact_field_read").fetchone()
    assert whole[1] == before - damaged[0] and conn.execute("SELECT COUNT(*) FROM star_fact_field_read WHERE correct > leaves").fetchone()[0] == 0


def test_a_day_that_had_not_closed_at_the_runs_instant_is_not_in_the_calendar(star_export, tmp_path):
    """A run at the start of the 2nd has the 1st closed and not the 2nd: the warehouse of the session holds later days, so the control of the date relationship refuses the rebuild and names them."""
    from invoiceflow.export import build_powerbi_marts

    conn = _copy_of_the_warehouse(star_export, tmp_path)
    with pytest.raises(CheckFailedError, match="chk_R01_fact_invoice_date_key") as raised:
        build_powerbi_marts(conn, RunMeta("run-early", datetime(2026, 10, 2)), star_export.view, seal_path=star_export.state.seal)
    assert "20261002" in str(raised.value) and "20261001" not in str(raised.value)
    assert conn.execute("SELECT MAX(date_key) FROM star_dim_date").fetchone()[0] == 20261005, "the rebuild was undone: the marts keep what they had"


def test_a_null_key_is_a_violation_of_its_control(star_export, tmp_path):
    conn = _copy_of_the_warehouse(star_export, tmp_path)
    conn.execute("UPDATE star_fact_invoice SET cohort_key = NULL WHERE rowid = 2")
    failing = {r.check_id for r in warehouse.run_checks(conn, "model") if r.violations}
    assert failing == {"chk_R07_fact_invoice_cohort_key"}
