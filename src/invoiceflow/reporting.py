"""The evaluation as a task and the summary that tells it (RF20; AC22, AC24; plan section 16; decisions D19, D21): ``evaluate`` runs once at the close and writes the sealed rows and the test part of the
seal; ``build_summary`` reads what there is, in this order of trust: the sealed rows of the warehouse, else the criteria **rebuilt from the seal** (a warehouse without the sealed rows: a new one, a quick
copy, the site's build), else nothing yet (before the close the summary says when the test is evaluated and shows the validation figures with the partitions their model saw).

Beside the seal, and never in it, the close writes ``post_seal_analysis.json`` (``evaluation.post_seal_analysis``, D59): what was found after the seal and is published with its measured impact
next to the sealed figures. The summary tells C08 with the sentence of what the seal computed (per invoice) and keeps the sealed sentence as ``sealed_verdict_es``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from invoiceflow import evaluation, texts
from invoiceflow.config import PipelineConfig, SealedConfig
from invoiceflow.evaluation import Criterion, EvaluationResult
from invoiceflow.files import write_atomic
from invoiceflow.registry import RunMeta, SealError, check_test_evaluation, config_fingerprint, read_seal, write_test_seal
from invoiceflow.synth.invoices import InvoiceTruth
from invoiceflow.synth.master import Master

logger = logging.getLogger(__name__)

POST_SEAL_FILE = "post_seal_analysis.json"
CORD_PREDICTIONS_FILE = "cord_test_predictions.json"
CRITERION_COLUMNS = ("criterion_id", "name_es", "value", "baseline_value", "ci_low", "ci_high", "threshold_es", "passed", "seen_by_research", "seen_by_planner", "synthetic", "verdict_es")


def criterion_dict(c: Criterion) -> dict[str, Any]:
    return {name: getattr(c, name) for name in CRITERION_COLUMNS}


def _insert(conn: sqlite3.Connection, table: str, rows: Sequence[Mapping[str, Any]], state: str) -> None:
    for row in rows:
        values = {**row, "seal_state": state}
        if "passed" in values and values["passed"] is not None:
            values["passed"] = int(values["passed"])
        if "synthetic" in values:
            values["synthetic"] = int(bool(values["synthetic"]))
        names = ", ".join(values)
        conn.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join(':' + n for n in values)})", values)


def store_result(conn: sqlite3.Connection, result: EvaluationResult, criteria: Sequence[Criterion], cord_rows: Sequence[Mapping[str, Any]], state: str) -> dict[str, int]:
    """Write the rows of the evaluation in one transaction with the seal state of `state` (``first`` and ``repeat`` are the sealed ones, ``post_seal`` the apart one). A repetition finds its sealed rows
    already there and writes none of them; what is sealed is never replaced."""
    seal_state = "post_seal" if state == "post_seal" else "sealed"
    sealed_there = conn.execute("SELECT count(*) FROM eval_criteria WHERE seal_state = 'sealed'").fetchone()[0] > 0
    if seal_state == "sealed" and sealed_there:
        return {}
    if seal_state == "post_seal":
        conn.execute("DELETE FROM eval_criteria WHERE seal_state = 'post_seal'")
        for table in ("eval_invoice", "eval_field", "eval_baseline", "eval_cord", "eval_variants"):
            conn.execute(f"DELETE FROM {table} WHERE seal_state = 'post_seal'")
    conn.execute("BEGIN IMMEDIATE")
    try:
        _insert(conn, "eval_criteria", [criterion_dict(c) for c in criteria], seal_state)
        _insert(conn, "eval_invoice", result.invoices, seal_state)
        _insert(conn, "eval_field", result.fields, seal_state)
        _insert(conn, "eval_baseline", result.baselines, seal_state)
        _insert(conn, "eval_cord", cord_rows, seal_state)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return {"eval_criteria": len(criteria), "eval_invoice": len(result.invoices), "eval_field": len(result.fields), "eval_baseline": len(result.baselines), "eval_cord": len(cord_rows)}


def evaluate(conn: sqlite3.Connection, cfg: PipelineConfig, view: SealedConfig, run: RunMeta, master: Master, truths: Sequence[InvoiceTruth], *, seal_path: Path, chosen: Any, checkpoint: Any,
             closing_run: bool = True, repeat_new: int | None = None, checkpoint_trees: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The task ``evaluate`` of the close: the test is evaluated **once**. The state of the seal is decided first (``EvaluationRefusedError`` before anything is read or written); then CORD's test split is
    opened by ``evaluation.evaluate_cord``, the synthetic test is joined to what was issued, the rows are stored and the test part of the seal is written (or reproduced)."""
    state = check_test_evaluation(seal_path, view)
    predictions: list[dict[str, Any]] = []
    cord_figures, cord_rows = evaluation.evaluate_cord(cfg, view, chosen, checkpoint, closing_run=closing_run, predictions=predictions)
    result = evaluation.evaluate_test(conn, view, master, truths, repeat_new=repeat_new, checkpoint_trees=checkpoint_trees, cord=cord_figures)
    criteria = evaluation.evaluate_criteria(result, view)
    rows = store_result(conn, result, criteria, cord_rows, state)
    launch = (read_seal(seal_path) or {})["launch"]
    part = {"run_key": run.run_key, "as_of": run.as_of.strftime("%Y-%m-%d %H:%M:%S"), "config_sha256": config_fingerprint(view), "criteria": [c.seal_entry() for c in criteria],
            "metrics": result.figures, "cord": {"chosen_epoch": launch["chosen_epoch"], **cord_figures}, "baselines": result.figures["baselines"],
            "operation": {"days": result.figures["days"], "invoices": len(result.invoices)}}
    outcome = write_test_seal(seal_path, part)
    logger.info("evaluate: state %s, seal %s, rows %s", state, outcome, rows)
    if state in ("first", "repeat"):
        write_post_seal_analysis(seal_path, evaluation.post_seal_analysis(conn, view, master, truths), config_fingerprint(view))
        write_cord_predictions(seal_path, predictions)
    return {"state": state, "seal": outcome, "criteria": criteria, "rows": rows, "result": result}


