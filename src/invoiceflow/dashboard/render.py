"""The HTML design of the Power BI report, generated from the semantic model and the exported Parquet (RF22, AC27; plan, section 20; D37). Adapted from project 06's ``dashboard/render.py``; it is
not imported from it.

One self-contained page (inline CSS, inline SVG, no scripts, no fonts or images from the network) with the steps to build the report in Power BI Desktop, the diagram of the model, the relationships,
the Power Query code of each table, the DAX measures and the five pages placed on a 12 x 9 grid, each visual with a preview drawn from the exported tables. Nothing here is typed by hand twice: change
``semantic_model.py`` and render again.

Rules for what a preview shows. Every text that comes from the data goes through ``html.escape``. The previews are the figures of the exported tables with no slicer applied: the document says that no
measure was run (there is no Power BI here), so a card is what the table says, not what the DAX would give. The test shows its sealed rows and, when there are none, says that it is pending. Every page
says that the data are synthetic except CORD's, and a «time» is the processing time of the invoice, not the time of a person. The file is written through ``files.write_atomic``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from html import escape
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from invoiceflow import labels
from invoiceflow.dashboard import svg
from invoiceflow.dashboard.semantic_model import NOT_FILTERED, PAGE_COLUMNS, PAGE_ROWS, Page, SemanticModel, Visual, dax_measure_refs, validate_model
from invoiceflow.files import write_atomic
from invoiceflow.registry import RunMeta

logger = logging.getLogger(__name__)

PAGE_WIDTH_PX, PAGE_HEIGHT_PX = 1280, 720
CELL_W, CELL_H = PAGE_WIDTH_PX / PAGE_COLUMNS, PAGE_HEIGHT_PX / PAGE_ROWS
HEADER_PX = 26
TABLE_ROWS = 9              # the most rows a table preview shows
DAYS_SHOWN = 20             # the most days the bars of page 1 show
LEGEND = "Datos sintéticos salvo CORD"
TITLE = "Diseño del tablero de Power BI: facturas que se cargan solas al ERP, con lo que no cierra a revisión"
SECTIONS = (
    ("armado", "Cómo armarlo en Power BI Desktop"),
    ("modelo", "El modelo en estrella"),
    ("relaciones", "Relaciones"),
    ("consultas", "Power Query: una consulta por tabla"),
    ("medidas", "Medidas DAX"),
    ("paginas", "Las cinco páginas"),
    ("fuente", "Fuente y licencia"),
)
KIND_ES = {"slicer": "Segmentador", "card": "Tarjeta", "lineChart": "Gráfico de líneas", "barChart": "Gráfico de barras", "table": "Tabla", "scatter": "Dispersión"}
DASH = "—"
CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--panel:#f3f2ef;--text:#0b0b0b;--text-2:#52514e;--muted:#6f6e69;--line:#d9d8d3;--accent:#256abf;
--series-1:#2a78d6;--series-2:#eb6834;--series-3:#1baf7a;--series-4:#eda100;--series-5:#e87ba4;--series-6:#008300;--series-7:#6250d6;--series-8:#e34948}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--surface:#1a1a19;--panel:#242422;--text:#f0efec;--text-2:#c3c2b7;--muted:#a3a299;--line:#3a3a37;--accent:#6da7ec;
--series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--series-4:#c98500;--series-5:#d55181;--series-6:#008300;--series-7:#9085e9;--series-8:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--panel:#242422;--text:#f0efec;--text-2:#c3c2b7;--muted:#a3a299;--line:#3a3a37;--accent:#6da7ec;
--series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--series-4:#c98500;--series-5:#d55181;--series-6:#008300;--series-7:#9085e9;--series-8:#e66767}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--text);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;line-height:1.25;margin:0 0 4px}h2{font-size:1.3rem;margin:48px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
h3{font-size:1.05rem;margin:28px 0 8px}p,li{max-width:78ch}a{color:var(--accent)}
.lead{color:var(--text-2);margin:0 0 16px}.meta{display:flex;flex-wrap:wrap;gap:8px 24px;color:var(--text-2);font-size:.9rem;margin:12px 0}
nav ul{display:flex;flex-wrap:wrap;gap:4px 16px;padding:0;list-style:none;margin:16px 0}
code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.85rem}
pre{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px 12px;overflow-x:auto;margin:6px 0;white-space:pre}
table{border-collapse:collapse;width:100%;font-size:.88rem}th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{color:var(--text-2);font-weight:600;background:var(--surface)}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}.warn{border-left:3px solid var(--series-4);padding:4px 12px;background:var(--panel);margin:12px 0}
.model-svg{width:100%;height:auto;display:block}.model-svg .box{fill:var(--panel);stroke:var(--line)}.model-svg .dim{stroke:var(--series-1)}
.model-svg .fact{stroke:var(--series-2)}.model-svg text{fill:var(--text);font-size:12px}.model-svg .small{fill:var(--text-2);font-size:10px}
.model-svg .rel{stroke:var(--muted);stroke-width:1;fill:none;opacity:.55}.model-svg .card{font-weight:700;fill:var(--accent)}
.canvas-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--panel);padding:8px}
.canvas{display:grid;grid-template-columns:repeat(12,1fr);grid-template-rows:repeat(9,1fr);gap:6px;aspect-ratio:16/9;min-width:960px;max-width:1280px}
.visual{background:var(--surface);border:1px solid var(--line);border-radius:6px;overflow:hidden;display:flex;flex-direction:column;min-width:0;min-height:0}
.visual header{display:flex;justify-content:space-between;gap:6px;padding:2px 8px;font-size:.75rem;color:var(--text-2);height:26px;align-items:center}
.visual header b{color:var(--text);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.visual header span{white-space:nowrap;color:var(--muted)}
.visual .body{flex:1;min-height:0;padding:0 8px 3px;overflow:hidden;font-size:.75rem}.visual .body svg{max-width:100%;height:auto;display:block}
.big{font-size:1.2rem;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sub{color:var(--muted);font-size:.65rem;line-height:1.1}
.chips{display:flex;flex-wrap:nowrap;gap:3px;overflow:hidden}.chip{white-space:nowrap;border:1px solid var(--line);border-radius:10px;padding:0 6px;font-size:.7rem;color:var(--text-2)}
.visual table{font-size:.7rem}.visual th,.visual td{padding:1px 5px}.rows p{margin:1px 0;font-size:.75rem}
.multiples{display:grid;grid-template-columns:repeat(4,1fr);gap:2px 6px}.multiples figure{margin:0;min-width:0}.multiples figcaption{font-size:.6rem;color:var(--text-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
svg .svg-grid{stroke:var(--line);stroke-width:1}svg .svg-axis{stroke:var(--muted);stroke-width:1}
svg .svg-tick,svg .svg-legend,svg .svg-note,svg .svg-value{fill:var(--text-2);font-size:10px}svg .svg-value{fill:var(--text)}
svg .svg-hit{fill:transparent}svg .svg-hit:hover{fill:var(--text);fill-opacity:.15}
.placement td:first-child{white-space:nowrap}
@media print{.canvas-wrap{break-inside:avoid}body{font-size:12px}}
"""


