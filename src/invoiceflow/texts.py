"""The Spanish texts of the criteria and the way numbers are written in them (RF20; AC22; plan sections 16 and 21; decision D19): the names, the thresholds and the verdicts of C01-C10.

Each verdict is built from the figures it tells and **names its own comparison** (the checkpoint, a design the model did not see, B1, the read-back of the ERP); a figure that comes from the synthetic
invoices says so in the sentence. A criterion that cannot be computed says why and is never passed (``cannot``).
"""

from __future__ import annotations

import math
from typing import NamedTuple


class CriterionText(NamedTuple):
    name_es: str
    threshold_es: str          # with the limits of ``[criteria]`` in {braces}
    seen_by_research: str
    seen_by_planner: str
    synthetic: bool            # its figure comes from the synthetic invoices


# id: name, threshold, what the research saw, what the planner saw (requirements.md, 8.1; D19)
CRITERIA: dict[str, CriterionText] = {
    "C01": CriterionText("El ajuste no olvida CORD", "límite inferior del intervalo del 95 % de TED(elegido) − TED(checkpoint) en la prueba de CORD > {c01} puntos", "sí", "sí", False),
    "C02": CriterionText("Lee bien las líneas de proveedores conocidos", "tasa de líneas exactas en test_known ≥ {c02}", "no", "no", True),
    "C03": CriterionText("Lee bien las líneas de un diseño nuevo", "tasa de líneas exactas en test_new ≥ {c03}", "no", "no", True),
    "C04": CriterionText("Lee el QR", "de las facturas impresas con QR intacto, la parte con el texto exacto ≥ {c04}", "sí", "sí", True),
    "C05": CriterionText("No carga mal", "límite superior del intervalo de Wilson de la tasa de carga mala, en toda la prueba, ≤ {c05}", "no", "no", True),
    "C06": CriterionText("Se carga sola la mayoría de las facturas de proveedores conocidos", "paso directo en test_known ≥ {c06}", "no", "no", True),
    "C07": CriterionText("Manda a revisión lo que no cierra", "de las facturas con discrepancia sembrada, la parte con revisión ≥ {c07}", "no", "no", True),
    "C08": CriterionText("El cruce de tres vías evita cargas malas", "límite inferior del intervalo del 95 % de (tasa de carga mala de B1 − la del flujo) > 0", "no", "no", True),
    "C09": CriterionText("Lo cargado es lo decidido", "el 100 % de los borradores leídos de vuelta del ERP coincide campo por campo, y repetir un día no crea ninguna factura nueva", "no", "no", True),
    "C10": CriterionText("Variantes y seguimiento (informativo)", "sin umbral: que esté escrito y sea consistente", "en parte", "en parte", True),
}
_LIMIT_KEYS = {"c01": "c01_min_ted_diff", "c02": "c02_min_line_exact", "c03": "c03_min_line_exact", "c04": "c04_min_qr_read", "c05": "c05_max_bad_post_upper",
               "c06": "c06_min_straight_through", "c07": "c07_min_review_recall"}
LINES_OF = {"known": "de proveedores conocidos", "new": "de proveedores con un diseño nuevo"}


