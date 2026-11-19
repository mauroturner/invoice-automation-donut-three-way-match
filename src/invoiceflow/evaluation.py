"""The closing evaluation (RF17, RF19, RF20; AC20, AC22-AC25; plan section 16; decisions D19, D20, D21): the criteria C01-C10 from what was **issued** and the synthetic truth, and the one opening of
CORD's test split.

* ``evaluate_test`` joins the truth of the 600 test invoices to what the flow issued (``decisions``, ``extracted_fields``, ``qr_reads``, ``erp_posts``): no figure is a new calculation of the flow. A
  **bad post** is an ``auto_post`` that has a planted discrepancy or whose QR header or lines are not the truth's; the **straight through** is an invoice with nothing planted and an intact QR that was posted
  well; the **exact line** is as in ``metrics.exact_lines``. B0-B3 run on the same invoices (B1 and B3 in their own ERP worlds, ``baselines.replay``; B2 needs the unadjusted checkpoint's trees).
* ``evaluate_cord`` is **the only opening of CORD's test split** (AC23): the chosen model and the checkpoint over the 100 receipts, the difference paired by receipt.
* ``criteria_of`` turns the figures and the sealed limits of ``[criteria]`` into C01-C10 and nothing else, so the same function rebuilds each sealed criterion from the seal (``criteria_from_seal``): a seal
  that the code cannot rebuild to 6 decimals is not published. A criterion that cannot be computed is never passed, a limit that is reached passes where the criterion says «at least» or «at most»,
  and a tie never beats.

The evaluation never runs the flow again: the repetition of the last day on a copy of the ERP (C09) is made by the caller and handed in.
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image

from invoiceflow import metrics, texts
from invoiceflow.baselines import b1, b2, replay, truth_fields, truth_qr
from invoiceflow.config import PipelineConfig, SealedConfig
from invoiceflow.cord import read_split
from invoiceflow.decide import Decision, decide
from invoiceflow.match import ErpView
from invoiceflow.normalize import InvoiceFields, LineFields
from invoiceflow.qr import QrPayload, QrRead
from invoiceflow.registry import SealError
from invoiceflow.synth.invoices import InvoiceTruth
from invoiceflow.synth.master import Master
from invoiceflow.truth import read_truth

COHORTS = {"test_known": "known", "test_new": "new"}
TEST_SPLITS = tuple(COHORTS)


class EvaluationError(RuntimeError):
    """The evaluation cannot be made with what the warehouse and the truth hold (nothing was written)."""

    EXIT_CODE = 2


@dataclass(frozen=True)
class EvaluationResult:
    """``figures`` is what the criteria read and the seal keeps (``metrics``); ``invoices``, ``fields`` and ``baselines`` are the rows of ``eval_invoice``, ``eval_field`` and ``eval_baseline``."""

    figures: dict[str, Any]
    invoices: list[dict[str, Any]] = field(default_factory=list)
    fields: list[dict[str, Any]] = field(default_factory=list)
    baselines: list[dict[str, Any]] = field(default_factory=list)


# --- what was read, from the warehouse --------------------------------------------------------------------------------------------------


def _decimal(text: Any) -> Decimal | None:
    return None if text is None else Decimal(text)


def read_fields(conn: Any, key: str) -> InvoiceFields:
    """The fields the flow read for `key`, as typed values from the normalized column of ``extracted_fields``."""
    rows = conn.execute("SELECT field, ordinal, value_norm FROM extracted_fields WHERE invoice_key = ? ORDER BY ordinal, field", (key,)).fetchall()
    lines: dict[int, dict[str, Any]] = {}
    header: dict[str, Any] = {}
    for name, ordinal, value in rows:
        if name.startswith("line."):
            lines.setdefault(ordinal, {})[name[5:]] = value
        elif name.startswith("header."):
            header[name[7:]] = value
    order = [LineFields(l.get("code"), l.get("desc"), _decimal(l.get("qty")), l.get("unit"), _decimal(l.get("unit_price")), _decimal(l.get("amount"))) for _, l in sorted(lines.items())]
    return InvoiceFields(orden_compra=header.get("orden_compra"), lines=tuple(order))


def read_qr_row(conn: Any, key: str) -> QrRead:
    row = conn.execute("SELECT reader, status, invalid_reason, cuit, fecha, pto_vta, tipo_cmp, nro_cmp, importe, moneda, ctz, doc_rec_tipo, doc_rec_nro, cod_aut_tipo, cod_aut FROM qr_reads WHERE invoice_key = ?", (key,)).fetchone()
    if row is None:
        return QrRead(None, "not_read", None, None)
    reader, status, reason, cuit, fecha, pto, tipo, nro, importe, moneda, ctz, doc_tipo, doc_nro, aut_tipo, aut = row
    payload = None if status != "ok" else QrPayload(1, date.fromisoformat(fecha), cuit, pto, tipo, nro, Decimal(importe), moneda, Decimal(ctz), doc_tipo, doc_nro, aut_tipo, aut)
    return QrRead(reader, status, payload, reason)


def _payload_equal(read: QrPayload | None, truth: QrPayload | None) -> bool:
    return read is not None and truth is not None and read == truth


def _bad_post(truth: InvoiceTruth, decision: str, qr: QrRead, fields: InvoiceFields) -> bool:
    """(a) an auto_post with a discrepancy planted, or (b) one whose QR header or lines are not the truth's (requirements 8.1)."""
    if decision != "auto_post":
        return False
    if truth.planted != "none":
        return True
    if qr.payload is None or qr.payload.cuit != truth.header.cuit_emisor or qr.payload.pto_vta != truth.header.pto_vta or qr.payload.nro_cmp != truth.header.nro \
            or qr.payload.fecha != truth.header.fecha or qr.payload.moneda != truth.header.moneda or qr.payload.importe != truth.totals.total:
        return True
    exact, lines = metrics.exact_lines(fields, truth.lines)
    return not (exact == lines == len(fields.lines))