def post_seal_path(seal_path: Path) -> Path:
    """Where the analyses after the seal live: beside the seal, in a file of their own."""
    return Path(seal_path).with_name(POST_SEAL_FILE)


def write_post_seal_analysis(seal_path: Path, analysis: Mapping[str, Any], config_sha256: str) -> Path:
    """Write the analyses after the seal (atomically), bound to the bytes of the seal they were computed beside: a repetition rewrites the same file and the seal is never touched."""
    document = {**analysis, "config_sha256": config_sha256, "seal_sha256": hashlib.sha256(Path(seal_path).read_bytes()).hexdigest()}
    return write_atomic(post_seal_path(seal_path), json.dumps(document, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def write_cord_predictions(seal_path: Path, predictions: Sequence[Mapping[str, Any]]) -> Path:
    """The trees the chosen model and the checkpoint read on each receipt of CORD's test at the close, with the receipt's truth, beside the seal (A10 of the evaluation, D63): CORD's receipts are
    real (``synthetic`` false) and carry their attribution."""
    from invoiceflow.labels import ATTRIBUTION

    document = {"synthetic": False, "attribution": ATTRIBUTION, "receipts": [dict(p) for p in predictions]}
    return write_atomic(Path(seal_path).with_name(CORD_PREDICTIONS_FILE), json.dumps(document, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def read_post_seal_analysis(seal_path: Path) -> dict[str, Any] | None:
    """The analyses after the seal, or None when there are none or they were computed beside another seal (a stale file is never published)."""
    path, seal = post_seal_path(seal_path), Path(seal_path)
    if not path.is_file() or not seal.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("seal_sha256") != hashlib.sha256(seal.read_bytes()).hexdigest():
        logger.warning("%s was computed beside another seal: it is not published", path.name)
        return None
    return document


# --- the summary ------------------------------------------------------------------------------------------------------------------------


def _rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = conn.execute(f"SELECT {', '.join(CRITERION_COLUMNS)} FROM eval_criteria WHERE seal_state = 'sealed' ORDER BY criterion_id")
    return [{**dict(zip(CRITERION_COLUMNS, row, strict=True)), "passed": None if row[7] is None else bool(row[7]), "synthetic": bool(row[10])} for row in cursor]


def close_day(view: SealedConfig) -> str:
    return (view.calendar.test_last_day + timedelta(days=1)).isoformat()


def build_summary(conn: sqlite3.Connection, view: SealedConfig, *, seal_path: Path) -> dict[str, Any]:
    """What the README, the model card, the dashboard and the site tell: ``source`` is ``warehouse`` (the sealed rows are there), ``seal`` (C01-C09 rebuilt from the seal, each one compared with its own
    entry to 6 decimals) or ``pending`` (the test is not evaluated yet). The validation figures come from the launch part of the seal, each with the partitions its model saw."""
    document = read_seal(seal_path)
    summary: dict[str, Any] = {"synthetic": True, "close_day": close_day(view), "config_sha256": config_fingerprint(view), "source": "pending", "criteria": [], "validation": {}}
    if document is not None:
        launch = document["launch"]
        summary["validation"] = {"chosen_epoch": launch["chosen_epoch"], "choice": launch["choice"], "checkpoint_cord_val": launch["checkpoint_cord_val"], "oracle_validation": launch["oracle_validation"]}
        summary["config_sha256"] = launch["config_sha256"]
    sealed_rows = _rows(conn)
    if sealed_rows:
        summary.update(source="warehouse", criteria=sealed_rows)
    elif document is not None and document.get("test") is not None:
        summary.update(source="seal", criteria=[criterion_dict(c) for c in evaluation.criteria_from_seal(document)])
    else:
        summary["criteria"] = pending_criteria(view)
    if summary["source"] != "pending" and document is not None and document.get("test") is not None:
        summary["criteria"] = [_published(c, document["test"]["metrics"]) for c in summary["criteria"]]
        analysis = read_post_seal_analysis(seal_path)
        if analysis is not None:
            summary["post_seal_analysis"] = analysis
    return summary


def _published(criterion: dict[str, Any], metrics: Mapping[str, Any]) -> dict[str, Any]:
    """C08 told with the sentence of what the seal computed, per invoice (D59), and the sealed sentence kept beside it as ``sealed_verdict_es``; every other criterion as it was sealed."""
    versus, baselines = metrics.get("b1_minus_flow") or {}, metrics.get("baselines") or {}
    if criterion["criterion_id"] != "C08" or criterion["passed"] is None or versus.get("ci_low") is None or "b1:all" not in baselines or "flow:all" not in baselines:
        return criterion
    verdict = texts.c08_per_invoice_verdict(bool(criterion["passed"]), b1_bad=baselines["b1:all"]["bad_post"], flow_bad=baselines["flow:all"]["bad_post"],
                                            invoices=metrics["bad_post"]["invoices"], value=versus["value"], low=versus["ci_low"], high=versus["ci_high"])
    return {**criterion, "verdict_es": verdict, "sealed_verdict_es": criterion["verdict_es"]}


def pending_criteria(view: SealedConfig) -> list[dict[str, Any]]:
    limits = dataclasses.asdict(view.criteria)
    thresholds = texts.thresholds_from(limits)
    reason = f"La prueba se evalúa con el cierre del {close_day(view)}"
    return [{"criterion_id": cid, "name_es": t.name_es, "value": None, "baseline_value": None, "ci_low": None, "ci_high": None, "threshold_es": thresholds[cid],
             "passed": None if cid == "C10" else False, "seen_by_research": t.seen_by_research, "seen_by_planner": t.seen_by_planner, "synthetic": t.synthetic, "verdict_es": f"{reason}."}
            for cid, t in texts.CRITERIA.items()]


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _validation_lines(validation: Mapping[str, Any]) -> list[str]:
    if not validation:
        return []
    out = ["", "Cifras de la validación (no son la prueba; cada una dice con qué se ajustó su modelo):", ""]
    checkpoint = validation["checkpoint_cord_val"]
    out.append(f"- Checkpoint de CORD sin ajustar, en la validación de CORD ({checkpoint['receipts']} recibos, `fit_splits`: {checkpoint['fit_splits']}): TED {texts.es(checkpoint['ted'], 2)}.")
    for c in validation["choice"]["candidates"]:
        out.append(f"- Época {c['epoch']} ({c['status']}, `fit_splits`: {c['fit_splits']}): TED en la validación de CORD {texts.es(c['cord_ted'], 2)}; líneas exactas en la validación de proveedores conocidos "
                   f"{texts.pct(c['exact_known'])} y de proveedores nuevos {texts.pct(c['exact_new'])} (sintético).")
    oracle = validation["oracle_validation"]
    out.append(f"- Oráculo B3 sobre la validación (`fit_splits`: {oracle['fit_splits']}): {oracle['invoices'] - oracle['wrong']} de {oracle['invoices']} facturas con la decisión que el generador sembró (sintético).")
    return out


def _post_seal_lines(analysis: Mapping[str, Any] | None) -> list[str]:
    if not analysis:
        return []
    return ["", "### Análisis posteriores al sello", "", "No reemplazan ninguna cifra sellada: se calcularon después del sello, sobre las mismas decisiones emitidas y la misma verdad sintética, y se "
            "publican al lado de lo sellado (D59).", "", texts.c08_preregistered_line(dict(analysis)),
            *([texts.b1_read_order_line(dict(analysis))] if "b1_read_order" in analysis else [])]


def summary_markdown(summary: Mapping[str, Any]) -> str:
    """The summary as Markdown: the table of C01-C10 with each verdict, what does not pass named with its number, the validation figures and, before the close, when the test is evaluated."""
    lines = ["## Resumen de la evaluación", ""]
    if summary["source"] == "pending":
        lines += [f"La prueba se evalúa con el cierre del {summary['close_day']}: hasta entonces no hay veredictos.", ""]
    else:
        failing = [c["criterion_id"] for c in summary["criteria"] if c["passed"] is False]
        lines += [("Todos los criterios con umbral se cumplen." if not failing else f"No se cumple: {', '.join(failing)}."), "", "Cada cifra marcada (sintético) sale de facturas que genera este proyecto.", ""]
    lines += ["| ID | Criterio | Lo que prueba el umbral | Veredicto |", "| --- | --- | --- | --- |"]
    for c in summary["criteria"]:
        lines.append(f"| {c['criterion_id']} | {_cell(c['name_es'])} | {_cell(texts.threshold_figure(c))} | {_cell(c['verdict_es'])} |")
    lines += _post_seal_lines(summary.get("post_seal_analysis"))
    lines += _validation_lines(summary.get("validation") or {})
    return "\n".join(lines) + "\n"


def write_summary(summary: Mapping[str, Any], reports_dir: Path) -> Path:
    """Write ``evaluation_summary.md`` into `reports_dir` (atomically) and return its path."""
    return write_atomic(Path(reports_dir) / "evaluation_summary.md", summary_markdown(summary))


RESULTS_START, RESULTS_END = "<!-- results:start -->", "<!-- results:end -->"


def replace_block(text: str, name: str, start: str, end: str, content: str) -> str:
    """`text` with what lies between the markers `start` and `end` replaced by `content`: each marker once and in order, or a ``ValueError`` that names the file (`name`). A second use with the same
    content changes nothing."""
    first, last = text.find(start), text.find(end)
    if text.count(start) != 1 or text.count(end) != 1 or last < first:
        raise ValueError(f"{name} no tiene los marcadores {start} y {end}, una vez cada uno y en ese orden")
    return text[: first + len(start)] + "\n" + content.strip("\n") + "\n" + text[last:]


__all__ = ["CORD_PREDICTIONS_FILE", "POST_SEAL_FILE", "RESULTS_END", "RESULTS_START", "build_summary", "close_day", "evaluate", "pending_criteria", "post_seal_path", "read_post_seal_analysis", "replace_block",
           "store_result", "summary_markdown", "write_cord_predictions", "write_post_seal_analysis", "write_summary"]