def es(value: float | None, decimals: int = 3, *, percent: bool = False) -> str:
    """A number as the Spanish text shows it: a comma for the decimals, a point for the thousands and «−» for the minus sign; «sin dato» if there is none. With `percent` the number is a
    fraction written as « x %». A number that rounds to zero never carries a minus sign."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "sin dato"
    text = f"{(value * 100 if percent else value):,.{decimals}f}"
    if text.startswith("-") and not any(ch in "123456789" for ch in text):
        text = text[1:]
    text = text.replace(",", "§").replace(".", ",").replace("§", ".").replace("-", "−")
    return f"{text} %" if percent else text


def pct(value: float | None) -> str:
    """A fraction as a percentage with the decimals it needs (none when whole, one otherwise, two under 1 %)."""
    if value is None:
        return "sin dato"
    scaled = value * 100
    if abs(scaled - round(scaled)) < 1e-9:
        return es(value, 0, percent=True)
    return es(value, 2 if abs(scaled) < 1 else 1, percent=True)


def thresholds_from(limits: dict[str, float]) -> dict[str, str]:
    """The threshold of each criterion with its limits from ``[criteria]`` written in."""
    numbers = {key: (es(limits[name], 1) if key == "c01" else pct(limits[name])) for key, name in _LIMIT_KEYS.items()}
    return {cid: text.threshold_es.format(**numbers) for cid, text in CRITERIA.items()}


def cannot(why: str) -> str:
    return f"El criterio no se puede calcular: {why}."


def informative_verdict(variants: int, days: int) -> str:
    return f"Informativo: sin umbral. Las líneas base, el recuerdo por clase sembrada, la lectura por campo, perfil y tamaño del QR y los {days} días de operación están en el resumen ({variants} variantes)."


def verdict(cid: str, passed: bool, **f: object) -> str:
    """The sentence of criterion `cid`, from its figures (see ``evaluation.criteria_of``)."""
    mark = "" if passed else " No se cumple."
    if cid == "C01":
        return (f"En la prueba de CORD ({f['receipts']} recibos), el modelo elegido tiene TED {es(f['chosen'], 1)} y el checkpoint sin ajustar {es(f['checkpoint'], 1)}: la diferencia es {es(f['value'], 1)} "
                f"(intervalo del 95 %, {es(f['low'], 1)} a {es(f['high'], 1)}); el límite inferior debía quedar por encima de {es(f['limit'], 1)} puntos.{mark}")
    if cid in ("C02", "C03"):
        return f"Sobre {f['invoices']} facturas sintéticas {LINES_OF[str(f['cohort'])]}, el {pct(f['rate'])} de las líneas se leyó exacto; el umbral era {pct(f['limit'])}.{mark}"
    if cid == "C04":
        return f"Entre las {f['printed']} facturas sintéticas impresas con QR intacto, el lector leyó el texto exacto de {f['read']}: {pct(f['rate'])}; el umbral era {pct(f['limit'])}.{mark}"
    if cid == "C05":
        return (f"Sobre {f['invoices']} facturas sintéticas, {f['bad']} de {f['posts']} cargas automáticas fue mala; el límite superior del intervalo de Wilson es {pct(f['high'])} "
                f"y el máximo era {pct(f['limit'])}.{mark}")
    if cid == "C06":
        return f"Sobre las facturas sintéticas limpias de proveedores conocidos, el {pct(f['rate'])} ({f['posted']} de {f['clean']}) se cargó solo y bien; el umbral era {pct(f['limit'])}.{mark}"
    if cid == "C07":
        return f"De las {f['planted']} facturas sintéticas con una discrepancia sembrada, {f['review']} de {f['planted']} fueron a revisión ({pct(f['rate'])}); el umbral era {pct(f['limit'])}.{mark}"
    if cid == "C08":
        return (f"Sobre las facturas sintéticas de la prueba, B1 (solo el QR en dos vías) cargó mal el {es(f['b1'], 1, percent=True)} y el flujo el {es(f['flow'], 1, percent=True)}; la diferencia es {es(f['value'] * 100, 1)} puntos "
                f"(intervalo del 95 %, {es(f['low'] * 100, 1)} a {es(f['high'] * 100, 1)}); el límite inferior debía ser mayor que 0.{mark}")
    if cid == "C09":
        return (f"{f['equal']} de {f['posts']} cargas automáticas se leyeron de vuelta del ERP iguales a lo decidido (sintético) y repetir el último día creó {f['repeat']} facturas nuevas; "
                f"debían coincidir todas y no crearse ninguna.{mark}")
    raise KeyError(cid)


# --- what is published beside the sealed sentences (D59) ---------------------------------------------------------------------------------


def c08_per_invoice_verdict(passed: bool, *, b1_bad: int, flow_bad: int, invoices: int, value: float, low: float, high: float) -> str:
    """The sentence the summary tells for the sealed C08. The sealed sentence (``verdict`` above, kept as it is so the seal is rebuilt) writes the bad-post rates per auto-post of B1 and the flow
    beside a difference computed per invoice of the whole test; this one tells only what the seal computed, per invoice, and says where the preregistered difference of rates is (D59)."""
    mark = "" if passed else " No se cumple."
    return (f"Sobre las facturas sintéticas de la prueba, B1 (solo el QR en dos vías) cargó mal {b1_bad} de las {invoices} y el flujo {flow_bad}: por factura, la diferencia es "
            f"{es(value * 100, 1)} puntos (intervalo del 95 %, {es(low * 100, 1)} a {es(high * 100, 1)}); el límite inferior debía ser mayor que 0.{mark} Es la cifra sellada; la diferencia de las tasas "
            "de carga mala que fija el criterio, por carga, está entre los análisis posteriores al sello (D59).")


def threshold_figure(criterion: dict) -> str:
    """What the threshold of `criterion` tests, as the summary's column shows it (A1 of the evaluation, D59): the limit of the interval for C01 (points of TED), C05 (Wilson's upper limit) and C08
    (points per invoice), the rate itself for the rest; «sin umbral» for C10 and «sin dato» for a criterion that cannot be computed."""
    cid = criterion["criterion_id"]
    if criterion.get("passed") is None and criterion.get("value") is None:
        return "sin umbral"
    if cid == "C01":
        text = "sin dato" if criterion.get("ci_low") is None else f"{es(criterion['ci_low'], 2)} puntos (límite inferior)"
    elif cid == "C05":
        text = "sin dato" if criterion.get("ci_high") is None else f"{es(criterion['ci_high'], 3)} (límite superior de Wilson)"
    elif cid == "C08":
        text = "sin dato" if criterion.get("ci_low") is None else f"{es(criterion['ci_low'] * 100, 1)} puntos (límite inferior, por factura)"
    else:
        text = es(criterion["value"], 3) if criterion.get("value") is not None else "sin dato"
    return f"{text} (sintético)" if criterion.get("synthetic") and text != "sin dato" else text


def c08_preregistered_line(analysis: dict) -> str:
    """The line of the summary that tells C08 as requirements 8.1 define it, from ``evaluation.post_seal_analysis``."""
    pre = analysis["c08_preregistered"]
    b1, flow = pre["b1"], pre["flow"]
    if pre["value"] is None:
        return f"- **C08 como lo fija el preregistro**: no se puede calcular, porque B1 cargó {b1['posts']} facturas y el flujo {flow['posts']}, y una tasa sin cargas no existe (sintético)."
    verdict = "se cumple también así" if pre["passed"] else "así no se cumple"
    return (f"- **C08 como lo fija el preregistro** (requirements, sección 8.1: la tasa de carga mala es cargas malas sobre facturas con `auto_post`): B1 cargó mal {b1['bad']} de sus {b1['posts']} "
            f"cargas ({es(b1['rate'], 1, percent=True)}) y el flujo {flow['bad']} de {flow['posts']} ({es(flow['rate'], 1, percent=True)}); la diferencia de las tasas es {es(pre['value'] * 100, 1)} "
            f"puntos (intervalo del 95 %, {es(pre['ci_low'] * 100, 1)} a {es(pre['ci_high'] * 100, 1)}, remuestreo pareado por factura): {verdict} (sintético). El veredicto sellado de C08 no cambia; "
            "el texto sellado, que pone esas tasas por carga al lado de la diferencia por factura, queda en el sello y en el almacén.")


def b1_read_order_line(analysis: dict) -> str:
    """The line of the summary that tells B1 as D48 says, with the order number the model read (D60), from ``evaluation.post_seal_analysis``."""
    read = analysis["b1_read_order"]
    every, per_invoice, per_post = read["baselines"]["all"], read["c08_per_invoice"], read["c08_preregistered"]
    if per_invoice["value"] is None or per_post["value"] is None:
        return f"- **B1 con la orden que leyó el modelo** (D48, D60): carga {every['auto_post']} facturas y {every['bad_post']} mal; C08 no se puede calcular así (sintético)."
    verdicts = ["se cumple" if p["passed"] else "no se cumple" for p in (per_invoice, per_post)]
    return (f"- **B1 con la orden que leyó el modelo** (D48, D60; la B1 sellada tomó la orden de la verdad): carga {every['auto_post']} facturas y {every['bad_post']} mal "
            f"({es(every['bad_post_rate'], 1, percent=True)} de sus cargas). C08 por factura, como el sello: {es(per_invoice['value'] * 100, 1)} puntos (intervalo del 95 %, "
            f"{es(per_invoice['ci_low'] * 100, 1)} a {es(per_invoice['ci_high'] * 100, 1)}), {verdicts[0]}; por carga, como el preregistro: {es(per_post['value'] * 100, 1)} puntos "
            f"({es(per_post['ci_low'] * 100, 1)} a {es(per_post['ci_high'] * 100, 1)}), {verdicts[1]} (sintético).")