# --- data ---------------------------------------------------------------------------------------------------------------------


class _Data:
    """The exported tables the previews are drawn from (a missing file is an empty table with its columns) and what their files say about the run."""

    def __init__(self, parquet_dir: Path, model: SemanticModel) -> None:
        self.t: dict[str, pd.DataFrame] = {}
        for table in model.tables:
            path = Path(parquet_dir) / f"{table.name}.parquet"
            self.t[table.name] = pd.read_parquet(path) if path.is_file() else pd.DataFrame(columns=list(table.column_names))
        path = Path(parquet_dir) / "dim_pipeline_run.parquet"
        metadata = pq.read_schema(path).metadata or {} if path.is_file() else {}
        self.artifact_sha256 = metadata.get(b"invoiceflow.artifact_sha256", b"").decode("utf-8", "replace") or DASH
        self.reason_es = dict(zip(self.t["dim_reason"]["reason_key"], self.t["dim_reason"]["label_es"], strict=True))
        self.planted_es = dict(zip(self.t["dim_planted"]["planted_key"], self.t["dim_planted"]["label_es"], strict=True))
        self.cohort_es = dict(zip(self.t["dim_cohort"]["cohort_key"], self.t["dim_cohort"]["label_es"], strict=True))
        self.scan_es = dict(zip(self.t["dim_scan"]["scan_key"], self.t["dim_scan"]["label_es"], strict=True))
        self.field_es = dict(zip(self.t["dim_field"]["field_key"], self.t["dim_field"]["label_es"], strict=True))
        self.baseline_es = dict(zip(self.t["dim_baseline"]["baseline_key"], self.t["dim_baseline"]["label_es"], strict=True))
        self.model_es = dict(zip(self.t["dim_model"]["model_key"], self.t["dim_model"]["model_key"].map(labels.MODEL_LABELS_ES), strict=True))
        self.supplier_es = dict(zip(self.t["dim_supplier"]["supplier_key"], self.t["dim_supplier"]["name_es"], strict=True))

    def sealed(self, name: str) -> pd.DataFrame:
        """The sealed rows of a fact. A row that is not sealed is never a figure of the test."""
        frame = self.t[name]
        return frame[frame["seal_state"] == "sealed"] if "seal_state" in frame.columns else frame


def _missing(value) -> bool:
    return value is None or bool(pd.isna(value))


