"""The evaluation as a task and the summary that tells it (RF20; AC22, AC24; plan section 16; decisions D19, D21): ``evaluate`` runs once at the close and writes the sealed rows and the test part of the
seal; ``build_summary`` reads what there is, in this order of trust: the sealed rows of the warehouse, else the criteria **rebuilt from the seal** (a warehouse without the sealed rows: a new one, a quick
copy, the site's build), else nothing yet (before the close the summary says when the test is evaluated and shows the validation figures with the partitions their model saw).
"""

from __future__ import annotations

import dataclasses
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
    cord_figures, cord_rows = evaluation.evaluate_cord(cfg, view, chosen, checkpoint, closing_run=closing_run)
    result = evaluation.evaluate_test(conn, view, master, truths, repeat_new=repeat_new, checkpoint_trees=checkpoint_trees, cord=cord_figures)
    criteria = evaluation.evaluate_criteria(result, view)
    rows = store_result(conn, result, criteria, cord_rows, state)
    launch = (read_seal(seal_path) or {})["launch"]
    part = {"run_key": run.run_key, "as_of": run.as_of.strftime("%Y-%m-%d %H:%M:%S"), "config_sha256": config_fingerprint(view), "criteria": [c.seal_entry() for c in criteria],
            "metrics": result.figures, "cord": {"chosen_epoch": launch["chosen_epoch"], **cord_figures}, "baselines": result.figures["baselines"],
            "operation": {"days": result.figures["days"], "invoices": len(result.invoices)}}
    outcome = write_test_seal(seal_path, part)
    logger.info("evaluate: state %s, seal %s, rows %s", state, outcome, rows)
    return {"state": state, "seal": outcome, "criteria": criteria, "rows": rows, "result": result}


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
    return summary


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


def summary_markdown(summary: Mapping[str, Any]) -> str:
    """The summary as Markdown: the table of C01-C10 with each verdict, what does not pass named with its number, the validation figures and, before the close, when the test is evaluated."""
    lines = ["## Resumen de la evaluación", ""]
    if summary["source"] == "pending":
        lines += [f"La prueba se evalúa con el cierre del {summary['close_day']}: hasta entonces no hay veredictos.", ""]
    else:
        failing = [c["criterion_id"] for c in summary["criteria"] if c["passed"] is False]
        lines += [("Todos los criterios con umbral se cumplen." if not failing else f"No se cumple: {', '.join(failing)}."), "", "Cada cifra marcada (sintético) sale de facturas que genera este proyecto.", ""]
    lines += ["| ID | Criterio | Valor | Veredicto |", "| --- | --- | --- | --- |"]
    for c in summary["criteria"]:
        value = "sin umbral" if c["passed"] is None and c["value"] is None else texts.es(c["value"], 3) if c["value"] is not None else "sin dato"
        if c["synthetic"] and c["value"] is not None:
            value += " (sintético)"
        lines.append(f"| {c['criterion_id']} | {_cell(c['name_es'])} | {_cell(value)} | {_cell(c['verdict_es'])} |")
    lines += _validation_lines(summary.get("validation") or {})
    return "\n".join(lines) + "\n"


def write_summary(summary: Mapping[str, Any], reports_dir: Path) -> Path:
    """Write ``evaluation_summary.md`` into `reports_dir` (atomically) and return its path."""
    return write_atomic(Path(reports_dir) / "evaluation_summary.md", summary_markdown(summary))


__all__ = ["build_summary", "close_day", "evaluate", "pending_criteria", "store_result", "summary_markdown", "write_summary"]