def _rate(successes: int, n: int) -> float | None:
    return None if n == 0 else successes / n


def _baseline_row(name: str, cohort: str, rows: Sequence[dict[str, Any]], decisions: Mapping[str, str], bad: Mapping[str, bool]) -> dict[str, Any]:
    keys = [r["invoice_key"] for r in rows]
    posted = [k for k in keys if decisions[k] == "auto_post"]
    badly = sum(1 for k in posted if bad[k])
    low, high = metrics.wilson(badly, len(posted))
    clean = [r for r in rows if r["planted"] == "none" and r["qr_printed"]]
    straight = sum(1 for r in clean if decisions[r["invoice_key"]] == "auto_post" and not bad[r["invoice_key"]])
    planted = [r for r in rows if r["planted"] != "none"]
    review = sum(1 for r in planted if decisions[r["invoice_key"]] == "review")
    return {"baseline": name, "cohort": cohort, "auto_post": len(posted), "bad_post": badly, "bad_post_rate": _rate(badly, len(posted)), "ci_low": low, "ci_high": high,
            "straight_through": _rate(straight, len(clean)), "review_recall": _rate(review, len(planted))}


def load_test_truths(interim_dir: Path, delivery_ids: Sequence[str]) -> list[InvoiceTruth]:
    """The truth of the test, read from the truth file of each daily delivery of `delivery_ids` (``<interim>/truth/<id>.jsonl``): **the only place the closing run reads them** (D23; ``truth.read_truth``
    answers to this module and to ``baselines``). A missing file is an ``EvaluationError``; nothing is written."""
    truths: list[InvoiceTruth] = []
    for delivery_id in delivery_ids:
        path = Path(interim_dir) / "truth" / f"{delivery_id}.jsonl"
        if not path.is_file():
            raise EvaluationError(f"falta la verdad de la entrega {delivery_id}: la prueba no se evalúa sin ella")
        truths += read_truth(path)
    return sorted(truths, key=lambda t: t.invoice_key)