def _fmt(value, digits: int = 3) -> str:
    return DASH if _missing(value) else f"{float(value):.{digits}f}".replace(".", ",")


def _int(value) -> str:
    return DASH if _missing(value) else f"{int(value):,}".replace(",", ".")


def _pct(value, digits: int = 1) -> str:
    return DASH if _missing(value) else f"{float(value) * 100:.{digits}f} %".replace(".", ",")


def _when(value) -> str:
    return DASH if _missing(value) else pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def _table_html(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    """A table; every cell is escaped."""
    head = "".join(f'<th{" class=num" if i in numeric else ""}>{escape(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td{" class=num" if i in numeric else ""}>{escape(str(c))}</td>' for i, c in enumerate(row)) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body or '<tr><td>Sin datos</td></tr>'}</tbody></table>"


def _size(v: Visual) -> tuple[int, int]:
    return max(int(v.w * CELL_W - 22), 60), max(int(v.h * CELL_H - HEADER_PX - 12), 40)


def _card(big: str, sub: str = "") -> str:
    return f'<div class="big">{escape(big)}</div><div class="sub">{escape(sub)}</div>'


def _note(text: str) -> str:
    return f'<div class="rows"><p>{escape(text)}</p></div>'


def _caption(text: str) -> str:
    return f'<div class="sub">{escape(text)}</div>'



# --- previews ---------------------------------------------------------------------------------------------------------------------


def _slicer(data: _Data, field: str) -> str:
    table, column = field.split("[")[0], field.split("[")[1].rstrip("]")
    values = data.t[table][column].dropna() if column in data.t[table].columns else pd.Series(dtype=object)
    shown = sorted({str(x) for x in values})
    if column == "date" and shown:
        chips = f'<span class="chip">{escape(shown[0])}</span><span class="chip">a</span><span class="chip">{escape(shown[-1])}</span><span class="chip">{len(shown)} días</span>'
        return f'<div class="chips">{chips}</div>'
    chips = "".join(f'<span class="chip">{escape(x)}</span>' for x in shown[:3])
    more = f'<span class="chip">+{len(shown) - 3}</span>' if len(shown) > 3 else ""
    return f'<div class="chips">{chips or "Sin datos"}{more}</div>'


def _cards(data: _Data) -> dict:
    ops, invoices = data.t["fact_daily_ops"], data.sealed("fact_invoice")
    rules, runs = data.t["fact_validation"], data.t["dim_pipeline_run"]
    ok = runs[runs["status"] == "ok"]
    clean = invoices[invoices["planted_key"] == "none"]
    return {
        "[Facturas]": lambda: _card(_int(ops["invoices"].sum()) if len(ops) else DASH, f"en {len(ops)} días de operación"),
        "[Cargadas solas]": lambda: _card(_int(ops["auto_post"].sum()) if len(ops) else DASH, "cargadas como borrador en el ERP"),
        "[A revisión]": lambda: _card(_int(ops["review"].sum()) if len(ops) else DASH, "con sus motivos en la cola de revisión"),
        "[Paso directo (%)]": lambda: _card(_pct(clean["straight_through"].sum() / len(clean) if len(clean) else None), "de las facturas sin discrepancia (sintético)"),
        "[Segundos por factura]": lambda: _card(_fmt(invoices["seconds"].mean() if len(invoices) else None), "segundos de procesamiento (no es el tiempo de una persona)"),
        "[Reglas bloqueantes fallidas]": lambda: _card(_int((rules["status"] == "falla").sum()), "en las corridas cargadas"),
        "[Última fecha de cálculo]": lambda: _card(_when(ok["as_of"].max()) if len(ok) else DASH, "última corrida terminada bien"),
    }


def _days_label(date_key) -> str:
    text = str(int(date_key))
    return f"{text[4:6]}-{text[6:]}"


def _day_bars(data: _Data, v: Visual) -> str:
    ops = data.t["fact_daily_ops"].sort_values("date_key").tail(DAYS_SHOWN)
    groups = [(_days_label(r.date_key), {"cargadas solas": float(r.auto_post), "a revisión": float(r.review)}) for r in ops.itertuples()]
    width, height = _size(v)
    return svg.grouped_bar_chart(groups, width, height - 14, v.title_es) + _caption(f"Los {len(groups)} días con operación.")


def _p95_line(data: _Data, v: Visual) -> str:
    ops = data.t["fact_daily_ops"].sort_values("date_key").tail(DAYS_SHOWN)
    width, height = _size(v)
    points = [(float(i), float(r.seconds_p95)) for i, r in enumerate(ops.itertuples()) if not _missing(r.seconds_p95)]
    return svg.line_chart({"p95 del día": points} if points else {}, width, height - 14, v.title_es) + _caption("Segundos de procesamiento por factura, día por día.")


