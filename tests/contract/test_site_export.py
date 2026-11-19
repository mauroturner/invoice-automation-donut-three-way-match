"""The five JSON files the personal site copies (U18; RF23, AC28; D38; plan, section 21; risk item 3: another repository consumes their shape). Every figure that comes from the synthetic
invoices says so, nothing of the test is published before the evaluation is sealed, the set is one export or it is refused, and the five stay under their cap."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from invoiceflow import site_export
from invoiceflow.registry import read_seal
from invoiceflow.site_export import MANIFEST_FILE, SITE_FILES, SiteExportError, SiteSetError, export_site, export_summary, read_site, verify_site

ENVELOPE = ("schema_version", "run_key", "as_of", "artifact_sha256", "cord_commit", "attribution", "synthetic_note_es")
TOP_KEYS = {
    "summary.json": {"status", "evaluation_status", "headline", "criteria", "seal", "sealed_from", "post_seal_analysis"},
    "data.json": {"cord", "synthetic", "qr_spec"},
    "algorithms.json": {"checkpoint_on_invoices", "choice", "fields", "qr", "match_rules", "baselines", "cord", "example_invoice"},
    "operation.json": {"days", "review_mix", "erp", "smoke_erpnext"},
    "validation_rules.json": {"rules", "totals"},
}


def documents(site) -> dict[str, dict]:
    return {"summary.json": site.summary, "data.json": site.data, "algorithms.json": site.algorithms, "operation.json": site.operation, "validation_rules.json": site.validation_rules}


def test_the_five_files_and_their_manifest_are_one_export_with_the_same_envelope_under_the_cap(site_files):
    out = site_files.out
    assert [p.name for p in site_files.paths] == list(SITE_FILES) and (out / MANIFEST_FILE).is_file() and verify_site(out) == []
    manifest = json.loads((out / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert sorted(manifest["files"]) == sorted(SITE_FILES) and sum(e["bytes"] for e in manifest["files"].values()) <= 204800
    for name, document in documents(site_files.site).items():
        assert {k: document[k] for k in ENVELOPE} == {k: manifest[k] for k in ENVELOPE}, name
        assert set(document) - set(ENVELOPE) == TOP_KEYS[name], name
        data = (out / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == manifest["files"][name]["sha256"] and data.endswith(b"\n") and b"\r" not in data
    assert manifest["schema_version"] == 1 and manifest["run_key"] == "run-close" and "CC BY 4.0" in manifest["attribution"] and "sintéticos" in manifest["synthetic_note_es"]
    assert manifest["cord_commit"] == site_files.state.view.source.cord_commit and manifest["artifact_sha256"] == site_files.state.launch["artifact_sha256"]


def _figures_without_label(node, path="$", labelled=False):
    """Every dict that holds a number and no ``synthetic`` mark (its own or an ancestor's), by path."""
    missing = []
    if isinstance(node, dict):
        mark = node.get("synthetic", None)
        here = labelled or mark is not None
        numbers = any(isinstance(v, (int, float)) and not isinstance(v, bool) and k != "synthetic" for k, v in node.items())
        if numbers and not here and "schema_version" not in node:
            missing.append(path)
        for key, value in node.items():
            missing += _figures_without_label(value, f"{path}.{key}", here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            missing += _figures_without_label(value, f"{path}[{i}]", labelled)
    return missing


def test_every_synthetic_figure_is_labelled(site_files):
    for name, document in documents(site_files.site).items():
        body = {k: v for k, v in document.items() if k not in ENVELOPE}
        assert _figures_without_label(body) == [], name
    summary = site_files.site.summary
    assert summary["headline"]["synthetic"] is True and all(c["synthetic"] in (True, False) for c in summary["criteria"]) and [c["criterion_id"] for c in summary["criteria"]] == [f"C{n:02d}" for n in range(1, 11)]
    assert site_files.site.algorithms["cord"]["synthetic"] is False and site_files.site.data["cord"]["synthetic"] is False and site_files.site.data["synthetic"]["synthetic"] is True
    # the walk sees an unlabelled figure
    assert _figures_without_label({"a": {"rate": 0.5}}) == ["$.a"] and _figures_without_label({"a": {"rate": 0.5, "synthetic": True}}) == [] and _figures_without_label({"a": {"synthetic": True, "b": {"n": 1}}}) == []


def test_the_headline_and_the_sections_are_the_figures_of_the_seal_and_the_star_model(site_files):
    summary, seal = site_files.site.summary, read_seal(site_files.state.seal)
    metrics = seal["test"]["metrics"]
    h = summary["headline"]
    assert summary["evaluation_status"] == "sealed" and summary["status"] == "running" and summary["sealed_from"] in ("warehouse", "seal") and h["days"] == 3
    analysis = json.loads(site_files.state.seal.with_name("post_seal_analysis.json").read_text(encoding="utf-8"))
    assert summary["post_seal_analysis"] == analysis and analysis["synthetic"] is True and analysis["sealed"] is False, "the analyses after the seal travel beside the sealed figures (D59)"
    c08 = next(c for c in summary["criteria"] if c["criterion_id"] == "C08")
    assert c08["sealed_verdict_es"] == next(c["verdict_es"] for c in seal["test"]["criteria"] if c["id"] == "C08") and "por factura" in c08["verdict_es"]
    assert h["straight_through_known"] == round(metrics["straight_through"]["known"]["rate"], 3) and h["line_exact_known"] == round(metrics["lines"]["known"]["rate"], 3)
    assert h["bad_post_rate"] == {"rate": round(metrics["bad_post"]["rate"], 3), "ci_high": round(metrics["bad_post"]["ci_high"], 3)} and h["qr_read"] == round(metrics["qr"]["rate"], 3)
    ops = site_files.site.operation
    assert [d["date"] for d in ops["days"]] == ["2026-10-01", "2026-10-02", "2026-10-05"] and h["invoices"] == sum(d["invoices"] for d in ops["days"]) == h["auto_post"] + h["review"]
    for day in ops["days"]:
        assert sum(day["by_reason"].values()) == day["review"] and day["seconds_mean"] is not None
    assert sum(sum(r["by_reason"].values()) for r in ops["review_mix"]) == h["review"] and ops["erp"]["posted"] + ops["erp"]["already_posted"] == h["auto_post"] - ops["erp"]["post_failed"]
    assert ops["erp"]["readback_equal"] == ops["erp"]["posted"] and ops["smoke_erpnext"] is None
    algorithms = site_files.site.algorithms
    assert [r["reason"] for r in algorithms["match_rules"]][0] == "file_rejected" and len(algorithms["match_rules"]) == 18 and {b["baseline"] for b in algorithms["baselines"]} >= {"flow", "b0", "b1", "b3"}
    assert [c["epoch"] for c in algorithms["choice"]] == [1, 2] and [c["chosen"] for c in algorithms["choice"]] == [False, True] and all(c["fit_splits"] for c in algorithms["choice"])
    assert algorithms["cord"]["receipts"] == 4 and "D42" in algorithms["cord"]["note_es"] or "checkpoint" in algorithms["cord"]["note_es"]


def test_the_example_invoice_follows_its_fixed_rule_and_carries_values_and_boxes_but_no_image(site_files):
    example = site_files.site.algorithms["example_invoice"]
    assert example["synthetic"] is True and example["split"] == "test_known" and example["planted"] == "none" and example["lines"] == 3
    assert example["texts"] and all(set(t) == {"text", "box"} and len(t["box"]) == 4 and all(0.0 <= v <= 1.0 for v in t["box"]) for t in example["texts"])
    assert not any(k in json.dumps(site_files.site.algorithms) for k in ("base64", "image/", "data:image"))
    truths = [t for t in site_files.state.world.all_truths() if t.split == "test_known" and t.planted == "none" and len(t.lines) == 3]
    assert example["invoice_key"] == sorted(truths, key=lambda t: t.invoice_key)[0].invoice_key


def test_the_data_file_tells_cord_and_the_synthetic_invoices_and_the_thirteen_fields_of_the_qr(site_files):
    data = site_files.site.data
    cord = data["cord"]
    assert cord["receipts"] == {"train": 16, "validation": 4, "test": 4} and cord["menu_forms"]["list"] + cord["menu_forms"]["object"] == 20 and cord["lines_median"] > 0
    assert cord["fields_present"]["menu"] <= 1.0 and cord["missing_fields_es"] == ["proveedor", "CUIT", "fecha", "punto de venta", "número", "moneda"] and cord["scope_es"]
    synthetic = data["synthetic"]
    assert synthetic["suppliers"]["known"] > 0 and sum(synthetic["planted"][i]["count"] for i in range(len(synthetic["planted"]))) == sum(1 for t in site_files.state.world.all_truths() if t.planted != "none")
    assert "30-0000" in synthetic["cuit_range_es"] and len(data["qr_spec"]) == 13 and [f["field"] for f in data["qr_spec"]][:3] == ["ver", "fecha", "cuit"]
    assert {f["field"]: f["required"] for f in data["qr_spec"]}["tipoDocRec"] is False and {f["field"]: f["required"] for f in data["qr_spec"]}["cuit"] is True


def test_before_the_seal_nothing_of_the_test_is_published_and_the_days_already_run_are_(before_the_seal, tmp_path):
    conn, seal, state = before_the_seal
    out = tmp_path / "site"
    export_site(conn, out, state.run, state.view, seal_path=seal, max_total_bytes=204800)
    site = read_site(out)
    assert site.summary["evaluation_status"] == "pending" and site.summary["sealed_from"] is None and site.summary["seal"]["test"] is None
    h = site.summary["headline"]
    assert h["invoices"] == 18 and h["days"] == 3 and all(h[k] is None for k in ("straight_through_known", "straight_through_new", "bad_post_rate", "line_exact_known", "line_exact_new", "qr_read"))
    assert all(c["value"] is None and c["passed"] in (False, None) and "se evalúa" in c["verdict_es"] for c in site.summary["criteria"])
    a = site.algorithms
    assert a["baselines"] == [] and a["fields"] == [] and a["qr"] == [] and a["cord"] is None and a["example_invoice"] is None and a["checkpoint_on_invoices"] is None
    assert [c["epoch"] for c in a["choice"]] == [1, 2], "the launch figures are validation, not the test"
    assert site.operation["review_mix"] == [] and len(site.operation["days"]) == 3 and site.data["synthetic"]["planted"] == []
    assert "test_known" not in json.dumps(site.operation["review_mix"]) and verify_site(out) == []


def test_a_failure_in_the_middle_of_the_writes_leaves_a_set_that_is_refused(site_files, tmp_path, monkeypatch):
    out = tmp_path / "site"
    state = site_files.state
    export_site(state.conn, out, state.run, state.view, seal_path=state.seal, max_total_bytes=204800)
    assert verify_site(out) == []
    calls = []
    real = site_export.write_atomic

    def failing(path, data):
        calls.append(path.name)
        if len(calls) == 3:
            raise OSError("disk full")
        return real(path, data)

    monkeypatch.setattr(site_export, "write_atomic", failing)
    with pytest.raises(OSError, match="disk full"):
        export_site(state.conn, out, state.run, state.view, seal_path=state.seal, max_total_bytes=204800)
    assert not (out / MANIFEST_FILE).exists() and verify_site(out) != []
    with pytest.raises(SiteSetError, match="manifest"):
        read_site(out)


def test_a_mixed_set_a_changed_file_and_a_foreign_envelope_are_refused(site_files, tmp_path):
    folder = tmp_path / "set"
    shutil.copytree(site_files.out, folder)
    assert verify_site(folder) == []
    original = (folder / "operation.json").read_bytes()
    (folder / "operation.json").write_bytes(original.replace(b'"days"', b'"dayz"', 1))
    assert any("operation.json" in p and "SHA-256" in p for p in verify_site(folder))
    (folder / "operation.json").write_bytes(original)
    document = json.loads((folder / "data.json").read_text(encoding="utf-8"))
    document["run_key"] = "run-other"
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest["files"]["data.json"] = {"bytes": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}
    (folder / "data.json").write_text(text, encoding="utf-8", newline="")
    (folder / MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="")
    assert any("data.json" in p and "envoltorio" in p for p in verify_site(folder))
    (folder / "validation_rules.json").unlink()
    assert any("falta validation_rules.json" in p for p in verify_site(folder))
    with pytest.raises(SiteSetError):
        read_site(folder)


def test_a_total_over_the_cap_writes_nothing_and_a_warehouse_without_the_star_model_is_refused(site_files, tmp_path):
    state = site_files.state
    with pytest.raises(SiteExportError, match="el tope es 100"):
        export_site(state.conn, tmp_path / "small", state.run, state.view, seal_path=state.seal, max_total_bytes=100)
    assert not (tmp_path / "small").exists() or not list((tmp_path / "small").iterdir())
    from invoiceflow.warehouse import connect

    bare = connect(tmp_path / "bare.sqlite")
    with pytest.raises(SiteExportError, match="modelo estrella"):
        export_site(bare, tmp_path / "bare", state.run, state.view, seal_path=state.seal, max_total_bytes=204800)
    names = {n for n in site_export.REQUIRED_STAR}
    import re
    source = (site_export.__file__ and open(site_export.__file__, encoding="utf-8").read())
    assert names == set(re.findall(r"\bstar_[a-z_]+\b", source)) - {"star_", "star_schema"}, "the one list that is checked names every table the queries read"


def test_cumulative_rules_are_read_once_from_the_latest_loaded_run(site_files):
    rules = {r["rule_id"]: r for r in site_files.site.validation_rules["rules"]}
    state = site_files.state
    per_run = state.conn.execute("SELECT COALESCE(SUM(files_affected), 0) FROM validation_results WHERE rule_id = 'V14'").fetchone()[0]
    assert rules["V14"]["aggregation"] == "per_run" and rules["V14"]["files"] == per_run and rules["V10"]["status"] == "pass"
    assert [r["rule_id"] for r in site_files.site.validation_rules["rules"]] == sorted(rules, key=lambda r: ("VIM".index(r[0]), int(r[1:])))
    totals = site_files.site.validation_rules["totals"]
    assert totals["invoices"] == 18 and totals["deliveries_loaded"] == 3 and totals["deliveries_rejected"] == 0
    # a rule that counts something cumulative is not summed over the runs
    table = site_export._aggregated  # noqa: SLF001
    import pandas as pd

    rows = pd.DataFrame([{"rule_id": "V01", "aggregation": "source", "as_of": "2026-10-01", "files_affected": 3, "passed": 1, "severity": "bloqueante"},
                         {"rule_id": "V01", "aggregation": "source", "as_of": "2026-10-02", "files_affected": 3, "passed": 1, "severity": "bloqueante"},
                         {"rule_id": "V14", "aggregation": "per_run", "as_of": "2026-10-01", "files_affected": 1, "passed": 0, "severity": "advertencia"},
                         {"rule_id": "V14", "aggregation": "per_run", "as_of": "2026-10-02", "files_affected": 1, "passed": 0, "severity": "advertencia"}])
    assert table(rows, "V01")["files_affected"].sum() == 3 and table(rows, "V14")["files_affected"].sum() == 2


def test_the_summary_alone_is_rewritten_with_the_manifest_and_only_over_one_export_of_the_same_run(site_files, tmp_path):
    state = site_files.state
    folder = tmp_path / "set"
    shutil.copytree(site_files.out, folder)
    before = {n: (folder / n).read_bytes() for n in SITE_FILES}
    path = export_summary(state.conn, folder, state.run, state.view, seal_path=state.seal, max_total_bytes=204800)
    assert path.name == "summary.json" and verify_site(folder) == [] and {n: (folder / n).read_bytes() for n in SITE_FILES} == before
    (folder / "data.json").write_bytes(b"{}\n")
    snapshot = {p.name: p.read_bytes() for p in folder.iterdir()}
    with pytest.raises(SiteExportError, match="no se reescribe el resumen"):
        export_summary(state.conn, folder, state.run, state.view, seal_path=state.seal, max_total_bytes=204800)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == snapshot
    from invoiceflow.registry import RunMeta

    other = tmp_path / "other"
    shutil.copytree(site_files.out, other)
    with pytest.raises(SiteExportError, match="otra corrida"):
        export_summary(state.conn, other, RunMeta("run-x", state.run.as_of), state.view, seal_path=state.seal, max_total_bytes=204800)


def test_what_the_data_file_says_about_cord_is_worked_out_by_hand():
    from invoiceflow.cord import CordIndex, IndexEntry

    def entry(key, menu, total, *, obj=False, in_list=False, dup=None):
        return IndexEntry(key, "h" + key, 10, 10, {"menu": menu, "sub_total": {"subtotal_price": "1"}, **({"total": total} if total is not None else {})}, obj, in_list, dup)

    index = CordIndex({"train": [entry("a", [1, 2, 3], {"total_price": "12.000"}), entry("b", [1], {"total_price": "x?"}, obj=True), entry("c", [1, 2], None, dup="a")],
                       "validation": [entry("d", [], {"total_price": "5.000"}, in_list=True)]}, {}, "c" * 40)
    facts = site_export.cord_facts(index, {"train": 800, "validation": 100, "test": 100})
    assert facts["receipts"] == {"train": 800, "validation": 100, "test": 100} and facts["unique_images"] == 3 and facts["menu_forms"] == {"list": 3, "object": 1}
    assert facts["lines_median"] == 1.5 and facts["totals"] == {"readable": 2, "missing": 1, "unreadable": 1}
    assert facts["fields_present"] == {"menu": 0.75, "sub_total": 1.0, "total": 0.75} and facts["synthetic"] is False
    bare = site_export.cord_facts(None)
    assert bare["receipts"] == {"train": None, "validation": None, "test": None} and bare["unique_images"] is None and bare["totals"]["readable"] is None
    assert site_export.cord_facts(index)["receipts"] == {"train": 3, "validation": 1, "test": None}, "without the configured counts the index counts what it holds and the test split is not guessed"


def test_a_run_under_another_configuration_than_the_sealed_one_is_post_seal_and_publishes_nothing_of_the_test(site_files, tmp_path):
    import dataclasses

    state = site_files.state
    other = dataclasses.replace(state.view, model=dataclasses.replace(state.view.model, learning_rate=state.view.model.learning_rate * 2))
    export_site(state.conn, tmp_path / "site", state.run, other, seal_path=state.seal, max_total_bytes=204800)
    site = read_site(tmp_path / "site")
    assert site.summary["evaluation_status"] == "post_seal" and site.algorithms["example_invoice"] is None and site.algorithms["baselines"] == [] and site.algorithms["cord"] is None
    assert site.operation["review_mix"] == [] and site.data["synthetic"]["planted"] == [] and site.summary["headline"]["bad_post_rate"] is None and site.summary["headline"]["qr_read"] is None


def test_the_example_invoice_is_the_first_clean_one_with_three_lines_by_key_whatever_the_generator_gives_first(site_files, monkeypatch):
    import dataclasses

    state = site_files.state
    base = next(t for t in state.world.all_truths() if t.split == "test_known" and len(t.lines) >= 3)
    lines3, lines2 = base.lines[:3], base.lines[:2]
    made = [dataclasses.replace(base, invoice_key="test_known-9", planted="none", lines=lines3), dataclasses.replace(base, invoice_key="test_known-3", planted="price_off", lines=lines3),
            dataclasses.replace(base, invoice_key="test_known-5", planted="none", lines=lines2), dataclasses.replace(base, invoice_key="test_known-4", planted="none", lines=lines3)]
    monkeypatch.setattr(site_export, "invoices", lambda *a, **k: made)
    example = site_export._example_invoice(state.view, state.seal)
    assert example["invoice_key"] == "test_known-4" and example["lines"] == 3
    monkeypatch.setattr(site_export, "invoices", lambda *a, **k: made[1:3])
    assert site_export._example_invoice(state.view, state.seal) is None


def test_the_cap_is_a_boundary_the_total_itself_fits_and_one_byte_less_does_not(site_files, tmp_path):
    state = site_files.state
    total = sum(p.stat().st_size for p in site_files.paths)
    export_site(state.conn, tmp_path / "exact", state.run, state.view, seal_path=state.seal, max_total_bytes=total, cord_index=site_files.cord_index,
                expected_rows={"train": 16, "validation": 4, "test": 4})
    assert verify_site(tmp_path / "exact") == []
    with pytest.raises(SiteExportError, match="el tope es"):
        export_site(state.conn, tmp_path / "short", state.run, state.view, seal_path=state.seal, max_total_bytes=total - 1, cord_index=site_files.cord_index, expected_rows={"train": 16, "validation": 4, "test": 4})
    assert not (tmp_path / "short").exists() or not list((tmp_path / "short").iterdir())


SMOKE = {"synthetic": True, "image": "frappe/erpnext:v15.124.4", "image_digest": "sha256:" + "a" * 64, "erpnext_version": "15.99.0", "frappe_version": "15.99.0", "tested_on": "2026-11-18",
         "documents": {"suppliers": 1, "items": 2, "orders": 1, "receipts": 1, "invoices": 2}, "readback_equal": True, "repost_creates_none": True,
         "duplicate_bill_no": {"default_settings_creates_a_duplicate": True, "status_with_uniqueness_on": 417}}


def test_the_smoke_result_of_erpnext_is_read_from_its_file_strictly_and_is_none_without_it(tmp_path):
    """U22 (AC36; plan section 21): ``operation.smoke_erpnext`` is the result the smoke test left in ``reports/erpnext_smoke.json``; no file is ``null``; a file that is not exactly that shape is refused by
    name, never published half-read."""
    path = tmp_path / "erpnext_smoke.json"
    assert site_export.read_smoke(path) is None
    path.write_text(json.dumps(SMOKE), encoding="utf-8")
    assert site_export.read_smoke(path) == SMOKE
    for broken in ("{not json", "[]", json.dumps({**SMOKE, "extra": 1}), json.dumps({k: v for k, v in SMOKE.items() if k != "image"}), json.dumps({**SMOKE, "synthetic": False}),
                   json.dumps({**SMOKE, "image_digest": "sha256:short"}), json.dumps({**SMOKE, "image": "C:/Users/someone/erpnext"})):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(SiteExportError, match="erpnext_smoke"):
            site_export.read_smoke(path)


def test_the_site_carries_the_smoke_result_it_is_given(star_export, tmp_path):
    """The five files say what the smoke test found, labelled synthetic like every figure that is not CORD's; without it the field stays null (``test_the_five_files...`` checks the default)."""
    out = tmp_path / "site"
    state = star_export.state
    export_site(state.conn, out, state.run, state.view, seal_path=state.seal, max_total_bytes=204800, smoke_erpnext=SMOKE)
    assert read_site(out).operation["smoke_erpnext"] == SMOKE and verify_site(out) == []