def evaluate_test(conn: Any, view: SealedConfig, master: Master, truths: Sequence[InvoiceTruth], *, repeat_new: int | None = None,
                  checkpoint_trees: Mapping[str, Any] | None = None, cord: Mapping[str, Any] | None = None) -> EvaluationResult:
    """The figures of the closing evaluation over the test `truths` and what the flow issued for them (see the module). `repeat_new` is how many invoices a repetition of the last day created in a
    copy of the ERP; `checkpoint_trees` the unadjusted checkpoint's CORD-schema tree of each test page (for B2); `cord` the result of ``evaluate_cord``."""
    issued = {k: (d, fr) for k, d, fr in conn.execute("SELECT invoice_key, decision, first_reason FROM decisions")}
    missing = sorted(t.invoice_key for t in truths if t.invoice_key not in issued)
    if not truths or missing:
        raise EvaluationError(f"la prueba no está completa: {len(missing)} de {len(truths)} facturas sin decisión emitida" + (f", por ejemplo {missing[:3]}" if missing else ""))
    posts = {k: (status, equal) for k, status, equal in conn.execute("SELECT invoice_key, status, readback_equal FROM erp_posts")}
    rows: list[dict[str, Any]] = []
    flow_decisions: dict[str, str] = {}
    bad: dict[str, bool] = {}
    for truth in sorted(truths, key=lambda t: t.invoice_key):
        decision, first = issued[truth.invoice_key]
        read, intact = read_qr_row(conn, truth.invoice_key), truth_qr(truth, view)
        fields = read_fields(conn, truth.invoice_key)
        exact, lines = metrics.exact_lines(fields, truth.lines)
        printed = truth.qr_text is not None
        status = posts.get(truth.invoice_key, (None, None))[0]
        is_bad = _bad_post(truth, decision, read, fields)
        posted_ok = decision == "auto_post" and not is_bad
        rows.append({"invoice_key": truth.invoice_key, "cohort": truth.split, "supplier_cohort": truth.cohort, "planted": truth.planted, "expected_decision": truth.expected_decision,
                     "decision": decision, "first_reason": first, "first_reason_ok": (first == truth.expected_reasons[0]) if truth.planted != "none" and truth.expected_reasons else None,
                     "scan_profile": truth.scan_profile, "file_format": truth.file_format, "qr_cm": float(truth.qr_cm), "qr_printed": int(printed),
                     "qr_read_ok": int(printed and _payload_equal(read.payload, intact.payload)), "posted": int(status in ("posted", "already_posted")), "bad_post": int(is_bad),
                     "straight_through": int(truth.planted == "none" and printed and posted_ok), "lines": lines, "line_exact": exact})
        flow_decisions[truth.invoice_key], bad[truth.invoice_key] = decision, is_bad
    by_split = {split: [r for r in rows if r["cohort"] == split] for split in TEST_SPLITS}

    def line_figures(split: str) -> dict[str, Any]:
        group = by_split[split]
        exact, total = sum(r["line_exact"] for r in group), sum(r["lines"] for r in group)
        low, high = metrics.wilson(exact, total)
        return {"exact": exact, "lines": total, "rate": _rate(exact, total), "ci_low": low, "ci_high": high, "invoices": len(group)}

    def straight_figures(split: str) -> dict[str, Any]:
        clean = [r for r in by_split[split] if r["planted"] == "none" and r["qr_printed"]]
        posted = sum(r["straight_through"] for r in clean)
        return {"posted": posted, "clean": len(clean), "rate": _rate(posted, len(clean))}

    printed = [r for r in rows if r["qr_printed"]]
    posted_rows = [r for r in rows if r["decision"] == "auto_post"]
    badly = sum(r["bad_post"] for r in posted_rows)
    low, high = metrics.wilson(badly, len(posted_rows))
    planted = [r for r in rows if r["planted"] != "none"]
    figures: dict[str, Any] = {
        "lines": {"known": line_figures("test_known"), "new": line_figures("test_new")},
        "qr": {"printed": len(printed), "intact": len(printed), "read": sum(r["qr_read_ok"] for r in printed), "rate": _rate(sum(r["qr_read_ok"] for r in printed), len(printed))},
        "bad_post": {"posts": len(posted_rows), "bad": badly, "rate": _rate(badly, len(posted_rows)), "ci_low": low, "ci_high": high, "invoices": len(rows)},
        "straight_through": {"known": straight_figures("test_known"), "new": straight_figures("test_new")},
        "review_recall": {"planted": len(planted), "review": sum(1 for r in planted if r["decision"] == "review"), "first_reason_ok": sum(1 for r in planted if r["first_reason_ok"]),
                          "rate": _rate(sum(1 for r in planted if r["decision"] == "review"), len(planted)),
                          "by_class": {c: {"planted": n, "review": sum(1 for r in planted if r["planted"] == c and r["decision"] == "review")} for c, n in sorted(Counter(r["planted"] for r in planted).items())}},
        "erp": {"posts": sum(r["posted"] for r in rows), "readback_equal": sum(1 for k, (status, equal) in posts.items() if k in flow_decisions and status in ("posted", "already_posted") and equal == 1),
                "repeat_new": repeat_new},
        "variants": 0, "days": len({t.received_on for t in truths}),
    }
    if cord is not None:
        figures["cord"] = dict(cord)
    baselines = []
    cohorts = {"all": rows, "test_known": by_split["test_known"], "test_new": by_split["test_new"]}
    worlds: dict[str, dict[str, str]] = {"b0": {k: "review" for k in flow_decisions}, "flow": flow_decisions}
    bad_of = {"b0": {k: False for k in flow_decisions}, "flow": bad}
    by_key = {t.invoice_key: t for t in truths}

    def judged(decisions: Mapping[str, Decision]) -> tuple[dict[str, str], dict[str, bool]]:
        return _judged(conn, view, by_key, decisions)

    worlds["b1"], bad_of["b1"] = judged(_b1_world(conn, view, master, truths))
    worlds["b3"], bad_of["b3"] = judged(replay(view, master, truths, lambda t, erp: decide(truth_fields(t), truth_qr(t, view), erp, view)))
    if checkpoint_trees is not None:
        worlds["b2"], bad_of["b2"] = judged(replay(view, master, truths, lambda t, erp: b2(checkpoint_trees.get(t.invoice_key, {}), read_qr_row(conn, t.invoice_key), erp, view)))
    for name in ("b0", "b1", "b2", "b3", "flow"):
        if name not in worlds:
            continue
        for cohort, group in cohorts.items():
            baselines.append(_baseline_row(name, cohort, group, worlds[name], bad_of[name]))
    figures["baselines"] = {f"{b['baseline']}:{b['cohort']}": {k: v for k, v in b.items() if k not in ("baseline", "cohort")} for b in baselines}
    b1_rate = next(b for b in baselines if b["baseline"] == "b1" and b["cohort"] == "all")["bad_post_rate"]
    flow_rate = next(b for b in baselines if b["baseline"] == "flow" and b["cohort"] == "all")["bad_post_rate"]
    paired_b1 = [float(bad_of["b1"][r["invoice_key"]] and worlds["b1"][r["invoice_key"]] == "auto_post") for r in rows]
    paired_flow = [float(bad_of["flow"][r["invoice_key"]] and worlds["flow"][r["invoice_key"]] == "auto_post") for r in rows]
    value, low, high = metrics.paired_difference(paired_b1, paired_flow, resamples=view.evaluation.resamples, seed=view.evaluation.seed)
    figures["b1_minus_flow"] = {"value": value, "ci_low": low, "ci_high": high, "b1_rate": b1_rate, "flow_rate": flow_rate}
    return EvaluationResult(figures, rows, _field_rows(conn, by_split), baselines)


