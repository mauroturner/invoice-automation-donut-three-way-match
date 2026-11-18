"""The HTML design of the report (U17; RF22, AC27; plan, section 20): every page and measure of the model, no external resource, every text of the data escaped, the figures of the previews the ones
of the exported files, every page says that the data are synthetic except CORD's, and an incoherent model that writes nothing."""

from __future__ import annotations

import dataclasses
import re
import shutil

import pandas as pd
import pytest

from invoiceflow.dashboard.render import render_design
from invoiceflow.dashboard.semantic_model import MODEL, PAGES
from invoiceflow.registry import RunMeta
from tests.unit.test_semantic_model import MEASURE_NAMES, PAGE_NAMES


def test_html_has_every_page_and_escapes_data_text(star_export, rendered_html, tmp_path):
    html = rendered_html.html
    assert html.startswith("<!doctype html>") and '<html lang="es">' in html and len(html.encode("utf-8")) < 2 * 1024 * 1024
    for number, name in enumerate(PAGE_NAMES, start=1):
        assert f'id="pagina-{number}">Página {number}: {name}</h3>' in html
    for page in PAGES:
        for v in page.visuals:
            assert f'aria-label="{v.title_es}"' in html.replace("&quot;", '"') or v.title_es.split(" (no lo")[0] in html, v.title_es
    for name in MEASURE_NAMES:
        assert f"<h3>{name}</h3>" in html.replace("&amp;", "&") or name in html, name
    assert html.count('class="visual"') == sum(len(p.visuals) for p in PAGES) and '<h2 id="relaciones">' in html and html.count("<tr><td>R") >= 17
    assert all(f"<h3>{t.name}</h3>" in html for t in MODEL.tables) and "Ninguna medida se ejecutó" in html
    # text that comes from the data is escaped wherever it is shown
    tainted = tmp_path / "powerbi"
    shutil.copytree(star_export.out, tainted)
    suppliers = pd.read_parquet(tainted / "dim_supplier.parquet")
    suppliers["name_es"] = '<script>alert("x")</script> & más'
    suppliers.to_parquet(tainted / "dim_supplier.parquet")
    runs = pd.read_parquet(tainted / "dim_pipeline_run.parquet")
    runs["message"] = "<img src=x onerror=alert(1)>"
    runs.to_parquet(tainted / "dim_pipeline_run.parquet")
    other = render_design(MODEL, tainted, tmp_path / "other.html", star_export.run).read_text(encoding="utf-8")
    assert "<script>alert" not in other and "<img src=x" not in other and "&lt;img src=x onerror=alert(1)&gt;" in other


def test_the_page_loads_nothing_from_outside_and_has_no_script(rendered_html):
    html = rendered_html.html
    assert "<script" not in html.lower() and "<iframe" not in html.lower() and "@import" not in html and not re.findall(r"url\((?!#)", html)
    assert not re.findall(r'(?:src|href)=["\'](?!#)', html) and "http://" not in html and "https://www" not in html.replace("https://creativecommons.org", "")
    assert str(rendered_html.path.parent) not in html and "C:\\" not in html and "/Users/" not in html


def test_every_page_says_that_the_data_are_synthetic_except_cords_and_the_attribution_is_there(rendered_html):
    html = rendered_html.html
    assert html.count("Datos sintéticos salvo CORD") >= len(PAGES) and "CC BY 4.0" in html and "commit 7f0115a4" in html
    assert "no es el tiempo de una persona" in html or "no es el tiempo de una persona".lower() in html.lower()


