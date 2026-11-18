"""The writing of the star model's files (U17; plan, section 19; RNF05, RNF06): each file is serialized and measured before any is written, the manifest is last, and a set that is not whole is refused."""

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import datetime

import pytest

from invoiceflow import export
from invoiceflow.dashboard.semantic_model import MODEL
from invoiceflow.export import STAR_MANIFEST, export_run_table, export_star_schema, verify_star_schema
from invoiceflow.registry import RunMeta


def test_the_manifest_lists_every_file_with_its_bytes_and_hash_and_a_set_with_a_changed_file_is_refused(star_export, tmp_path):
    folder = tmp_path / "set"
    shutil.copytree(star_export.out, folder)
    assert verify_star_schema(folder) == []
    target = folder / "fact_invoice.parquet"
    target.write_bytes(target.read_bytes() + b"x")
    assert any("fact_invoice.parquet" in p and "manifiesto" in p for p in verify_star_schema(folder))
    original = (star_export.out / "fact_invoice.parquet").read_bytes()
    target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))                  # the same size and other content
    assert any("fact_invoice.parquet" in p and "manifiesto" in p for p in verify_star_schema(folder))
    target.write_bytes(original)
    (folder / "dim_scan.parquet").unlink()
    assert any("dim_scan.parquet" in p and "falta" in p for p in verify_star_schema(folder))
    (folder / "stray.parquet").write_bytes(b"x")
    assert any("stray.parquet" in p for p in verify_star_schema(folder))
    (folder / STAR_MANIFEST).unlink()
    assert any("no hay un manifiesto" in p for p in verify_star_schema(folder))


def test_a_failure_in_the_middle_of_the_writes_leaves_a_set_without_a_manifest(star_export, tmp_path, monkeypatch):
    out = tmp_path / "powerbi"
    export_star_schema(star_export.conn, out, MODEL, star_export.run, star_export.view, seal_path=star_export.state.seal)
    assert verify_star_schema(out) == []
    calls = []
    real = export.write_atomic

    def failing(path, data):
        calls.append(path.name)
        if len(calls) == 4:
            raise OSError("disk full")
        return real(path, data)

    monkeypatch.setattr(export, "write_atomic", failing)
    with pytest.raises(OSError, match="disk full"):
        export_star_schema(star_export.conn, out, MODEL, star_export.run, star_export.view, seal_path=star_export.state.seal)
    assert not (out / STAR_MANIFEST).exists() and verify_star_schema(out) != []


def test_a_file_over_the_limit_is_refused_before_anything_is_written(star_export, tmp_path, monkeypatch):
    out = tmp_path / "powerbi"
    monkeypatch.setattr(export, "MAX_PARQUET_BYTES", 100)
    with pytest.raises(ValueError, match="no se escribe nada"):
        export_star_schema(star_export.conn, out, MODEL, star_export.run, star_export.view, seal_path=star_export.state.seal)
    assert not out.exists() or not list(out.iterdir())


def test_an_incoherent_model_writes_nothing(star_export, tmp_path):
    broken = dataclasses.replace(MODEL, relationships=(dataclasses.replace(MODEL.relationships[0], cardinality="many_to_many"), *MODEL.relationships[1:]))
    with pytest.raises(ValueError, match="incoherente"):
        export_star_schema(star_export.conn, tmp_path / "powerbi", broken, star_export.run, star_export.view, seal_path=star_export.state.seal)
    assert not (tmp_path / "powerbi").exists()


def test_the_run_table_alone_is_rewritten_with_its_line_of_the_manifest_and_nothing_else_changes(star_export, tmp_path):
    out = tmp_path / "powerbi"
    shutil.copytree(star_export.out, out)
    before = {p.name: p.read_bytes() for p in out.glob("*.parquet")}
    star_export.conn.execute("BEGIN IMMEDIATE")
    star_export.conn.execute("UPDATE pipeline_runs SET message = 'otra cosa' WHERE run_key = (SELECT MIN(run_key) FROM pipeline_runs)")
    star_export.conn.execute("COMMIT")
    try:
        path = export_run_table(star_export.conn, out, MODEL, star_export.run, star_export.view)
        assert path is not None and path.name == "dim_pipeline_run.parquet" and verify_star_schema(out) == []
        after = {p.name: p.read_bytes() for p in out.glob("*.parquet")}
        assert [n for n in after if after[n] != before[n]] == ["dim_pipeline_run.parquet"]
    finally:
        star_export.conn.execute("UPDATE pipeline_runs SET message = NULL WHERE message = 'otra cosa'")


def test_the_run_table_is_not_rewritten_into_a_set_that_does_not_match_its_manifest(star_export, tmp_path, caplog):
    out = tmp_path / "powerbi"
    shutil.copytree(star_export.out, out)
    (out / "dim_scan.parquet").write_bytes(b"x")
    snapshot = {p.name: p.read_bytes() for p in out.iterdir()}
    assert export_run_table(star_export.conn, out, MODEL, star_export.run, star_export.view) is None
    assert {p.name: p.read_bytes() for p in out.iterdir()} == snapshot and "run-close" in caplog.text
    assert export_run_table(star_export.conn, tmp_path / "nothing", MODEL, star_export.run, star_export.view) is None and not (tmp_path / "nothing").exists()


def test_the_marts_are_rebuilt_only_inside_a_transaction_and_only_for_a_known_group(tmp_path):
    from invoiceflow import warehouse

    conn = warehouse.connect(tmp_path / "w.sqlite")
    with pytest.raises(ValueError, match="grupo de marts desconocido"):
        warehouse.rebuild_marts(conn, "tableau")
    with pytest.raises(RuntimeError, match="transacción abierta"):
        warehouse.rebuild_marts(conn, "powerbi")
    assert warehouse.CHECK_GROUPS == ("load", "model") and warehouse.MART_GROUPS == ("powerbi",)