def _judged(conn: Any, view: SealedConfig, by_key: Mapping[str, InvoiceTruth], decisions: Mapping[str, Decision]) -> tuple[dict[str, str], dict[str, bool]]:
    """A baseline's decision by invoice and whether each one is a bad post (``_would_be_bad`` of the ones it posted)."""
    names = {k: d.decision for k, d in decisions.items()}
    return names, {k: names[k] == "auto_post" and _would_be_bad(by_key[k], conn, view) for k in names}


B1_ORDERS = ("truth", "read")


def b1_decisions(conn: Any, view: SealedConfig, master: Master, truths: Sequence[InvoiceTruth], *, order: str) -> dict[str, Decision]:
    """B1 over the test in its own ERP world, with the QR the flow read and the order number the flow read for the decision. `order` says which order the ERP view is built from: ``read``, the one
    the model read, as D48 says (D60); ``truth``, the page's, as the seal computed it (B2 of the evaluation: the sealed B1 is kept so that the seal is rebuilt, and published as such)."""
    if order not in B1_ORDERS:
        raise ValueError(f"b1_decisions: order={order!r}; se esperaba uno de {B1_ORDERS}")

    def from_b1(truth: InvoiceTruth, erp: ErpView) -> Decision:
        return b1(read_fields(conn, truth.invoice_key), read_qr_row(conn, truth.invoice_key), erp, view)

    if order == "truth":
        return replay(view, master, truths, from_b1)
    return replay(view, master, truths, from_b1, order_of=lambda truth: read_fields(conn, truth.invoice_key).orden_compra)