def test_the_previews_are_the_figures_of_the_exported_files(star_export, rendered_html):
    html = rendered_html.html
    ops = pd.read_parquet(star_export.out / "fact_daily_ops.parquet")
    invoices = pd.read_parquet(star_export.out / "fact_invoice.parquet")
    assert f'<div class="big">{int(ops["invoices"].sum())}</div>' in html and f'<div class="big">{int(ops["auto_post"].sum())}</div>' in html
    clean = invoices[invoices["planted_key"] == "none"]
    shown = f"{clean['straight_through'].sum() / len(clean) * 100:.1f} %".replace(".", ",")
    assert f'<div class="big">{shown}</div>' in html
    criteria = pd.read_parquet(star_export.out / "fact_evaluation_summary.parquet")
    assert all(verdict.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") in html for verdict in criteria[criteria["seal_state"] == "sealed"]["verdict_es"])
    assert "La prueba se evalúa" not in html


def test_without_the_exported_files_every_preview_says_there_is_no_data_and_the_criteria_are_pending(star_export, tmp_path):
    out = render_design(MODEL, tmp_path / "nothing", tmp_path / "empty.html", RunMeta("run-x", star_export.run.as_of)).read_text(encoding="utf-8")
    assert "Sin datos" in out and "pendientes" in out and out.count('class="visual"') == sum(len(p.visuals) for p in PAGES) and "<script" not in out


def test_an_incoherent_model_writes_nothing_and_the_file_is_written_whole(star_export, tmp_path):
    broken = dataclasses.replace(MODEL, relationships=MODEL.relationships[1:] + (dataclasses.replace(MODEL.relationships[0], cardinality="many_to_many"),))
    target = tmp_path / "design.html"
    with pytest.raises(ValueError, match="incoherente"):
        render_design(broken, star_export.out, target, star_export.run)
    assert not target.exists() and not list(tmp_path.glob("*.tmp"))
    render_design(MODEL, star_export.out, target, star_export.run)
    before = target.read_bytes()
    render_design(MODEL, star_export.out, target, star_export.run)
    assert target.read_bytes() == before and not list(tmp_path.glob("*.tmp"))


def test_every_visual_of_every_page_gets_its_preview_drawn_from_the_exported_files(star_export):
    from invoiceflow.dashboard.render import _Data, _note, _preview

    data = _Data(star_export.out, MODEL)
    undrawn = _note("Se calcula en Power BI.")
    missing = [v.title_es for page in PAGES for v in page.visuals if _preview(data, v) == undrawn]
    assert missing == [], "a visual whose title carries the not-filtered note must still find its preview by the title it had without it"


def test_every_maker_key_is_the_bare_title_of_a_visual_and_every_visual_has_a_maker(star_export):
    from invoiceflow.dashboard.render import _Data, _makers

    data = _Data(star_export.out, MODEL)
    visuals = [v for page in PAGES for v in page.visuals if v.kind not in ("slicer", "card")]
    assert set(_makers(data, visuals[0])) == {v.title_es.split(" (no lo")[0] for v in visuals}


def test_the_mark_that_cuts_the_not_filtered_note_is_derived_from_the_note_itself():
    from invoiceflow.dashboard import render
    from invoiceflow.dashboard.semantic_model import NOT_FILTERED

    assert render.NOT_FILTERED_MARK == NOT_FILTERED[:NOT_FILTERED.index("{")] == " (no lo filtra"
    for title in ("Facturas" + NOT_FILTERED.format(plural="", names="la cohorte"), "Pérdida" + NOT_FILTERED.format(plural="n", names="la fecha y el grupo")):
        assert render._bare_title(title) == title.split(" (no lo")[0]


def test_a_row_that_is_not_sealed_is_never_shown_as_a_figure_of_the_test(star_export, tmp_path):
    folder = tmp_path / "powerbi"
    shutil.copytree(star_export.out, folder)
    criteria = pd.read_parquet(folder / "fact_evaluation_summary.parquet")
    other = criteria.assign(seal_state="post_seal", verdict_es="POSTSEAL VERDICT", value=-123.0)
    pd.concat([criteria, other]).to_parquet(folder / "fact_evaluation_summary.parquet")
    cord = pd.read_parquet(folder / "fact_cord_eval.parquet")
    pd.concat([cord, cord.assign(seal_state="post_seal", fit_splits="POSTSEAL SPLITS")]).to_parquet(folder / "fact_cord_eval.parquet")
    html = render_design(MODEL, folder, tmp_path / "design.html", star_export.run).read_text(encoding="utf-8")
    assert "POSTSEAL" not in html and "-123" not in html and criteria["verdict_es"].iloc[0].replace("&", "&amp;") in html