def _review_by_reason(data: _Data, v: Visual) -> str:
    invoices = data.sealed("fact_invoice")
    counts = invoices[invoices["decision"] == "review"].groupby("first_reason_key").size().sort_values(ascending=False)
    width, height = _size(v)
    return svg.bar_chart([(data.reason_es.get(k, k), float(n)) for k, n in counts.items()], width, height - 14, v.title_es) + _caption("Facturas de la prueba (sintético) por su primer motivo.")


def _planted_by_reason(data: _Data) -> str:
    invoices = data.sealed("fact_invoice")
    counts = invoices.groupby(["planted_key", "first_reason_key"]).size().reset_index(name="n")
    expected = dict(zip(data.t["dim_planted"]["planted_key"], data.t["dim_planted"]["expected_reason_key"], strict=True))
    counts["order"] = counts["planted_key"].map(lambda k: (k != "none", k))
    counts = counts.sort_values("order")
    rows = [[data.planted_es.get(r.planted_key, r.planted_key), data.reason_es.get(r.first_reason_key, r.first_reason_key), "sí" if expected.get(r.planted_key) == r.first_reason_key else "no", _int(r.n)]
            for r in counts.head(TABLE_ROWS + 6).itertuples()]
    return _table_html(["Clase sembrada", "Primer motivo", "Es el esperado", "Facturas"], rows, {3}) + _caption("La diagonal es lo esperado; las filas son las de la prueba (sintético).")


def _review_queue(data: _Data) -> str:
    invoices = data.sealed("fact_invoice")
    review = invoices[invoices["decision"] == "review"]
    if len(review):
        review = review[review["date_key"] == review["date_key"].max()]
    rows = [[r.invoice_key, data.supplier_es.get(r.supplier_key, r.supplier_key), data.reason_es.get(r.first_reason_key, r.first_reason_key), _int(r.reasons), _fmt(r.total, 2)]
            for r in review.sort_values("invoice_key").head(TABLE_ROWS).itertuples()]
    return _table_html(["Factura", "Proveedor", "Primer motivo", "Motivos", "Total"], rows, {3, 4}) + _caption("Las facturas a revisión del último día evaluado (sintético).")


def _field_bars(data: _Data, v: Visual) -> str:
    fields = data.sealed("fact_field_read").groupby("field_key")[["correct", "leaves"]].sum()
    width, height = _size(v)
    values = [(data.field_es.get(k, k), float(r["correct"] / r["leaves"] * 100)) for k, r in fields.iterrows() if r["leaves"]]
    return svg.bar_chart(values, width, height - 14, v.title_es, unit="%") + _caption("Hojas que se normalizaron a un valor; no dice si es el verdadero.")


def _exact_by_cohort(data: _Data, v: Visual) -> str:
    invoices = data.sealed("fact_invoice")
    groups = []
    for cohort, part in invoices.groupby("cohort_key"):
        sums = part.groupby("scan_key")[["lines_exact", "lines"]].sum()
        groups.append((data.cohort_es.get(cohort, cohort), {data.scan_es.get(k, k): float(r["lines_exact"] / r["lines"] * 100) for k, r in sums.iterrows() if r["lines"]}))
    width, height = _size(v)
    return svg.grouped_bar_chart(groups, width, height - 14, v.title_es) + _caption("Líneas exactas (%) contra la verdad sintética.")


def _qr_bars(data: _Data, v: Visual) -> str:
    reads = data.sealed("fact_qr_read")
    groups = []
    for size, part in reads.groupby("qr_cm"):
        sums = part.groupby("scan_key")[["read_ok", "printed"]].sum()
        groups.append((f"{_fmt(size, 1)} cm", {data.scan_es.get(k, k): float(r["read_ok"] / r["printed"] * 100) for k, r in sums.iterrows() if r["printed"]}))
    width, height = _size(v)
    return svg.grouped_bar_chart(groups, width, height - 14, v.title_es) + _caption("QR leídos con el texto exacto (%), por lado impreso y perfil (sintético).")


def _cord_table(data: _Data) -> str:
    cord = data.sealed("fact_cord_eval").sort_values("model_key")
    rows = [[data.model_es.get(r.model_key, r.model_key), _fmt(r.ted_mean, 1), _pct(r.leaf_recall), _int(r.receipts), r.fit_splits] for r in cord.itertuples()]
    return _table_html(["Modelo", "TED medio", "Hojas acertadas", "Recibos", "Ajustado con"], rows, {1, 2, 3}) + _caption("La prueba de CORD (recibos reales), abierta una sola vez en el cierre.")


