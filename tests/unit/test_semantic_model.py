"""The measures and the pages of the report (U17; RF21, RF22, AC26, AC27; D37; plan, section 20): no measure runs anywhere in the project, so what stands in for running them is that every
table, column, measure and function a DAX expression names exists, that a measure of the test reads only sealed rows, and that each slicer reaches the visuals of its page through a relationship
or the title of the visual says it does not filter it (risk items 3 and 6: no other test would catch a measure that reads the wrong rows)."""

from __future__ import annotations

import dataclasses
import re

from invoiceflow.dashboard.semantic_model import (
    DAX_FUNCTIONS,
    MEASURES,
    MODEL,
    PAGE_COLUMNS,
    PAGE_ROWS,
    PAGES,
    Page,
    SemanticModel,
    Visual,
    dax_column_refs,
    dax_measure_refs,
    validate_model,
    visual_tables,
)

PAGE_NAMES = ("La operación, día por día", "Excepciones por motivo", "Calidad de lectura", "La prueba", "Calidad de datos y corridas")
MEASURE_NAMES = (
    "Facturas", "Cargadas solas", "A revisión", "Facturas de la prueba", "Facturas a revisión de la prueba", "Paso directo (%)", "Cargas malas", "Tasa de carga mala", "Líneas exactas (%)",
    "Segundos por factura", "Segundos p95 del día", "QR leídos (%)", "Recuerdo del campo", "Carga mala de la línea base", "TED de CORD", "Veredicto", "Última fecha de cálculo",
    "Reglas bloqueantes fallidas")
SEALED_FACTS = [t.name for t in MODEL.tables if "seal_state" in t.column_names]
PICKS = ("SELECTEDVALUE", "COUNTROWS", "DISTINCTCOUNT", "SUM", "AVERAGE", "SUMX")


def test_the_measures_of_the_plan_exist_with_their_names_formats_and_descriptions():
    assert tuple(m.name_es for m in MEASURES) == MEASURE_NAMES and len(MODEL.measures) == 18
    assert all(m.format_string and m.description_es and m.dax.count("(") == m.dax.count(")") for m in MEASURES)
    assert MODEL.measure("Veredicto").format_string == "@" and MODEL.measure("Última fecha de cálculo").format_string == "yyyy-mm-dd hh:mm"
    assert validate_model(MODEL) == []


def test_every_dax_reference_exists():
    columns = {t.name: {c.name: c for c in t.columns} for t in MODEL.tables}
    names = {m.name_es for m in MODEL.measures}
    for m in MODEL.measures:
        for table, column in dax_column_refs(m.dax):
            assert column in columns.get(table, {}), (m.name_es, table, column)
        for ref in dax_measure_refs(m.dax):
            assert ref in names and ref != m.name_es, (m.name_es, ref)
        for table in re.findall(r"\b(?:dim|fact)_\w+", m.dax):
            assert table in columns, (m.name_es, table)
        for function in re.findall(r"\b([A-Z][A-Z0-9.]*)(?=\s*\()", m.dax):
            assert function in DAX_FUNCTIONS, (m.name_es, function)
        for table, column, literal in re.findall(r'\b([a-z_]\w*)\[(\w+)\]\s*=\s*"([^"]*)"', m.dax):
            assert literal in columns[table][column].description_es, (m.name_es, table, column, literal)       # the text a column is compared with is one the column takes
        assert not re.search(r"\b(ALL|ALLEXCEPT|ALLSELECTED|REMOVEFILTERS|CROSSFILTER|USERELATIONSHIP|KEEPFILTERS)\b", m.dax), m.name_es       # no measure removes the slicers' filters
    for page in PAGES:
        for v in page.visuals:
            for field in v.fields:
                if field.startswith("["):
                    assert field[1:-1] in names, (page.name_es, v.title_es, field)
                else:
                    table, column = field.split("[")[0], field.split("[")[1].rstrip("]")
                    assert column in columns[table], (page.name_es, v.title_es, field)


def test_every_test_measure_reads_only_sealed_rows():
    """A measure that picks, sums or counts the rows of a table with a seal state would count a sealed and a post-seal row of the same key twice, or be blank when it picks one (project 04's
    D71): it asks for the sealed rows. The measures of the follow-up (``fact_daily_ops``, ``dim_pipeline_run``, ``fact_validation``) have no seal state and are not asked for it."""
    for m in MEASURES:
        tables = {t for t, _ in dax_column_refs(m.dax) if t in SEALED_FACTS} | {t for t in re.findall(r"\b(?:dim|fact)_\w+", m.dax) if t in SEALED_FACTS}
        reads = any(re.search(rf"\b{p}\b", m.dax) for p in PICKS)
        for table in tables:
            if reads:
                assert f'{table}[seal_state] = "sealed"' in m.dax, (m.name_es, table)
    assert {m.name_es for m in MEASURES if any(t in m.dax for t in SEALED_FACTS)} >= {"Facturas de la prueba", "Cargas malas", "Tasa de carga mala", "TED de CORD", "Veredicto", "QR leídos (%)"}
    for name in ("Facturas", "Cargadas solas", "A revisión", "Segundos p95 del día"):
        assert "fact_daily_ops" in MODEL.measure(name).dax and "seal_state" not in MODEL.measure(name).dax       # the follow-up has no sealed rows: it exists before the close
    assert 'fact_cord_eval[split] = "test"' in MODEL.measure("TED de CORD").dax and 'fact_cord_eval[field] = "all"' in MODEL.measure("TED de CORD").dax


