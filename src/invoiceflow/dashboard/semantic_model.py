"""The semantic model of the Power BI report: the single source of its tables and relationships (RF21, RF22, AC26, AC27; D37; plan, sections 19 and 20). Adapted from project 06's
``dashboard/semantic_model.py``.

Tables with their columns (type, description, unit) and relationships (all one to many, single direction, active) live here; the Arrow schema of every Parquet file, the data dictionary and the
HTML design come from this module. Names of tables, columns and files are in English; descriptions and labels in Spanish (RNF13). The measures (DAX) and the five pages of the report live here
too: ``validate_model`` checks the tables and the relationships and, for the measures and the pages, that every DAX reference and every field of a visual is a column or a measure of the model,
because no measure runs in the project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import pyarrow as pa

ARROW_TYPES = {"int8": pa.int8(), "int16": pa.int16(), "int32": pa.int32(), "int64": pa.int64(), "float64": pa.float64(), "string": pa.string(), "bool": pa.bool_(),
               "date": pa.date32(), "datetime": pa.timestamp("us")}


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str    # a key of ARROW_TYPES
    description_es: str
    unit: str | None = None


@dataclass(frozen=True)
class Table:
    name: str
    kind: Literal["dimension", "fact"]
    key: tuple[str, ...]          # a dimension's unique key or a fact's grain
    columns: tuple[Column, ...]
    description_es: str = ""

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


@dataclass(frozen=True)
class Relationship:
    rel_id: str
    dim_table: str
    dim_column: str
    fact_table: str
    fact_column: str
    cardinality: str = "one_to_many"
    cross_filter: str = "single"
    active: bool = True


@dataclass(frozen=True)
class Measure:
    name_es: str
    dax: str
    format_string: str
    description_es: str


@dataclass(frozen=True)
class Visual:
    kind: str                     # slicer, card, table, barChart, lineChart, scatter
    title_es: str
    fields: tuple[str, ...]       # "table[column]" or "[Measure]"
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Page:
    name_es: str
    visuals: tuple[Visual, ...]


@dataclass(frozen=True)
class SemanticModel:
    tables: tuple[Table, ...]
    relationships: tuple[Relationship, ...]
    measures: tuple[Measure, ...] = ()
    pages: tuple[Page, ...] = ()

    def table(self, name: str) -> Table:
        return next(t for t in self.tables if t.name == name)

    def measure(self, name: str) -> Measure:
        return next(m for m in self.measures if m.name_es == name)


def _c(name: str, dtype: str, description_es: str, unit: str | None = None) -> Column:
    return Column(name, dtype, description_es, unit)

RUN_KEY = _c("run_key", "string", "Corrida que lo escribió.")
SEAL_STATE = _c("seal_state", "string", "sealed (la configuración sellada) o post_seal (otra, posterior al sello).")
DATE_KEY = _c("date_key", "int32", "Fecha, como AAAAMMDD.")
SUPPLIER_KEY = _c("supplier_key", "string", "Proveedor: su CUIT sintético, o sin_identificar cuando la factura no se pudo atribuir a ninguno.")
REASON_KEY = _c("reason_key", "string", "Motivo del cruce (uno de los 18, en su orden) o none cuando no hay ninguno.")
FIELD_KEY = _c("field_key", "string", "Campo que lee el modelo: header.<nombre>, line.<nombre> o totals.<nombre>.")
COHORT_KEY = _c("cohort_key", "string", "Cohorte de la factura: test_known, test_new, validation_known o validation_new.")
SCAN_KEY = _c("scan_key", "string", "Perfil de escaneo de la página: clean, mild o poor.")
PLANTED_KEY = _c("planted_key", "string", "Discrepancia que el generador sembró (una de 13 clases) o none.")
BASELINE_KEY = _c("baseline_key", "string", "Línea base: flow (el flujo), b0, b1, b2 o b3.")
MODEL_KEY = _c("model_key", "string", "Modelo medido en la prueba de CORD: chosen (el elegido) o checkpoint (sin ajustar).")
FIT_SPLITS = _c("fit_splits", "string", "Con qué particiones se ajustó el modelo que dio la cifra: cord_train+synth_train, o checkpoint si no se ajustó aquí.")

DIM_DATE = Table("dim_date", "dimension", ("date_key",), (
    DATE_KEY, _c("date", "date", "Fecha."), _c("weekday_es", "string", "Nombre del día de la semana."), _c("period", "string", "history (hasta el lanzamiento) o test (los días de operación y el cierre)."),
), "Los días hábiles del calendario simulado que ya cerraron a la fecha de cálculo.")
DIM_SUPPLIER = Table("dim_supplier", "dimension", ("supplier_key",), (
    SUPPLIER_KEY, _c("name_es", "string", "Razón social ficticia."), _c("cuit", "string", "CUIT sintético, del rango 30-0000xxxx-d."),
    _c("cohort", "string", "known, new_validation o new_test: si el ajuste vio su diseño."), _c("layout", "string", "Diseño de la factura del proveedor, de L1 a L5."),
), "Los proveedores ficticios del maestro, más uno para las facturas sin proveedor atribuible.")
DIM_REASON = Table("dim_reason", "dimension", ("reason_key",), (
    REASON_KEY, _c("rank", "int8", "Lugar del motivo en el orden fijo del cruce, de 1 a 18; 0 si no hay motivo."), _c("label_es", "string", "Motivo, en español."),
    _c("rule_es", "string", "La regla que lo activa."),
), "Los 18 motivos del cruce y «sin motivo».")
DIM_FIELD = Table("dim_field", "dimension", ("field_key",), (
    FIELD_KEY, _c("group_es", "string", "Grupo del campo: Cabecera, Línea o Totales."), _c("label_es", "string", "Campo, en español."),
), "Los campos que lee el modelo.")
DIM_COHORT = Table("dim_cohort", "dimension", ("cohort_key",), (COHORT_KEY, _c("label_es", "string", "Cohorte, en español.")), "Las cuatro cohortes de facturas sintéticas.")
DIM_SCAN = Table("dim_scan", "dimension", ("scan_key",), (SCAN_KEY, _c("label_es", "string", "Perfil, en español.")), "Los perfiles de escaneo.")
DIM_PLANTED = Table("dim_planted", "dimension", ("planted_key",), (
    PLANTED_KEY, _c("label_es", "string", "Clase, en español."), _c("expected_reason_key", "string", "Primer motivo que el generador espera para la clase; vacío si no hay ninguno."),
), "Las 13 clases sembradas y la ausencia de discrepancia.")
DIM_BASELINE = Table("dim_baseline", "dimension", ("baseline_key",), (BASELINE_KEY, _c("label_es", "string", "Línea base, en español.")), "El flujo y sus cuatro líneas base.")
DIM_MODEL = Table("dim_model", "dimension", ("model_key",), (
    MODEL_KEY, _c("model_version", "string", "Versión del modelo (o la revisión del checkpoint)."), _c("chosen_epoch", "int32", "Época elegida; vacía para el checkpoint.", "época"),
    _c("artifact_sha256", "string", "Huella del artefacto; vacía para el checkpoint."), _c("config_sha256", "string", "Huella de la configuración sellada; vacía para el checkpoint."), SEAL_STATE,
), "El modelo elegido del lanzamiento y el checkpoint sin ajustar.")
DIM_PIPELINE_RUN = Table("dim_pipeline_run", "dimension", ("run_key",), (
    RUN_KEY, _c("started_at", "datetime", "Inicio."), _c("finished_at", "datetime", "Fin."), _c("status", "string", "Estado: running, ok, failed, rejected o no_delivery."),
    _c("delivery_id", "string", "Entrega que cargó."), _c("as_of", "datetime", "Instante de cálculo de la corrida."), _c("config_sha256", "string", "Huella de la configuración sellada."),
    _c("artifact_sha256", "string", "Huella del artefacto del modelo."), _c("invoices", "int64", "Facturas de la entrega.", "facturas"), _c("peak_working_set_mb", "float64", "Pico del conjunto de trabajo.", "MB"),
    _c("peak_gpu_mb", "float64", "Pico de memoria de la GPU.", "MB"), _c("memory_counter", "string", "Contador de memoria que dio las cifras."), _c("message", "string", "Mensaje de la corrida."),
), "Las corridas del flujo.")

FACT_INVOICE = Table("fact_invoice", "fact", ("invoice_key", "seal_state"), (
    _c("invoice_key", "string", "Factura: el nombre de su archivo sin extensión."), DATE_KEY, SUPPLIER_KEY, COHORT_KEY, SCAN_KEY, PLANTED_KEY, _c("file_format", "string", "png, jpeg o pdf."),
    _c("qr_cm", "float64", "Lado del QR impreso.", "cm"), _c("decision", "string", "auto_post (se carga sola) o review (a revisión)."),
    _c("first_reason_key", "string", "Primer motivo de la decisión, o none si se cargó sola."), _c("reasons", "int32", "Cuántos motivos tiene la decisión.", "motivos"),
    _c("posted", "bool", "El borrador quedó en el ERP."), _c("bad_post", "bool", "Se cargó sola con una discrepancia sembrada o con campos que no son los verdaderos."),
    _c("straight_through", "bool", "Sin discrepancia, con QR impreso y cargada bien."), _c("lines", "int32", "Líneas de la verdad.", "líneas"), _c("lines_exact", "int32", "Líneas leídas exactas.", "líneas"),
    _c("total", "float64", "Total decidido (del QR si lo hay), en la moneda de la factura (sintético)."), _c("currency", "string", "Moneda del total."), _c("seconds", "float64", "Segundos de procesamiento de la factura.", "segundos"),
    SEAL_STATE,
), "Una fila por factura de la prueba evaluada: lo emitido unido a la verdad sintética.")
FACT_INVOICE_REASON = Table("fact_invoice_reason", "fact", ("invoice_key", "reason_key"), (
    _c("invoice_key", "string", "Factura: el nombre de su archivo sin extensión."), REASON_KEY, _c("rank", "int8", "Lugar del motivo en el orden del cruce.")),
    "Cada motivo de cada decisión emitida; se relaciona con la dimensión de motivos y no con el hecho de facturas (no hay una dimensión de facturas).")
FACT_FIELD_READ = Table("fact_field_read", "fact", ("cohort_key", "field_key", "scan_key", "seal_state"), (
    COHORT_KEY, FIELD_KEY, SCAN_KEY, _c("leaves", "int32", "Hojas que el modelo escribió.", "hojas"),
    _c("correct", "int32", "Hojas que se normalizaron a un valor (no dice si es el verdadero: eso lo dicen las líneas exactas).", "hojas"), _c("recall", "float64", "Parte de las hojas que se normalizaron.", "proporción"), SEAL_STATE,
), "La lectura por cohorte, campo y perfil de escaneo.")
FACT_QR_READ = Table("fact_qr_read", "fact", ("cohort_key", "scan_key", "qr_cm", "reader", "seal_state"), (
    COHORT_KEY, SCAN_KEY, _c("qr_cm", "float64", "Lado del QR impreso.", "cm"), _c("reader", "string", "Lector que lo leyó (pyzbar o aruco) o none si ninguno.", None),
    _c("printed", "int32", "QR impresos.", "facturas"), _c("read_ok", "int32", "QR leídos con el texto exacto.", "facturas"), SEAL_STATE,
), "La lectura del QR por cohorte, perfil, tamaño impreso y lector.")
FACT_DAILY_OPS = Table("fact_daily_ops", "fact", ("date_key",), (
    DATE_KEY, _c("invoices", "int32", "Facturas del día.", "facturas"), _c("auto_post", "int32", "Cargadas solas.", "facturas"), _c("review", "int32", "A revisión.", "facturas"),
    _c("posted", "int32", "Borradores que quedaron en el ERP.", "facturas"), _c("post_failed", "int32", "Cargas que fallaron.", "facturas"), _c("quarantined", "int32", "Archivos en cuarentena.", "archivos"),
    _c("seconds_mean", "float64", "Segundos medios por factura.", "segundos"), _c("seconds_p95", "float64", "Segundos del percentil 95.", "segundos"),
), "El seguimiento de cada día de operación (existe antes del cierre).")
FACT_BASELINE = Table("fact_baseline", "fact", ("baseline_key", "cohort_key", "seal_state"), (
    BASELINE_KEY, COHORT_KEY, _c("auto_post", "int32", "Facturas que cargaría sola.", "facturas"), _c("bad_post", "int32", "De ellas, cargas malas.", "facturas"),
    _c("bad_post_rate", "float64", "Tasa de carga mala.", "proporción"), _c("ci_low", "float64", "Límite inferior de Wilson.", "proporción"), _c("ci_high", "float64", "Límite superior de Wilson.", "proporción"),
    _c("straight_through", "float64", "Paso directo.", "proporción"), _c("review_recall", "float64", "Parte de las sembradas que van a revisión.", "proporción"), SEAL_STATE,
), "El flujo y las líneas base B0 a B3 sobre las mismas facturas, por cohorte.")
FACT_CORD_EVAL = Table("fact_cord_eval", "fact", ("model_key", "field", "split", "seal_state"), (
    MODEL_KEY, _c("field", "string", "Campo medido: all (el árbol entero; no se mide campo por campo)."), _c("split", "string", "Partición de CORD: test."),
    _c("ted_mean", "float64", "TED medio (0 a 100) sobre los recibos.", "puntos"), _c("leaf_recall", "float64", "Parte de las hojas acertadas.", "proporción"), _c("receipts", "int32", "Recibos.", "recibos"),
    _c("seconds_mean", "float64", "Segundos por recibo; vacío: la prueba de CORD no los mide.", "segundos"), FIT_SPLITS, SEAL_STATE,
), "La prueba de CORD del elegido y del checkpoint, que se abre una sola vez en el cierre.")
FACT_CHOICE = Table("fact_choice", "fact", ("epoch",), (
    _c("epoch", "int32", "Época candidata."), _c("cord_val_ted", "float64", "TED en la validación de CORD.", "puntos"), _c("val_known_line_exact", "float64", "Líneas exactas en validation_known.", "proporción"),
    _c("val_new_line_exact", "float64", "Líneas exactas en validation_new.", "proporción"), _c("kept", "bool", "Conserva CORD: no pierde más que la tolerancia."), _c("chosen", "bool", "Es la época que eligió el lanzamiento."), FIT_SPLITS,
), "Las épocas candidatas con sus cifras de validación.")
FACT_EVALUATION_SUMMARY = Table("fact_evaluation_summary", "fact", ("criterion_id", "seal_state"), (
    _c("criterion_id", "string", "Criterio, de C01 a C10."), _c("name_es", "string", "Nombre del criterio."), _c("value", "float64", "Valor medido."), _c("baseline_value", "float64", "Valor de la base con que se compara."),
    _c("ci_low", "float64", "Límite inferior del intervalo."), _c("ci_high", "float64", "Límite superior del intervalo."), _c("threshold_es", "string", "Umbral, en español."),
    _c("passed", "bool", "Cumple; vacío en el informativo."), _c("seen_by_research", "string", "Si la investigación lo vio antes."), _c("seen_by_planner", "string", "Si el plan lo vio antes."),
    _c("synthetic", "bool", "La cifra sale de las facturas sintéticas."), _c("verdict_es", "string", "Veredicto publicado, en español: el que cuenta el resumen (el de C08, por factura)."), SEAL_STATE,
    _c("sealed_verdict_es", "string", "Veredicto tal como lo selló la prueba; igual al publicado salvo en C08."),
    _c("threshold_figure_es", "string", "Lo que prueba el umbral, en español: la cifra que se compara con él (el límite del intervalo en C01, C05 y C08)."),
), "Los criterios de la prueba y su veredicto.")
FACT_VALIDATION = Table("fact_validation", "fact", ("run_key", "rule_id"), (
    RUN_KEY, _c("rule_id", "string", "Regla de validación."), _c("name_es", "string", "Nombre de la regla."), _c("severity", "string", "bloqueante, advertencia o informativa."),
    _c("scope", "string", "Alcance: fuente, entrega, archivo o modelo."), _c("aggregation", "string", "Cómo se agrega entre corridas: source, per_run o latest."),
    _c("status", "string", "pasa, falla (bloqueante que no pasa) o advierte."), _c("files_affected", "int64", "Archivos afectados.", "archivos"),
), "El resultado de cada regla en cada corrida.")

TABLES: tuple[Table, ...] = (DIM_DATE, DIM_SUPPLIER, DIM_REASON, DIM_FIELD, DIM_COHORT, DIM_SCAN, DIM_PLANTED, DIM_BASELINE, DIM_MODEL, DIM_PIPELINE_RUN,
                             FACT_INVOICE, FACT_INVOICE_REASON, FACT_FIELD_READ, FACT_QR_READ, FACT_DAILY_OPS, FACT_BASELINE, FACT_CORD_EVAL, FACT_CHOICE, FACT_EVALUATION_SUMMARY, FACT_VALIDATION)
# (dimension, its key, ((fact, the fact's column), ...)), in the order of the plan's table of relationships (R01 to R17)
_RELATIONSHIPS = (
    ("dim_date", "date_key", (("fact_invoice", "date_key"), ("fact_daily_ops", "date_key"))),
    ("dim_supplier", "supplier_key", (("fact_invoice", "supplier_key"),)),
    ("dim_reason", "reason_key", (("fact_invoice", "first_reason_key"), ("fact_invoice_reason", "reason_key"))),
    ("dim_field", "field_key", (("fact_field_read", "field_key"),)),
    ("dim_cohort", "cohort_key", (("fact_invoice", "cohort_key"), ("fact_field_read", "cohort_key"), ("fact_qr_read", "cohort_key"), ("fact_baseline", "cohort_key"))),
    ("dim_scan", "scan_key", (("fact_invoice", "scan_key"), ("fact_field_read", "scan_key"), ("fact_qr_read", "scan_key"))),
    ("dim_planted", "planted_key", (("fact_invoice", "planted_key"),)),
    ("dim_baseline", "baseline_key", (("fact_baseline", "baseline_key"),)),
    ("dim_model", "model_key", (("fact_cord_eval", "model_key"),)),
    ("dim_pipeline_run", "run_key", (("fact_validation", "run_key"),)),
)
RELATIONSHIPS: tuple[Relationship, ...] = tuple(
    Relationship(f"R{number:02d}", dim, key, fact, column) for number, (dim, key, fact, column) in enumerate(
        ((dim, key, fact, column) for dim, key, facts in _RELATIONSHIPS for fact, column in facts), start=1))
PAGE_COLUMNS, PAGE_ROWS = 12, 9

# --- the measures and the pages (plan, section 20; U17) --------------------------------------------------------------------------------------------------------------

INTEGER, PERCENT, DECIMAL_3, DATETIME_FORMAT, TEXT_FORMAT = "#,0", "0.0%", "0.000", "yyyy-mm-dd hh:mm", "@"
_S = 'fact_invoice[seal_state] = "sealed"'

MEASURES: tuple[Measure, ...] = (
    Measure("Facturas", "SUM ( fact_daily_ops[invoices] )", INTEGER, "Facturas de los días elegidos, desde el seguimiento diario: existe antes del cierre y no depende de la prueba."),
    Measure("Cargadas solas", "SUM ( fact_daily_ops[auto_post] )", INTEGER, "Facturas de los días elegidos que el flujo cargó solo."),
    Measure("A revisión", "SUM ( fact_daily_ops[review] )", INTEGER, "Facturas de los días elegidos que fueron a revisión."),
    Measure("Facturas de la prueba", f"CALCULATE ( COUNTROWS ( fact_invoice ), {_S} )", INTEGER, "Facturas de la prueba evaluada con la configuración sellada, en las cohortes y las clases elegidas."),
    Measure("Facturas a revisión de la prueba", f'CALCULATE ( COUNTROWS ( fact_invoice ), fact_invoice[decision] = "review", {_S} )', INTEGER, "Facturas de la prueba evaluada que fueron a revisión."),
    Measure("Paso directo (%)", f'DIVIDE ( CALCULATE ( COUNTROWS ( fact_invoice ), fact_invoice[straight_through] = TRUE (), {_S} ), CALCULATE ( COUNTROWS ( fact_invoice ), fact_invoice[planted_key] = "none", {_S} ) )',
            PERCENT, "Parte de las facturas sin discrepancia sembrada que se cargaron solas y bien (sintético)."),
    Measure("Cargas malas", f"CALCULATE ( COUNTROWS ( fact_invoice ), fact_invoice[bad_post] = TRUE (), {_S} )", INTEGER, "Facturas cargadas solas con una discrepancia sembrada o con campos que no son los verdaderos (sintético)."),
    Measure("Tasa de carga mala", f'DIVIDE ( [Cargas malas], CALCULATE ( COUNTROWS ( fact_invoice ), fact_invoice[decision] = "auto_post", {_S} ) )', PERCENT,
            "Cargas malas sobre las facturas cargadas solas (sintético)."),
    Measure("Líneas exactas (%)", f"DIVIDE ( CALCULATE ( SUM ( fact_invoice[lines_exact] ), {_S} ), CALCULATE ( SUM ( fact_invoice[lines] ), {_S} ) )", PERCENT,
            "Parte de las líneas leídas exactas, contra la verdad sintética."),
    Measure("Segundos por factura", f"CALCULATE ( AVERAGE ( fact_invoice[seconds] ), {_S} )", DECIMAL_3, "Segundos medios de procesamiento por factura de la prueba; no es el tiempo de una persona."),
    Measure("Segundos p95 del día", "MAX ( fact_daily_ops[seconds_p95] )", DECIMAL_3, "Segundos del percentil 95 de los días elegidos: el mayor de ellos."),
    Measure("QR leídos (%)", 'DIVIDE ( CALCULATE ( SUM ( fact_qr_read[read_ok] ), fact_qr_read[seal_state] = "sealed" ), CALCULATE ( SUM ( fact_qr_read[printed] ), fact_qr_read[seal_state] = "sealed" ) )', PERCENT,
            "Parte de los QR impresos que se leyeron con el texto exacto (sintético)."),
    Measure("Recuerdo del campo", 'DIVIDE ( CALCULATE ( SUM ( fact_field_read[correct] ), fact_field_read[seal_state] = "sealed" ), CALCULATE ( SUM ( fact_field_read[leaves] ), fact_field_read[seal_state] = "sealed" ) )',
            PERCENT, "Parte de las hojas que el modelo escribió y que se normalizaron a un valor (no dice si es el verdadero)."),
    Measure("Carga mala de la línea base", 'CALCULATE ( SELECTEDVALUE ( fact_baseline[bad_post_rate] ), fact_baseline[seal_state] = "sealed" )', PERCENT,
            "Tasa de carga mala de la línea base (o del flujo) de la cohorte elegida, con la configuración sellada."),
    Measure("TED de CORD", 'CALCULATE ( SELECTEDVALUE ( fact_cord_eval[ted_mean] ), fact_cord_eval[field] = "all", fact_cord_eval[split] = "test", fact_cord_eval[seal_state] = "sealed" )', DECIMAL_3,
            "TED medio del modelo elegido o del checkpoint en la prueba de CORD, con la configuración sellada."),
    Measure("Veredicto", 'CALCULATE ( SELECTEDVALUE ( fact_evaluation_summary[verdict_es] ), fact_evaluation_summary[seal_state] = "sealed" )', TEXT_FORMAT, "Veredicto de un criterio con la configuración sellada."),
    Measure("Última fecha de cálculo", 'CALCULATE ( MAX ( dim_pipeline_run[as_of] ), dim_pipeline_run[status] = "ok" )', DATETIME_FORMAT, "Instante de cálculo de la última corrida terminada bien."),
    Measure("Reglas bloqueantes fallidas", 'CALCULATE ( COUNTROWS ( fact_validation ), fact_validation[severity] = "bloqueante", fact_validation[status] = "falla" )', INTEGER,
            "Resultados de reglas bloqueantes que fallaron, en las corridas cargadas."),
)

NOT_FILTERED = " (no lo filtra{plural} {names})"
PAGES: tuple[Page, ...] = (
    Page("La operación, día por día", (
        Visual("slicer", "Fecha", ("dim_date[date]",), 0, 0, 6, 1),
        Visual("slicer", "Cohorte", ("dim_cohort[label_es]",), 6, 0, 6, 1),
        Visual("card", "Facturas" + NOT_FILTERED.format(plural="", names="la cohorte"), ("[Facturas]",), 0, 1, 2, 1),
        Visual("card", "Cargadas solas" + NOT_FILTERED.format(plural="", names="la cohorte"), ("[Cargadas solas]",), 2, 1, 2, 1),
        Visual("card", "A revisión" + NOT_FILTERED.format(plural="", names="la cohorte"), ("[A revisión]",), 4, 1, 2, 1),
        Visual("card", "Paso directo (%)", ("[Paso directo (%)]",), 6, 1, 3, 1),
        Visual("card", "Segundos por factura", ("[Segundos por factura]",), 9, 1, 3, 1),
        Visual("barChart", "Facturas por día: cargadas solas y a revisión" + NOT_FILTERED.format(plural="", names="la cohorte"), ("dim_date[date]", "fact_daily_ops[auto_post]", "fact_daily_ops[review]"), 0, 2, 12, 4),
        Visual("lineChart", "Segundos del percentil 95, día por día" + NOT_FILTERED.format(plural="", names="la cohorte"), ("dim_date[date]", "[Segundos p95 del día]"), 0, 6, 12, 3),
    )),
    Page("Excepciones por motivo", (
        Visual("slicer", "Cohorte", ("dim_cohort[label_es]",), 0, 0, 6, 1),
        Visual("slicer", "Clase sembrada", ("dim_planted[label_es]",), 6, 0, 6, 1),
        Visual("barChart", "Facturas a revisión por primer motivo", ("dim_reason[label_es]", "[Facturas a revisión de la prueba]"), 0, 1, 6, 4),
        Visual("table", "Clase sembrada por primer motivo (la diagonal es lo esperado)", ("dim_planted[label_es]", "dim_reason[label_es]", "[Facturas de la prueba]"), 6, 1, 6, 4),
        Visual("table", "Cola de revisión: facturas a revisión con sus motivos", ("fact_invoice[invoice_key]", "dim_supplier[name_es]", "dim_reason[label_es]", "fact_invoice[reasons]", "fact_invoice[total]"), 0, 5, 12, 4),
    )),
    Page("Calidad de lectura", (
        Visual("slicer", "Cohorte", ("dim_cohort[label_es]",), 0, 0, 6, 1),
        Visual("slicer", "Perfil de escaneo", ("dim_scan[label_es]",), 6, 0, 6, 1),
        Visual("barChart", "Recuerdo por campo", ("dim_field[label_es]", "[Recuerdo del campo]"), 0, 1, 6, 4),
        Visual("barChart", "Líneas exactas por cohorte y perfil", ("dim_cohort[label_es]", "dim_scan[label_es]", "[Líneas exactas (%)]"), 6, 1, 6, 4),
        Visual("barChart", "QR leídos por tamaño impreso, perfil y lector", ("fact_qr_read[qr_cm]", "dim_scan[label_es]", "fact_qr_read[reader]", "[QR leídos (%)]"), 0, 5, 6, 4),
        Visual("table", "TED de CORD del elegido y del checkpoint" + NOT_FILTERED.format(plural="n", names="la cohorte ni el perfil de escaneo"), ("dim_model[model_key]", "[TED de CORD]"), 6, 5, 6, 4),
    )),
    Page("La prueba", (
        Visual("table", "Criterios C01-C10", ("fact_evaluation_summary[criterion_id]", "fact_evaluation_summary[name_es]", "fact_evaluation_summary[threshold_figure_es]", "fact_evaluation_summary[threshold_es]",
                                              "fact_evaluation_summary[seen_by_research]", "fact_evaluation_summary[seen_by_planner]", "[Veredicto]"), 0, 0, 12, 4),
        Visual("barChart", "Carga mala del flujo frente a B0-B3", ("dim_baseline[label_es]", "dim_cohort[label_es]", "[Carga mala de la línea base]"), 0, 4, 6, 5),
        Visual("table", "La elección de la época en la validación", ("fact_choice[epoch]", "fact_choice[cord_val_ted]", "fact_choice[val_known_line_exact]", "fact_choice[val_new_line_exact]",
                                                                    "fact_choice[kept]", "fact_choice[chosen]", "fact_choice[fit_splits]"), 6, 4, 6, 5),
    )),
    Page("Calidad de datos y corridas", (
        Visual("card", "Reglas bloqueantes fallidas", ("[Reglas bloqueantes fallidas]",), 0, 0, 6, 1),
        Visual("card", "Última fecha de cálculo", ("[Última fecha de cálculo]",), 6, 0, 6, 1),
        Visual("table", "Reglas de validación", ("fact_validation[rule_id]", "fact_validation[name_es]", "fact_validation[severity]", "fact_validation[files_affected]", "fact_validation[status]"), 0, 1, 6, 4),
        Visual("table", "Corridas", ("dim_pipeline_run[run_key]", "dim_pipeline_run[delivery_id]", "dim_pipeline_run[as_of]", "dim_pipeline_run[status]", "dim_pipeline_run[invoices]", "dim_pipeline_run[message]"), 6, 1, 6, 4),
        Visual("table", "Archivos en cuarentena por día", ("dim_date[date]", "fact_daily_ops[quarantined]"), 0, 5, 12, 4),
    )),
)
MODEL = SemanticModel(TABLES, RELATIONSHIPS, MEASURES, PAGES)


def arrow_schema(table: Table) -> pa.Schema:
    """The Arrow schema of a table: its columns in order with their types."""
    return pa.schema([pa.field(c.name, ARROW_TYPES[c.dtype]) for c in table.columns])


def validate_model(model: SemanticModel) -> list[str]:
    """What is incoherent in `model` (empty when nothing is): unique table names, known types, keys among the columns, relationships between a dimension's whole key
    and a fact's column of the same type, one to many, single direction and active; then the measures (``_measure_problems``) and the pages (``_page_problems``)."""
    problems: list[str] = []
    names = [t.name for t in model.tables]
    if len(set(names)) != len(names):
        problems.append("hay tablas repetidas")
    by_name = {t.name: t for t in model.tables}
    for table in model.tables:
        columns = {c.name: c for c in table.columns}
        if len(columns) != len(table.columns):
            problems.append(f"{table.name} repite una columna")
        problems += [f"{table.name}[{c.name}] tiene un tipo desconocido {c.dtype}" for c in table.columns if c.dtype not in ARROW_TYPES]
        problems += [f"la clave {k} de {table.name} no es una columna" for k in table.key if k not in columns]
    for r in model.relationships:
        dim, fact = by_name.get(r.dim_table), by_name.get(r.fact_table)
        if dim is None or fact is None or dim.kind != "dimension" or fact.kind != "fact":
            problems.append(f"{r.rel_id} no une una dimensión con un hecho")
            continue
        if dim.key != (r.dim_column,):
            problems.append(f"{r.rel_id} no usa la clave entera de {dim.name}")
        dim_col = next((c for c in dim.columns if c.name == r.dim_column), None)
        fact_col = next((c for c in fact.columns if c.name == r.fact_column), None)
        if dim_col is None or fact_col is None or dim_col.dtype != fact_col.dtype:
            problems.append(f"{r.rel_id}: {r.fact_table}[{r.fact_column}] no es una columna del tipo de {r.dim_table}[{r.dim_column}]")
        if (r.cardinality, r.cross_filter, r.active) != ("one_to_many", "single", True):
            problems.append(f"{r.rel_id} no es uno a varios, de filtro unidireccional y activa")
    return problems + _measure_problems(model) + _page_problems(model)


