"""The figures of the documentation, drawn only from the verified files of the site (RF26; plan, sections 21 and 25).

``write_figures`` reads the five JSON files through ``site_export.read_site``, which refuses a folder that is not one export (``SiteSetError``): nothing is drawn from a warehouse, a Parquet file or a
number typed here, so a figure in the README can only say what the site says. ``figure_data`` returns, for each figure, the numbers it plots; the drawing functions take nothing else. Every figure says
what it shows in Spanish, a figure of the synthetic invoices says «(sintético)» in its title, and while the test is not sealed it says that the test is evaluated at the close. Each PNG is written
atomically and is the same bytes every time (no date or version is written in it).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from invoiceflow import labels  # noqa: E402
from invoiceflow.files import write_atomic  # noqa: E402
from invoiceflow.site_export import SiteFiles, read_site  # noqa: E402

logger = logging.getLogger(__name__)

FIGURES = ("operation", "review_reasons", "lines", "qr", "baselines", "choice", "cord")
DPI = 110
COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
PENDING = "La prueba se evalúa con el cierre"
COHORT_ES = {"test_known": "conocidos", "test_new": "diseño nuevo"}


def _none(values: list) -> list[float]:
    return [float("nan") if v is None else float(v) for v in values]


def figure_data(site: SiteFiles) -> dict[str, dict[str, Any]]:
    """The numbers each figure plots, taken from the verified files and from nothing else."""
    summary, algorithms, operation = site.summary, site.algorithms, site.operation
    sealed = summary["evaluation_status"] == "sealed"
    reasons: dict[str, int] = {}
    for day in operation["days"]:
        for reason, n in day["by_reason"].items():
            reasons[reason] = reasons.get(reason, 0) + n
    ordered = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    qr: dict[tuple[float, str], list[int]] = {}
    for r in algorithms["qr"]:
        cell = qr.setdefault((r["qr_cm"], r["scan"]), [0, 0])
        cell[0] += r["read_ok"]
        cell[1] += r["printed"]
    sizes = sorted({size for size, _ in qr})
    scans = [s for s in labels.SCAN_LABELS_ES if any(scan == s for _, scan in qr)]
    baselines = [b for b in algorithms["baselines"] if b["bad_post_rate"] is not None]
    cord = algorithms["cord"]
    h = summary["headline"]
    return {
        "operation": {"date": [d["date"][5:] for d in operation["days"]], "auto_post": [d["auto_post"] for d in operation["days"]], "review": [d["review"] for d in operation["days"]]},
        "review_reasons": {"reason": [labels.REASON_TEXTS[r][0] for r, _ in ordered], "count": [n for _, n in ordered]},
        "lines": {"cohort": ["conocidos", "diseño nuevo"], "rate": [h["line_exact_known"], h["line_exact_new"]]},
        "qr": {"size": sizes, "scan": [labels.SCAN_LABELS_ES[s] for s in scans],
               "rate": {labels.SCAN_LABELS_ES[s]: [qr[(size, s)][0] / qr[(size, s)][1] if (size, s) in qr and qr[(size, s)][1] else None for size in sizes] for s in scans}},
        "baselines": {"scope": "test" if sealed and baselines else "pending", "baseline": [b["label_es"] for b in baselines], "cohort": [COHORT_ES.get(b["cohort"], b["cohort"]) for b in baselines],
                      "rate": [b["bad_post_rate"] for b in baselines]},
        "choice": {"epoch": [c["epoch"] for c in algorithms["choice"]], "cord_val_ted": [c["cord_val_ted"] for c in algorithms["choice"]], "exact_known": [c["exact_known"] for c in algorithms["choice"]],
                   "exact_new": [c["exact_new"] for c in algorithms["choice"]], "chosen": [c["chosen"] for c in algorithms["choice"]],
                   "fit_splits": sorted({c["fit_splits"] for c in algorithms["choice"]})},
        "cord": {"scope": "test" if cord else "pending", "model": [labels.MODEL_LABELS_ES[m] for m in (cord or {}).get("models", {})], "ted": [v["ted"] for v in (cord or {}).get("models", {}).values()]},
    }


def _png(fig: plt.Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return buffer.getvalue()


def _empty(ax: plt.Axes, text: str) -> None:
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])


def _operation(d: dict) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.8))
    if not d["date"]:
        _empty(ax, "Sin días de operación")
    else:
        ax.bar(d["date"], d["auto_post"], color=COLORS[0], label="cargadas solas")
        ax.bar(d["date"], d["review"], bottom=d["auto_post"], color=COLORS[1], label="a revisión")
        ax.set_ylabel("Facturas por día")
        ax.legend(frameon=False)
        ax.set_title("Cada día: lo que se carga solo y lo que va a revisión (sintético)", fontsize=10)
    return _png(fig)


def _review_reasons(d: dict) -> bytes:
    fig, ax = plt.subplots(figsize=(7, max(2.6, 0.35 * len(d["reason"]) + 1.2)))
    if not d["reason"]:
        _empty(ax, "Ninguna factura fue a revisión")
    else:
        ax.barh(d["reason"], d["count"], color=COLORS[1])
        ax.invert_yaxis()
        ax.set_xlabel("Facturas")
        ax.set_title("Por qué una factura va a revisión: su primer motivo (sintético)", fontsize=10)
    return _png(fig)


def _lines(d: dict) -> bytes:
    fig, ax = plt.subplots(figsize=(5, 3.6))
    if all(v is None for v in d["rate"]):
        _empty(ax, PENDING)
    else:
        ax.bar(d["cohort"], [v * 100 if v is not None else 0 for v in d["rate"]], color=COLORS[:2])
        ax.set_ylim(0, 100)
        ax.set_ylabel("Líneas leídas exactas (%)")
        ax.set_title("Un diseño que el modelo no vio: la lectura baja (sintético)", fontsize=10)
    return _png(fig)


def _qr(d: dict) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.8))
    if not d["size"]:
        _empty(ax, PENDING)
    else:
        width = 0.8 / max(len(d["scan"]), 1)
        for i, (scan, rates) in enumerate(d["rate"].items()):
            ax.bar([k + i * width for k in range(len(d["size"]))], [(r or 0) * 100 for r in rates], width=width, color=COLORS[i], label=scan)
        ax.set_xticks([k + 0.4 - width / 2 for k in range(len(d["size"]))], [f"{s:g} cm".replace(".", ",") for s in d["size"]])
        ax.set_ylim(0, 100)
        ax.set_ylabel("QR leídos con el texto exacto (%)")
        ax.legend(frameon=False, title="perfil de escaneo")
        ax.set_title("El QR por tamaño impreso y perfil de escaneo (sintético)", fontsize=10)
    return _png(fig)


def _baselines(d: dict) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.8))
    if d["scope"] == "pending":
        _empty(ax, PENDING)
    else:
        names = list(dict.fromkeys(d["baseline"]))
        cohorts = list(dict.fromkeys(d["cohort"]))
        width = 0.8 / len(cohorts)
        for i, cohort in enumerate(cohorts):
            rates = [next((r * 100 for b, c, r in zip(d["baseline"], d["cohort"], d["rate"], strict=True) if b == name and c == cohort), 0.0) for name in names]
            ax.bar([k + i * width for k in range(len(names))], rates, width=width, color=COLORS[i], label=cohort)
        ax.set_xticks([k + 0.4 - width / 2 for k in range(len(names))], names, rotation=20, ha="right", fontsize=8)
        ax.set_ylabel("Cargas malas (% de lo cargado solo)")
        ax.legend(frameon=False)
        ax.set_title("La carga mala del flujo frente a las líneas base, sobre las mismas facturas (sintético)", fontsize=9)
    return _png(fig)


def _choice(d: dict) -> bytes:
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(6, 5), sharex=True)
    if not d["epoch"]:
        _empty(top, "Sin candidatas")
        _empty(bottom, "")
    else:
        top.plot(d["epoch"], d["cord_val_ted"], marker="o", color=COLORS[0])
        top.set_ylabel("TED en la validación de CORD")
        bottom.plot(d["epoch"], [v * 100 for v in _none(d["exact_known"])], marker="o", color=COLORS[2], label="proveedores conocidos")
        bottom.plot(d["epoch"], [v * 100 for v in _none(d["exact_new"])], marker="o", color=COLORS[1], label="diseño nuevo")
        bottom.set_ylabel("Líneas exactas (%)")
        bottom.set_xlabel("Época candidata")
        bottom.legend(frameon=False)
        top.set_title(f"La elección de la época: recibos de CORD y facturas (sintético); ajustado con {', '.join(d['fit_splits'])}", fontsize=9)
    return _png(fig)


def _cord(d: dict) -> bytes:
    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    if d["scope"] == "pending":
        _empty(ax, PENDING)
    else:
        ax.barh(d["model"], d["ted"], color=COLORS[:2])
        ax.invert_yaxis()
        ax.set_xlabel("TED medio (puntos) en la prueba de CORD")
        ax.set_title("La prueba de CORD, abierta una sola vez: recibos reales", fontsize=10)
    return _png(fig)


_DRAW = {"operation": _operation, "review_reasons": _review_reasons, "lines": _lines, "qr": _qr, "baselines": _baselines, "choice": _choice, "cord": _cord}


def figure_pngs(data: dict[str, dict[str, Any]]) -> dict[str, bytes]:
    """The PNG of each figure from its numbers."""
    return {name: _DRAW[name](data[name]) for name in FIGURES}


def write_figures(site_dir: Path, out_dir: Path) -> list[Path]:
    """Draw the figures from the verified files of `site_dir` into `out_dir` (``<name>.png``, each atomic) and return their paths. A folder that is not one export is a ``SiteSetError`` and nothing
    is drawn or written."""
    pictures = figure_pngs(figure_data(read_site(site_dir)))
    paths = [write_atomic(Path(out_dir) / f"{name}.png", pictures[name]) for name in FIGURES]
    logger.info("figures: %d drawn from %s into %s", len(paths), Path(site_dir).name, Path(out_dir).name)
    return paths