def _b1_world(conn: Any, view: SealedConfig, master: Master, truths: Sequence[InvoiceTruth]) -> dict[str, Decision]:
    """The sealed B1 (``b1_decisions`` with the truth's order)."""
    return b1_decisions(conn, view, master, truths, order="truth")


def _flow_judged(conn: Any, view: SealedConfig, truths: Sequence[InvoiceTruth]) -> tuple[dict[str, str], dict[str, bool]]:
    """The flow's issued decision by invoice and whether it is a bad post, as ``evaluate_test`` judges them."""
    issued = dict(conn.execute("SELECT invoice_key, decision FROM decisions"))
    names = {t.invoice_key: issued[t.invoice_key] for t in truths}
    bad = {t.invoice_key: _bad_post(t, names[t.invoice_key], read_qr_row(conn, t.invoice_key), read_fields(conn, t.invoice_key)) for t in truths}
    return names, bad


def _rate_difference(keys: Sequence[str], a: tuple[Mapping[str, str], Mapping[str, bool]], b: tuple[Mapping[str, str], Mapping[str, bool]], view: SealedConfig) -> dict[str, Any]:
    """C08 as requirements 8.1 define it: the bad-post rate of `a` (bad posts over auto-posts) less the one of `b`, resampled by invoice (``metrics.paired_rate_difference``)."""

    def side(world: tuple[Mapping[str, str], Mapping[str, bool]]) -> tuple[list[float], list[float], dict[str, Any]]:
        names, bad = world
        posted = [float(names[k] == "auto_post") for k in keys]
        wrong = [float(names[k] == "auto_post" and bad[k]) for k in keys]
        return wrong, posted, {"posts": int(sum(posted)), "bad": int(sum(wrong)), "rate": _rate(int(sum(wrong)), int(sum(posted)))}

    bad_a, posted_a, figures_a = side(a)
    bad_b, posted_b, figures_b = side(b)
    value, low, high = metrics.paired_rate_difference(bad_a, posted_a, bad_b, posted_b, resamples=view.evaluation.resamples, seed=view.evaluation.seed)
    return {"b1": figures_a, "flow": figures_b, "value": value, "ci_low": low, "ci_high": high, "passed": None if low is None else low > 0}


def _per_invoice_difference(keys: Sequence[str], a: tuple[Mapping[str, str], Mapping[str, bool]], b: tuple[Mapping[str, str], Mapping[str, bool]], view: SealedConfig) -> dict[str, Any]:
    """C08 as the seal computes it: «`a` posted badly» less «`b` posted badly», by invoice of the whole test, resampled by invoice."""
    paired_a = [float(a[1][k] and a[0][k] == "auto_post") for k in keys]
    paired_b = [float(b[1][k] and b[0][k] == "auto_post") for k in keys]
    value, low, high = metrics.paired_difference(paired_a, paired_b, resamples=view.evaluation.resamples, seed=view.evaluation.seed)
    return {"b1_bad": int(sum(paired_a)), "flow_bad": int(sum(paired_b)), "invoices": len(keys), "value": value, "ci_low": low, "ci_high": high,
            "passed": None if low is None else low > 0}