# --- the static checks that stand in for running the measures (item 6 of the risks: no DAX runs anywhere in the project) -----------------------------------------------------------------

_COLUMN_REF = re.compile(r"(?<![\w'])([A-Za-z_]\w*)\[(\w+)\]")
_MEASURE_REF = re.compile(r"(?<![\w\]'])\[([^\[\]]+)\]")
_FUNCTION_CALL = re.compile(r"\b([A-Z][A-Z0-9.]*)(?=\s*\()")
_LITERAL_COMPARISON = re.compile(r'\b([a-z_]\w*)\[(\w+)\]\s*=\s*"([^"]*)"')
_REMOVES_FILTERS = re.compile(r"\b(ALL|ALLEXCEPT|ALLSELECTED|REMOVEFILTERS|CROSSFILTER|USERELATIONSHIP|KEEPFILTERS)\b")
DAX_FUNCTIONS = frozenset({"CALCULATE", "COUNTROWS", "DISTINCTCOUNT", "DIVIDE", "MAX", "SELECTEDVALUE", "SUM", "SUMX", "NOT", "ISBLANK", "TRUE", "AVERAGE"})


def dax_column_refs(dax: str) -> set[tuple[str, str]]:
    """The ``table[column]`` references of a DAX expression."""
    return set(_COLUMN_REF.findall(dax))