def _criteria(data: _Data) -> str:
    summary = data.sealed("fact_evaluation_summary").sort_values("criterion_id")
    if summary.empty:
        return _note("La prueba se evalúa con el cierre: los criterios quedan pendientes hasta entonces.")
    rows = [[r.criterion_id, r.name_es, r.threshold_figure_es, r.threshold_es, r.seen_by_research, r.seen_by_planner, "informativo" if _missing(r.passed) else ("sí" if r.passed else "no"), r.verdict_es]
            for r in summary.itertuples()]
    return _table_html(["Criterio", "Nombre", "Lo que prueba el umbral", "Umbral", "Investigación", "Plan", "Cumple", "Veredicto"], rows)


def _baseline_bars(data: _Data, v: Visual) -> str:
    base = data.sealed("fact_baseline")
    groups = [(data.baseline_es.get(k, k), {data.cohort_es.get(r.cohort_key, r.cohort_key): float(r.bad_post_rate) * 100 for r in part.itertuples() if not _missing(r.bad_post_rate)})
              for k, part in base.groupby("baseline_key")]
    width, height = _size(v)
    return svg.grouped_bar_chart(groups, width, height - 14, v.title_es) + _caption("Tasa de carga mala (%) de cada línea sobre las mismas facturas (sintético).")


def _choice_table(data: _Data) -> str:
    choice = data.t["fact_choice"].sort_values("epoch")
    rows = [[_int(r.epoch), _fmt(r.cord_val_ted, 1), _pct(r.val_known_line_exact), _pct(r.val_new_line_exact), "sí" if r.kept else "no", "sí" if r.chosen else "no", r.fit_splits] for r in choice.itertuples()]
    return _table_html(["Época", "TED en CORD", "Líneas (conocidos)", "Líneas (diseño nuevo)", "Conserva CORD", "Elegida", "Ajustada con"], rows, {1, 2, 3})


def _rules(data: _Data) -> str:
    rules = data.t["fact_validation"].sort_values(["run_key", "rule_id"])
    return _table_html(["Corrida", "Regla", "Nombre", "Severidad", "Archivos", "Estado"],
                       [[r.run_key, r.rule_id, r.name_es, r.severity, _int(r.files_affected), r.status] for r in rules.head(TABLE_ROWS).itertuples()], {4})


def _runs(data: _Data) -> str:
    runs = data.t["dim_pipeline_run"].sort_values("as_of", ascending=False)
    return _table_html(["Corrida", "Entrega", "Instante de cálculo", "Estado", "Facturas", "Mensaje"],
                       [[r.run_key, r.delivery_id or DASH, _when(r.as_of), r.status, _int(r.invoices), r.message or ""] for r in runs.head(TABLE_ROWS).itertuples()], {4})


def _quarantine(data: _Data) -> str:
    ops = data.t["fact_daily_ops"].sort_values("date_key").tail(TABLE_ROWS)
    return _table_html(["Día", "Archivos en cuarentena", "Facturas"], [[_days_label(r.date_key), _int(r.quarantined), _int(r.invoices)] for r in ops.itertuples()], {1, 2})


NOT_FILTERED_MARK = NOT_FILTERED[:NOT_FILTERED.index("{")]   # " (no lo filtra": what every note starts with, before its fields


def _bare_title(title: str) -> str:
    """A visual's title without the note that says what its slicers do not filter (``semantic_model.NOT_FILTERED``): the key of its preview."""
    return title.split(NOT_FILTERED_MARK, 1)[0]


def _makers(data: _Data, v: Visual) -> dict[str, Callable[[], str]]:
    """The drawing of each visual that is not a slicer or a card, by its title without the not-filtered note."""
    return {
        "Facturas por día: cargadas solas y a revisión": lambda: _day_bars(data, v),
        "Segundos del percentil 95, día por día": lambda: _p95_line(data, v),
        "Facturas a revisión por primer motivo": lambda: _review_by_reason(data, v),
        "Clase sembrada por primer motivo (la diagonal es lo esperado)": lambda: _planted_by_reason(data),
        "Cola de revisión: facturas a revisión con sus motivos": lambda: _review_queue(data),
        "Recuerdo por campo": lambda: _field_bars(data, v),
        "Líneas exactas por cohorte y perfil": lambda: _exact_by_cohort(data, v),
        "QR leídos por tamaño impreso, perfil y lector": lambda: _qr_bars(data, v),
        "TED de CORD del elegido y del checkpoint": lambda: _cord_table(data),
        "Criterios C01-C10": lambda: _criteria(data),
        "Carga mala del flujo frente a B0-B3": lambda: _baseline_bars(data, v),
        "La elección de la época en la validación": lambda: _choice_table(data),
        "Reglas de validación": lambda: _rules(data),
        "Corridas": lambda: _runs(data),
        "Archivos en cuarentena por día": lambda: _quarantine(data),
    }


def _preview(data: _Data, v: Visual) -> str:
    """The body of a visual: its drawing or figure from the exported tables."""
    if v.kind == "slicer":
        return _slicer(data, v.fields[0])
    if v.kind == "card":
        maker = _cards(data).get(v.fields[0])
        return maker() if maker else _note("Se calcula en Power BI.")
    maker = _makers(data, v).get(_bare_title(v.title_es))
    return maker() if maker else _note("Se calcula en Power BI.")