def post_seal_analysis(conn: Any, view: SealedConfig, master: Master, truths: Sequence[InvoiceTruth]) -> dict[str, Any]:
    """What is published **beside** the sealed figures and never in their place (D59, after D45 of project 06): computed from the same issued rows and the same truth as ``evaluate_test``, it
    changes nothing that is sealed. ``c08_preregistered`` is C08 as requirements 8.1 define it, the difference of the bad-post rates (bad posts over auto-posts) of the sealed B1 and the flow,
    which the seal computed per invoice of the whole test instead. ``b1_read_order`` is B1 as D48 says, with the order number the model read (D60; the sealed one took the truth's): its
    auto-posts, bad posts and rates by cohort, and C08 against the flow both per invoice (the sealed form) and per auto-post (the preregistered one)."""
    by_key = {t.invoice_key: t for t in truths}
    keys = sorted(by_key)
    flow = _flow_judged(conn, view, truths)
    sealed_b1 = _judged(conn, view, by_key, _b1_world(conn, view, master, truths))
    read_b1 = _judged(conn, view, by_key, b1_decisions(conn, view, master, truths, order="read"))
    cohorts = {"all": list(truths), **{split: [t for t in truths if t.split == split] for split in TEST_SPLITS}}
    rows = {cohort: _baseline_row("b1", cohort, [{"invoice_key": t.invoice_key, "planted": t.planted, "qr_printed": t.qr_text is not None} for t in group], read_b1[0], read_b1[1])
            for cohort, group in cohorts.items()}
    return {"sealed": False, "synthetic": True, "invoices": len(keys), "decisions": ["D59", "D60"], "c08_preregistered": _rate_difference(keys, sealed_b1, flow, view),
            "b1_read_order": {"baselines": {c: {k: v for k, v in r.items() if k not in ("baseline", "cohort")} for c, r in rows.items()},
                              "c08_per_invoice": _per_invoice_difference(keys, read_b1, flow, view), "c08_preregistered": _rate_difference(keys, read_b1, flow, view)}}


def _would_be_bad(truth: InvoiceTruth, conn: Any, view: SealedConfig) -> bool:
    """For a baseline that decided auto_post: a planted discrepancy is a bad post; otherwise the fields it would post are the flow's (the baselines differ in the decision, not in the reading)."""
    if truth.planted != "none":
        return True
    fields, qr = read_fields(conn, truth.invoice_key), read_qr_row(conn, truth.invoice_key)
    return _bad_post(truth, "auto_post", qr, fields)