def dax_measure_refs(dax: str) -> set[str]:
    """The ``[Measure]`` references of a DAX expression: a bracket not attached to a table."""
    return set(_MEASURE_REF.findall(dax))


def visual_tables(model: SemanticModel, visual: Visual) -> set[str]:
    """The tables a visual reads: those of its columns and, through the DAX of its measures and of the measures they name, those the measures name by a column or whole."""
    found: set[str] = set()
    pending, seen = list(visual.fields), set()
    while pending:
        field = pending.pop()
        if field.startswith("["):
            name = field[1:-1]
            measure = next((m for m in model.measures if m.name_es == name), None)
            if measure is not None and name not in seen:
                seen.add(name)
                found |= {t.name for t in model.tables if re.search(rf"\b{t.name}\b", measure.dax)}
                pending += [f"[{n}]" for n in dax_measure_refs(measure.dax)]
        else:
            found.add(field.split("[")[0])
    return {t for t in found if any(table.name == t for table in model.tables)}


def _field_problem(model: SemanticModel, field: str) -> str | None:
    """Why the field of a visual does not resolve, or None."""
    if field.startswith("["):
        return None if any(f"[{m.name_es}]" == field for m in model.measures) else f"la medida {field} no existe"
    match = re.fullmatch(r"([A-Za-z_]\w*)\[(\w+)\]", field)
    if match is None:
        return f"el campo {field!r} no es una columna ni una medida"
    table, column = match.groups()
    return None if any(t.name == table and column in t.column_names for t in model.tables) else f"la columna {field} no existe"