# --- sections -----------------------------------------------------------------------------------------------------------------


def _steps(model: SemanticModel) -> str:
    steps = [
        "Instale Power BI Desktop (gratuito, solo para Windows) y abra un informe en blanco.",
        "Cree un parámetro de texto llamado <code>CarpetaSalidas</code> con la ruta de <code>outputs\\powerbi</code>.",
        f"Cree una consulta por tabla ({len(model.tables)}) con el código de la sección «Power Query»; deje el nombre de la consulta igual al de la tabla.",
        f"En la vista de modelo, cree las {len(model.relationships)} relaciones de la tabla «Relaciones»: uno a varios (1:*), filtro único de la dimensión al hecho, todas activas. "
        "Los hechos sin clave compartida (<code>fact_choice</code>, <code>fact_evaluation_summary</code>) no tienen relación: alimentan sus propios visuales, y <code>fact_invoice_reason</code> "
        "se relaciona solo con los motivos (no hay una dimensión de facturas).",
        "Marque <code>dim_date</code> como tabla de fechas con la columna <code>date</code>; ordene <code>dim_date[weekday_es]</code> por <code>date</code>.",
        "Cree una tabla vacía llamada <code>_Medidas</code> y agregue en ella las medidas de la sección «Medidas DAX», con su formato.",
        f"Cree las {len(model.pages)} páginas a 1280 × 720 px y ubique cada visual según la grilla de {PAGE_COLUMNS} columnas por {PAGE_ROWS} filas; ponga en cada una la leyenda «{LEGEND}».",
        "Guarde el informe como <code>.pbix</code>. Cuando corra el flujo, «Actualizar» vuelve a leer los mismos archivos.",
    ]
    return "<ol>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>"