def _field_rows(conn: Any, by_split: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """The reading by field and cohort: of the fields the model wrote, how many were normalized to something (the recall of the leaves is C10's informative part, by field name)."""
    out = []
    for split, group in by_split.items():
        keys = [r["invoice_key"] for r in group]
        if not keys:
            continue
        marks = ",".join("?" * len(keys))
        for name, leaves, correct in conn.execute(f"SELECT field, count(*), sum(value_norm IS NOT NULL) FROM extracted_fields WHERE invoice_key IN ({marks}) GROUP BY field ORDER BY field", keys):
            out.append({"cohort": split, "field": name, "leaves": leaves, "correct": correct or 0, "recall": _rate(correct or 0, leaves)})
    return out


# --- CORD's test split: the one opening ---------------------------------------------------------------------------------------------------


def evaluate_cord(cfg: PipelineConfig, view: SealedConfig, chosen: Any, checkpoint: Any, *, closing_run: bool,
                  predictions: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The chosen model and the checkpoint over the receipts of CORD's ``test`` split, with the CORD task's prompt (each argument has ``extract(image) -> tree``), the TED by receipt and the
    difference paired by receipt with its interval (C01). **The only function that opens the split** (``cord.read_split`` refuses any other caller and any run that is not the closing one).
    Returns the figures for ``criteria_of`` and the rows of ``eval_cord``; given a list as `predictions`, it appends to it, for each receipt, its truth and the tree each model read, so the TED can be
    recomputed without reading the split again (A10 of the evaluation, D63)."""
    chosen_ted: list[float] = []
    checkpoint_ted: list[float] = []
    rows: list[dict[str, Any]] = []
    for receipt in read_split(cfg, "test", closing_run=closing_run):
        with Image.open(io.BytesIO(receipt.image_bytes)) as opened:
            page = opened.convert("RGB")
        read: dict[str, Any] = {"receipt_key": receipt.receipt_key, "truth": receipt.gt_parse}
        for name, model, bucket in (("chosen", chosen, chosen_ted), ("checkpoint", checkpoint, checkpoint_ted)):
            tree = model.extract(page)
            read[name] = tree
            ted = 100.0 * metrics.ted_accuracy(tree, receipt.gt_parse)
            matched, leaves, _ = metrics.leaf_match(tree, receipt.gt_parse)
            bucket.append(ted)
            rows.append({"model": name, "receipt_key": receipt.receipt_key, "ted": ted, "leaves": leaves, "matched": matched})
        if predictions is not None:
            predictions.append(read)
    n = len(chosen_ted)
    value, low, high = metrics.paired_difference(chosen_ted, checkpoint_ted, resamples=view.evaluation.resamples, seed=view.evaluation.seed)
    figures = {"receipts": n, "ted_chosen": sum(chosen_ted) / n if n else None, "ted_checkpoint": sum(checkpoint_ted) / n if n else None,
               "diff": {"value": value, "ci_low": low, "ci_high": high}}
    return figures, rows


# --- the criteria -----------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    name_es: str
    value: float | None
    baseline_value: float | None
    ci_low: float | None
    ci_high: float | None
    threshold_es: str
    passed: bool | None
    seen_by_research: str
    seen_by_planner: str
    synthetic: bool
    verdict_es: str

    def seal_entry(self) -> dict[str, Any]:
        return {"id": self.criterion_id, "value": self.value, "baseline_value": self.baseline_value, "ci_low": self.ci_low, "ci_high": self.ci_high, "passed": self.passed,
                "verdict_es": self.verdict_es}


def _all(*values: Any) -> bool:
    return all(v is not None for v in values)


def criteria_of(figs: Mapping[str, Any], limits: Mapping[str, float]) -> list[Criterion]:
    """C01 to C10 from the figures of the test and the sealed limits of ``[criteria]``: nothing else (see the module)."""
    thresholds = texts.thresholds_from(dict(limits))
    out: list[Criterion] = []

    def add(cid: str, value, baseline, passed: bool | None, verdict: str, ci=(None, None)) -> None:
        t = texts.CRITERIA[cid]
        out.append(Criterion(cid, t.name_es, value, baseline, ci[0], ci[1], thresholds[cid], passed, t.seen_by_research, t.seen_by_planner, t.synthetic, verdict))

    def cannot(cid: str, why: str) -> None:
        add(cid, None, None, False, texts.cannot(why))

    cord = figs.get("cord") or {}
    diff = cord.get("diff") or {}
    if not _all(diff.get("value"), diff.get("ci_low"), diff.get("ci_high"), cord.get("ted_chosen"), cord.get("ted_checkpoint")):
        cannot("C01", "la prueba de CORD no se evaluó todavía")
    else:
        ok = diff["ci_low"] > limits["c01_min_ted_diff"]
        add("C01", cord["ted_chosen"], cord["ted_checkpoint"], ok, texts.verdict("C01", ok, receipts=cord["receipts"], chosen=cord["ted_chosen"], checkpoint=cord["ted_checkpoint"], value=diff["value"],
                                                                                low=diff["ci_low"], high=diff["ci_high"], limit=limits["c01_min_ted_diff"]), (diff["ci_low"], diff["ci_high"]))
    lines = figs.get("lines") or {}
    for cid, cohort, key in (("C02", "known", "c02_min_line_exact"), ("C03", "new", "c03_min_line_exact")):
        got = lines.get(cohort) or {}
        if got.get("rate") is None:
            cannot(cid, "la prueba no tiene líneas de esa cohorte")
        else:
            ok = got["rate"] >= limits[key]
            add(cid, got["rate"], None, ok, texts.verdict(cid, ok, cohort=cohort, invoices=got["invoices"], rate=got["rate"], limit=limits[key]), (got["ci_low"], got["ci_high"]))
    qr = figs.get("qr") or {}
    if qr.get("rate") is None:
        cannot("C04", "no hay facturas impresas con QR intacto")
    else:
        ok = qr["rate"] >= limits["c04_min_qr_read"]
        add("C04", qr["rate"], None, ok, texts.verdict("C04", ok, printed=qr["printed"], read=qr["read"], rate=qr["rate"], limit=limits["c04_min_qr_read"]))
    bad = figs.get("bad_post") or {}
    if bad.get("ci_high") is None:
        cannot("C05", "el flujo no cargó ninguna factura en la prueba")
    else:
        ok = bad["ci_high"] <= limits["c05_max_bad_post_upper"]
        add("C05", bad["rate"], None, ok, texts.verdict("C05", ok, invoices=bad["invoices"], bad=bad["bad"], posts=bad["posts"], high=bad["ci_high"], limit=limits["c05_max_bad_post_upper"]),
            (bad["ci_low"], bad["ci_high"]))
    straight = (figs.get("straight_through") or {}).get("known") or {}
    if straight.get("rate") is None:
        cannot("C06", "no hay facturas limpias de proveedores conocidos con QR impreso")
    else:
        ok = straight["rate"] >= limits["c06_min_straight_through"]
        add("C06", straight["rate"], None, ok, texts.verdict("C06", ok, rate=straight["rate"], posted=straight["posted"], clean=straight["clean"], limit=limits["c06_min_straight_through"]))
    recall = figs.get("review_recall") or {}
    if recall.get("rate") is None:
        cannot("C07", "la prueba no tiene facturas con discrepancia sembrada")
    else:
        ok = recall["rate"] >= limits["c07_min_review_recall"]
        add("C07", recall["rate"], None, ok, texts.verdict("C07", ok, planted=recall["planted"], review=recall["review"], rate=recall["rate"], limit=limits["c07_min_review_recall"]))
    versus = figs.get("b1_minus_flow") or {}
    if not _all(versus.get("ci_low"), versus.get("ci_high"), versus.get("b1_rate"), versus.get("flow_rate")):
        cannot("C08", "no hay cargas de B1 y del flujo que comparar")
    else:
        ok = versus["ci_low"] > 0
        add("C08", versus["flow_rate"], versus["b1_rate"], ok, texts.verdict("C08", ok, b1=versus["b1_rate"], flow=versus["flow_rate"], value=versus["value"], low=versus["ci_low"], high=versus["ci_high"]),
            (versus["ci_low"], versus["ci_high"]))
    erp = figs.get("erp") or {}
    if not erp.get("posts") or erp.get("repeat_new") is None:
        cannot("C09", "no hay cargas leídas de vuelta o falta la repetición del último día")
    else:
        ok = erp["readback_equal"] == erp["posts"] and erp["repeat_new"] == 0
        add("C09", erp["readback_equal"] / erp["posts"], None, ok, texts.verdict("C09", ok, equal=erp["readback_equal"], posts=erp["posts"], repeat=erp["repeat_new"]))
    add("C10", None, None, None, texts.informative_verdict(int(figs.get("variants", 0)), int(figs.get("days", 0))))
    return out


def evaluate_criteria(result: EvaluationResult, view: SealedConfig) -> list[Criterion]:
    """The criteria of an evaluation: ``criteria_of`` its figures and the sealed ``[criteria]`` of `view`."""
    import dataclasses

    return criteria_of(result.figures, dataclasses.asdict(view.criteria))


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Mapping):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    return value


def criteria_from_seal(seal: Mapping[str, Any]) -> list[Criterion]:
    """C01 to C10 rebuilt from the test part of the seal with ``criteria_of``; each must give back its own entry of the seal to 6 decimals, or the seal is not taken (``SealError``): a seal that the
    code no longer knows how to rebuild is never published (D21)."""
    test, launch = seal.get("test"), seal.get("launch")
    if not test or not launch:
        raise SealError("el sello no tiene la parte de la prueba: no hay criterios sellados")
    rebuilt = criteria_of(test["metrics"], launch["sealed_config"]["criteria"])
    sealed = {entry["id"]: entry for entry in test["criteria"]}
    for criterion in rebuilt:
        if _rounded(criterion.seal_entry()) != _rounded(sealed.get(criterion.criterion_id)):
            raise SealError(f"el criterio {criterion.criterion_id} del sello no se reconstruye desde sus cifras: lo sellado no se toma (docs/runbook.md, R7)")
    return rebuilt


__all__ = ["B1_ORDERS", "Criterion", "EvaluationError", "EvaluationResult", "criteria_from_seal", "criteria_of", "evaluate_cord", "evaluate_criteria", "evaluate_test", "b1_decisions", "post_seal_analysis", "read_fields",
           "read_qr_row"]