def _measure_problems(model: SemanticModel) -> list[str]:
    problems: list[str] = []
    names = [m.name_es for m in model.measures]
    problems += [f"la medida [{n}] está repetida" for n in sorted({n for n in names if names.count(n) > 1})]
    columns = {t.name: {c.name: c for c in t.columns} for t in model.tables}
    uses: dict[str, set[str]] = {}
    for m in model.measures:
        label = f"[{m.name_es}]"
        if m.dax.count("(") != m.dax.count(")"):
            problems.append(f"{label}: paréntesis sin cerrar en el DAX")
        if m.dax.count('"') % 2:
            problems.append(f"{label}: comillas sin cerrar en el DAX")
        problems += [f"{label}: {t}[{c}] no existe" for t, c in sorted(dax_column_refs(m.dax)) if c not in columns.get(t, {})]
        problems += [f"{label}: {w} no es una tabla" for w in sorted(set(re.findall(r"\b(?:dim|fact)_\w+", m.dax)) - set(columns))]
        problems += [f"{label}: la función {f} no es de las que usa el plan" for f in sorted(set(_FUNCTION_CALL.findall(m.dax)) - DAX_FUNCTIONS)]
        uses[m.name_es] = dax_measure_refs(m.dax)
        problems += [f"{label}: la medida [{r}] no existe o es ella misma" for r in sorted(uses[m.name_es]) if r not in names or r == m.name_es]
        for table, column, literal in _LITERAL_COMPARISON.findall(m.dax):
            if column in columns.get(table, {}) and literal not in columns[table][column].description_es:
                problems.append(f"{label}: compara {table}[{column}] con '{literal}', que la columna no toma")
        problems += [f"{label}: {w} quita los filtros de los segmentadores" for w in sorted(set(_REMOVES_FILTERS.findall(m.dax)))]
        if not m.format_string or not m.description_es:
            problems.append(f"{label}: necesita un formato y una descripción")

    def circle(name: str, path: tuple[str, ...]) -> bool:
        return name in path or any(circle(n, (*path, name)) for n in uses.get(name, ()) if n in uses and n != name)

    problems += [f"las medidas [{n}] se llaman en círculo" for n in sorted(uses) if any(circle(r, (n,)) for r in uses[n] if r in uses and r != n)]
    return problems