def _diagram(model: SemanticModel) -> str:
    """Related dimensions above, the facts with relationships in the middle, the run table below, and the tables without relationships last."""
    width, height = 1500, 700
    related = {r.dim_table for r in model.relationships} | {r.fact_table for r in model.relationships}
    top = [t.name for t in model.tables if t.kind == "dimension" and t.name in related and t.name != "dim_pipeline_run"]
    bottom = [t.name for t in model.tables if t.name == "dim_pipeline_run" and t.name in related]
    facts = [t.name for t in model.tables if t.kind == "fact" and t.name in related]
    alone = [t.name for t in model.tables if t.name not in related]
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for names, y, w, h in ((top, 24, 150, 46), (facts, 250, 150, 56), (bottom, 420, 170, 46), (alone, 590, 150, 46)):
        w = min(w, (width - 16 * (len(names) + 1)) / max(len(names), 1))
        gap = (width - w * len(names)) / (len(names) + 1) if names else 0
        for i, name in enumerate(names):
            boxes[name] = (gap + i * (w + gap), y, w, h)
    dims = sum(1 for t in model.tables if t.kind == "dimension")
    nfacts = sum(1 for t in model.tables if t.kind == "fact")
    out = [f'<svg class="model-svg" viewBox="0 0 {width} {height}" role="img" aria-label="Diagrama del modelo en estrella: {dims} dimensiones y {nfacts} hechos">',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/></marker></defs>']
    for r in model.relationships:
        dx, dy, dw, dh = boxes[r.dim_table]
        fx, fy, fw, fh = boxes[r.fact_table]
        up = r.dim_table in top
        x1, y1 = dx + dw / 2, (dy + dh if up else dy)
        x2, y2 = fx + fw / 2, (fy if up else fy + fh)
        out.append(f'<line class="rel" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" marker-end="url(#arrow)">'
                   f'<title>{escape(r.rel_id)}: {escape(r.dim_table)}[{escape(r.dim_column)}] 1 → * {escape(r.fact_table)}[{escape(r.fact_column)}]</title></line>')
        sign = 1 if up else -1
        out.append(f'<text class="card" x="{x1 + 3:.1f}" y="{y1 + sign * 13:.1f}">1</text><text class="card" x="{x2 + 3:.1f}" y="{y2 - sign * 6:.1f}">*</text>')
    for name, (x, y, w, h) in boxes.items():
        table = model.table(name)
        size = ' font-size="9"' if len(name) > 15 else ""
        key = ", ".join(table.key) if table.kind == "dimension" else f"{len(table.columns)} columnas"
        out.append(f'<rect class="box {"dim" if table.kind == "dimension" else "fact"}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="6"/>'
                   f'<text x="{x + w / 2:.1f}" y="{y + h / 2 - 2:.1f}" text-anchor="middle"{size}>{escape(name)}</text>'
                   f'<text class="small" x="{x + w / 2:.1f}" y="{y + h / 2 + 12:.1f}" text-anchor="middle">{escape(key)}</text>')
    out.append('<text class="small" x="8" y="570">Sin relaciones: alimentan sus propios visuales.</text>')
    out.append(f'<text class="small" x="8" y="{height - 6}">Cada línea va de la dimensión («1») al hecho («*»); la flecha es la dirección del filtro, única y activa.</text></svg>')
    return "".join(out)


def _relationships(model: SemanticModel) -> str:
    rows = [[r.rel_id, f"{r.dim_table}[{r.dim_column}]", "1", f"{r.fact_table}[{r.fact_column}]", "*", "Uno a varios (1:*)",
             "Única: de la dimensión al hecho" if r.cross_filter == "single" else r.cross_filter, "Sí" if r.active else "No"] for r in model.relationships]
    return _table_html(["ID", "Dimensión", "", "Hecho", "", "Cardinalidad", "Dirección del filtro", "Activa"], rows)


def _queries(model: SemanticModel) -> str:
    parts = []
    for t in model.tables:
        steps = f'    Origen = Parquet.Document(File.Contents(CarpetaSalidas & "\\{t.name}.parquet"))'
        last = "Origen"
        dates = [c.name for c in t.columns if c.dtype == "date"]
        if dates:
            casts = ", ".join(f'{{"{d}", type date}}' for d in dates)
            steps += f",\n    Tipos = Table.TransformColumnTypes(Origen, {{{casts}}})"
            last = "Tipos"
        parts.append(f"<h3>{escape(t.name)}</h3><p>{escape(t.description_es)}</p><pre><code>{escape(f'let{chr(10)}{steps}{chr(10)}in{chr(10)}    {last}')}</code></pre>")
    return "".join(parts)


def _measures(model: SemanticModel) -> str:
    parts = []
    for m in model.measures:
        uses = sorted(dax_measure_refs(m.dax))
        used = f" Usa: {', '.join('[' + u + ']' for u in uses)}." if uses else ""
        parts.append(f"<h3>{escape(m.name_es)}</h3><p>{escape(m.description_es)}{escape(used)} Formato: <code>{escape(m.format_string)}</code>.</p>"
                     f"<pre><code>{escape(m.name_es)} =\n{escape(m.dax)}</code></pre>")
    return "".join(parts)


def _page_section(data: _Data, page: Page, number: int) -> str:
    visuals = []
    for v in page.visuals:
        style = f"grid-column:{v.x + 1} / span {v.w};grid-row:{v.y + 1} / span {v.h}"
        visuals.append(f'<section class="visual" style="{style}" aria-label="{escape(v.title_es)}"><header><b>{escape(v.title_es)}</b>'
                       f'<span>{escape(KIND_ES.get(v.kind, v.kind))}</span></header><div class="body">{_preview(data, v)}</div></section>')
    rows = [[KIND_ES.get(v.kind, v.kind), v.title_es, ", ".join(v.fields), f"x {v.x}, y {v.y}, ancho {v.w}, alto {v.h}"] for v in page.visuals]
    return (f'<h3 id="pagina-{number}">Página {number}: {escape(page.name_es)}</h3>'
            f'<div class="canvas-wrap"><div class="canvas" role="group" aria-label="Página {escape(page.name_es)}">{"".join(visuals)}</div></div>'
            f'<details><summary>Posición, tipo y campos de cada visual</summary><div class="scroll placement">'
            f'{_table_html(["Visual", "Título", "Campos", "Posición (celdas)"], rows)}</div></details>')


def _page_section(data: _Data, page: Page, number: int) -> str:
    visuals = []
    for v in page.visuals:
        style = f"grid-column:{v.x + 1} / span {v.w};grid-row:{v.y + 1} / span {v.h}"
        visuals.append(f'<section class="visual" style="{style}" aria-label="{escape(v.title_es)}"><header><b>{escape(v.title_es)}</b>'
                       f'<span>{escape(KIND_ES.get(v.kind, v.kind))}</span></header><div class="body">{_preview(data, v)}</div></section>')
    rows = [[KIND_ES.get(v.kind, v.kind), v.title_es, ", ".join(v.fields), f"x {v.x}, y {v.y}, ancho {v.w}, alto {v.h}"] for v in page.visuals]
    return (f'<h3 id="pagina-{number}">Página {number}: {escape(page.name_es)}</h3><p class="sub">{escape(LEGEND)}</p>'
            f'<div class="canvas-wrap"><div class="canvas" role="group" aria-label="Página {escape(page.name_es)}">{"".join(visuals)}</div></div>'
            f'<details><summary>Posición, tipo y campos de cada visual</summary><div class="scroll placement">'
            f'{_table_html(["Visual", "Título", "Campos", "Posición (celdas)"], rows)}</div></details>')


def _page_html(model: SemanticModel, data: _Data, run: RunMeta) -> str:
    nav = "".join(f'<li><a href="#{key}">{escape(title)}</a></li>' for key, title in SECTIONS)
    pages = "".join(_page_section(data, p, i) for i, p in enumerate(model.pages, start=1))
    dims = sum(1 for t in model.tables if t.kind == "dimension")
    facts = sum(1 for t in model.tables if t.kind == "fact")
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(TITLE)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>{escape(TITLE)}</h1>
<p class="lead">Guía para armar el informe en Power BI Desktop a partir de los archivos Parquet de <code>outputs/powerbi</code>: modelo, relaciones, consultas, medidas y las cinco páginas con una vista previa de cada visual dibujada con las cifras exportadas. {escape(LEGEND)}: las facturas, órdenes, remitos, proveedores y CUIT son sintéticos; los recibos de CORD son reales.</p>
<div class="meta"><span>Fecha de cálculo <b>{escape(run.as_of.isoformat(sep=" "))}</b></span><span>Corrida <code>{escape(run.run_key)}</code></span><span>SHA-256 del artefacto <code>{escape(data.artifact_sha256[:12])}</code></span></div>
<nav aria-label="Secciones"><ul>{nav}</ul></nav>
<h2 id="armado">{escape(SECTIONS[0][1])}</h2>
{_steps(model)}
<div class="warn"><b>Qué se probó y qué no.</b> Las pruebas del proyecto comprueban que los archivos cumplen el modelo (claves únicas, claves foráneas en su dimensión, relaciones de uno a varios con filtro único) y que cada tabla, columna, medida y función que nombra una expresión DAX existe, que las medidas de la prueba piden solo las filas selladas y que cada segmentador alcanza lo que dice filtrar. Ninguna medida se ejecutó: aquí no hay Power BI, y las vistas previas muestran las cifras de las tablas sin segmentadores. El <code>.pbix</code> se arma a mano siguiendo este documento.</div>
<div class="warn"><b>Una cifra de las facturas es sintética y un tiempo es de procesamiento.</b> Toda cifra que sale de las facturas generadas lo dice; «segundos por factura» es el tiempo que tarda el flujo en procesarla, no el tiempo de una persona. La prueba de CORD (recibos reales) se abrió una sola vez, en el cierre.</div>
<h2 id="modelo">{escape(SECTIONS[1][1])}</h2>
<p>{dims} dimensiones y {facts} hechos. Los hechos de la elección y de la evaluación no comparten claves con las dimensiones y alimentan sus propios visuales.</p>
{_diagram(model)}
<h2 id="relaciones">{escape(SECTIONS[2][1])}</h2>
<div class="scroll">{_relationships(model)}</div>
<h2 id="consultas">{escape(SECTIONS[3][1])}</h2>
{_queries(model)}
<h2 id="medidas">{escape(SECTIONS[4][1])}</h2>
<p>Todas van en la tabla <code>_Medidas</code>. Los nombres de las medidas y los textos están en español; las tablas y las columnas, en inglés. Las medidas que leen una tabla con <code>seal_state</code> piden solo las filas selladas: si no, una fila posterior al sello contaría dos veces o vaciaría la cifra.</p>
{_measures(model)}
<h2 id="paginas">{escape(SECTIONS[5][1])}</h2>
<p>Cada página es un lienzo de 1280 × 720 px (16:9) sobre una grilla de {PAGE_COLUMNS} columnas por {PAGE_ROWS} filas; la posición de cada visual está en celdas. Las vistas previas de la evaluación muestran las filas selladas de la prueba y, si no las hay, dicen que están pendientes.</p>
{pages}
<h2 id="fuente">{escape(SECTIONS[6][1])}</h2>
<p>{escape(labels.ATTRIBUTION)}</p>
<p>Datos de la corrida <code>{escape(run.run_key)}</code>, con fecha de cálculo {escape(run.as_of.isoformat(sep=" "))}. Este documento se genera desde <code>dashboard/semantic_model.py</code>; no se edita a mano.</p>
</main>
</body>
</html>
"""


def render_design(model: SemanticModel, parquet_dir: Path, html_path: Path, run_meta: RunMeta) -> Path:
    """Write the HTML design of the report to `html_path` (atomically, through ``files.write_atomic``) from the Parquet files of `parquet_dir` and return the path. The model is checked first: an
    incoherent one writes nothing."""
    problems = validate_model(model)
    if problems:
        raise ValueError(f"el modelo semántico es incoherente: {problems[0]}")
    html = _page_html(model, _Data(Path(parquet_dir), model), run_meta)
    path = write_atomic(Path(html_path), html)
    logger.info("dashboard design written to %s (%d bytes)", path.name, len(html.encode("utf-8")))
    return path