def page(name: str) -> Page:
    return next(p for p in PAGES if p.name_es == name)


def visual(name: str, title: str) -> Visual:
    return next(v for v in page(name).visuals if v.title_es == title)


def test_there_are_five_pages_in_the_order_of_the_plan_inside_the_grid_without_overlaps():
    assert tuple(p.name_es for p in PAGES) == PAGE_NAMES and (PAGE_COLUMNS, PAGE_ROWS) == (12, 9)
    for p in PAGES:
        taken: set[tuple[int, int]] = set()
        for v in p.visuals:
            assert 0 <= v.x and v.x + v.w <= PAGE_COLUMNS and 0 <= v.y and v.y + v.h <= PAGE_ROWS and v.w >= 1 and v.h >= 1, (p.name_es, v.title_es)
            cells = {(x, y) for x in range(v.x, v.x + v.w) for y in range(v.y, v.y + v.h)}
            assert not cells & taken, (p.name_es, v.title_es)
            taken |= cells


def test_each_page_has_the_visuals_of_the_plan():
    first, second, third, fourth, fifth = PAGES
    assert [v.title_es for v in first.visuals if v.kind == "slicer"] == ["Fecha", "Cohorte"]
    assert [v.fields[0] for v in first.visuals if v.kind == "card"] == ["[Facturas]", "[Cargadas solas]", "[A revisión]", "[Paso directo (%)]", "[Segundos por factura]"]
    assert [v.title_es for v in second.visuals if v.kind == "slicer"] == ["Cohorte", "Clase sembrada"] and any(v.kind == "barChart" for v in second.visuals)
    assert any(v.title_es.startswith("Clase sembrada por primer motivo") for v in second.visuals) and any(v.title_es.startswith("Cola de revisión") for v in second.visuals)
    assert [v.title_es for v in third.visuals if v.kind == "slicer"] == ["Cohorte", "Perfil de escaneo"] and any(v.title_es.startswith("QR leídos") for v in third.visuals)
    assert [v.title_es for v in fourth.visuals][:1] == ["Criterios C01-C10"] and any("frente a B0-B3" in v.title_es for v in fourth.visuals) and any("elección" in v.title_es for v in fourth.visuals)
    assert [v.fields[0] for v in fifth.visuals if v.kind == "card"] == ["[Reglas bloqueantes fallidas]", "[Última fecha de cálculo]"]
    for p in PAGES:
        assert all(f"{m}" for m in p.visuals)


def _filtered_by(table: str) -> set[str]:
    """The tables a slicer on `table` filters: itself and, if it is a dimension, the facts it has a relationship with."""
    return {table} | {r.fact_table for r in MODEL.relationships if r.dim_table == table}


def _says_it_is_not_filtered_by(v: Visual, slicer: Visual) -> bool:
    match = re.search(r"\(no lo filtran? (.*)\)", v.title_es)
    return bool(match) and slicer.title_es.lower() in match.group(1).lower()


def test_every_slicer_reaches_the_visuals_of_its_page_through_a_relationship():
    """A slicer on a table with no path to a visual selects without filtering it, in silence (the correction A10 of project 04). Every slicer of every page reaches every visual of its page through
    the relationships of the model, or the visual's title says that this slicer does not filter it, and a title says so only when it is true."""
    slicers = 0
    for p in PAGES:
        for slicer in (v for v in p.visuals if v.kind == "slicer"):
            slicers += 1
            reached = _filtered_by(slicer.fields[0].split("[")[0])
            assert len(slicer.fields) == 1 and not slicer.fields[0].startswith("["), slicer.title_es
            for v in (v for v in p.visuals if v.kind != "slicer"):
                filtered = bool(reached & visual_tables(MODEL, v))
                assert filtered != _says_it_is_not_filtered_by(v, slicer), (p.name_es, slicer.title_es, v.title_es, filtered)
    assert slicers == 6
    # the two ways it can fail are seen: a slicer that reaches nothing of the page, and a title that says what is not so
    cohort = visual(PAGE_NAMES[0], "Cohorte")
    days = next(v for v in page(PAGE_NAMES[0]).visuals if v.title_es.startswith("Facturas por día"))
    assert not _filtered_by("dim_cohort") & visual_tables(MODEL, days) and _says_it_is_not_filtered_by(days, cohort)
    assert not _says_it_is_not_filtered_by(dataclasses.replace(days, title_es="Facturas por día"), cohort)