def _page_problems(model: SemanticModel) -> list[str]:
    problems: list[str] = []
    titles = [p.name_es for p in model.pages]
    problems += [f"página repetida: {t}" for t in sorted({t for t in titles if titles.count(t) > 1})]
    related = {(r.dim_table, r.fact_table) for r in model.relationships}
    kinds = {t.name: t.kind for t in model.tables}
    for page in model.pages:
        if not page.visuals:
            problems.append(f"{page.name_es}: sin visuales")
        taken: dict[tuple[int, int], str] = {}
        for v in page.visuals:
            label = f"{page.name_es}: {v.title_es}"
            problems += [f"{label}: {why}" for field in v.fields if (why := _field_problem(model, field)) is not None]
            if v.kind == "slicer" and (len(v.fields) != 1 or v.fields[0].startswith("[")):
                problems.append(f"{label}: un segmentador usa una sola columna")
            tables = visual_tables(model, v)
            dims, facts = sorted(t for t in tables if kinds[t] == "dimension"), sorted(t for t in tables if kinds[t] == "fact")
            if len(facts) >= 2 and not dims:
                problems.append(f"{label}: dos hechos sin una dimensión que los una")
            if len(dims) >= 2 and not facts:
                problems.append(f"{label}: dos dimensiones sin un hecho que las una")
            problems += [f"{label}: {d} y {f} no se unen por ninguna relación" for d in dims for f in facts if (d, f) not in related]
            if v.w < 1 or v.h < 1 or v.x < 0 or v.y < 0 or v.x + v.w > PAGE_COLUMNS or v.y + v.h > PAGE_ROWS:
                problems.append(f"{label}: sale de la grilla de {PAGE_COLUMNS} x {PAGE_ROWS}")
                continue
            cells = [(cx, cy) for cx in range(v.x, v.x + v.w) for cy in range(v.y, v.y + v.h)]
            clash = next((taken[c] for c in cells if c in taken), None)
            if clash is not None:
                problems.append(f"{label}: se superpone con {clash}")
            taken.update({c: v.title_es for c in cells})
    return problems