def test_a_visual_reads_only_tables_that_the_relationships_join():
    """A column of a dimension next to a measure of a fact it has no relationship with would show the same number in every row; two facts need a dimension that joins them."""
    kinds = {t.name: t.kind for t in MODEL.tables}
    for p in PAGES:
        for v in p.visuals:
            tables = visual_tables(MODEL, v)
            dims, facts = [t for t in tables if kinds[t] == "dimension"], [t for t in tables if kinds[t] == "fact"]
            assert not (len(facts) >= 2 and not dims) and not (len(dims) >= 2 and not facts), (p.name_es, v.title_es)
            for d in dims:
                for f in facts:
                    assert f in _filtered_by(d), (p.name_es, v.title_es, d, f)


def test_the_model_check_names_every_way_a_measure_or_a_page_can_be_wrong():
    assert validate_model(MODEL) == []
    good = MEASURES[0]

    def with_(**changes) -> SemanticModel:
        return dataclasses.replace(MODEL, **changes)

    def measure(dax: str, **changes) -> SemanticModel:
        return with_(measures=(*MEASURES, dataclasses.replace(good, name_es="Nueva", dax=dax, **changes)))

    first = PAGES[0].visuals[0]
    cases = [
        (measure("SUM ( fact_daily_ops[invoicez] )"), "fact_daily_ops[invoicez]"), (measure("SUM ( fact_daily_op[invoices] )"), "fact_daily_op"),
        (measure("COUNTROWS ( fact_invoic )"), "fact_invoic"), (measure("[No existe] + 1"), "[No existe]"), (measure("[Nueva] + 1"), "[Nueva]"),
        (measure("SUM ( fact_daily_ops[invoices] "), "paréntesis"), (measure('CALCULATE ( COUNTROWS ( fact_validation ), fact_validation[status] = "falla )'), "comillas"),
        (measure("MEDIAN ( fact_daily_ops[invoices] )"), "MEDIAN"), (measure('CALCULATE ( COUNTROWS ( fact_validation ), fact_validation[status] = "bien" )'), "'bien'"),
        (measure("CALCULATE ( COUNTROWS ( fact_validation ), ALL ( fact_validation ) )"), "ALL"), (measure("SUM ( fact_daily_ops[invoices] )", format_string=""), "formato"),
        (measure("SUM ( fact_daily_ops[invoices] )", description_es=""), "descripción"), (with_(measures=(*MEASURES, MEASURES[0])), "repetida"),
        (with_(measures=(dataclasses.replace(MEASURES[0], dax="[Facturas]"), *MEASURES[1:])), "no existe o es ella misma"),
        (with_(measures=(*MEASURES, dataclasses.replace(good, name_es="A", dax="[B] + 1"), dataclasses.replace(good, name_es="B", dax="[A] + 1"))), "en círculo"),
        (with_(pages=(*PAGES, PAGES[0])), "repetida"), (with_(pages=(*PAGES, Page("Vacía", ()))), "sin visuales"),
        (with_(pages=(Page("P", (dataclasses.replace(first, fields=("dim_date[nope]",)),)),)), "dim_date[nope]"),
        (with_(pages=(Page("P", (dataclasses.replace(first, fields=("dim_date[date]", "dim_cohort[label_es]")),)),)), "un segmentador usa una sola columna"),
        (with_(pages=(Page("P", (dataclasses.replace(first, x=10, w=4),)),)), "sale de la grilla"), (with_(pages=(Page("P", (first, dataclasses.replace(first, title_es="Otro"))),)), "se superpone"),
        (with_(pages=(Page("P", (Visual("table", "Dos hechos", ("fact_daily_ops[invoices]", "fact_baseline[bad_post]"), 0, 0, 4, 2),)),)), "dos hechos sin una dimensión"),
        (with_(pages=(Page("P", (Visual("table", "Dos dimensiones", ("dim_date[date]", "dim_scan[label_es]"), 0, 0, 4, 2),)),)), "dos dimensiones sin un hecho"),
        (with_(pages=(Page("P", (Visual("table", "Sin relación", ("dim_scan[label_es]", "fact_daily_ops[invoices]"), 0, 0, 4, 2),)),)), "no se unen por ninguna relación"),
    ]
    for model, text in cases:
        assert any(text in p for p in validate_model(model)), (text, validate_model(model))
    assert validate_model(dataclasses.replace(MODEL, measures=(), pages=())) == []          # a model without measures or pages is still checked as before


def test_no_text_of_the_report_promises_a_result_or_calls_a_synthetic_figure_real():
    forbidden = ("garantiza", "promete", "siempre acierta", "sin errores")
    texts = [m.description_es for m in MEASURES] + [v.title_es for p in PAGES for v in p.visuals] + [t.description_es for t in MODEL.tables] + [c.description_es for t in MODEL.tables for c in t.columns]
    for text in texts:
        assert not any(word in text.lower() for word in forbidden), text
