"""The closing evaluation on what the flow issued (U16; AC22-AC25; plan section 16; D19-D21): the figures are the join of the synthetic truth with the issued rows, worked out here by hand; B0-B3 run
on the same invoices; CORD's test split is opened by ``evaluate_cord`` of the closing run and by nothing else; the evaluation is stored once, sealed once and reproduced by a repetition."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime

import pytest

from invoiceflow import daily, evaluation, reporting
from invoiceflow.baselines import oracle
from invoiceflow.leakage import LeakageError
from invoiceflow.registry import RunMeta, read_seal, write_launch_seal
from tests.fixtures.daily_world import DailyWorld, build_world, decision_rows, fresh_erp, load_files, open_warehouse
from tests.fixtures.make_cord_fixture import FILE_NAMES
from tests.fixtures.projects import make_project
from tests.fixtures.seals import launch_document


class Reads:
    """A model of the tests that answers a CORD tree and counts what it was asked."""

    def __init__(self, tree):
        self.tree, self.asked = tree, 0

    def extract(self, image):
        self.asked += 1
        return self.tree


def run_world(world: DailyWorld, folder, monkeypatch):
    monkeypatch.setattr(daily, "read_qr", world.read_qr)
    client, _ = fresh_erp(world, folder)
    conn = open_warehouse(folder)
    for day in world.days:
        spec = world.specs()[day]
        run, inbox = load_files(world, conn, spec, [t.invoice_key for t in world.truths[day]], folder)
        daily.run_day(conn, client, world.extractor, run, world.view, spec, inbox)
    return conn, client


@pytest.fixture(scope="module")
def perfect(quick_view, tmp_path_factory):
    world = build_world(quick_view, tmp_path_factory.mktemp("eval_world"))
    folder = tmp_path_factory.mktemp("eval_run")
    with pytest.MonkeyPatch.context() as patch:
        conn, client = run_world(world, folder, patch)
    return world, conn


def test_the_figures_are_the_truth_joined_to_what_was_issued_by_hand(perfect):
    world, conn = perfect
    truths = world.all_truths()
    result = evaluation.evaluate_test(conn, world.view, world.master, truths, repeat_new=0)
    f = result.figures
    known = [t for t in truths if t.split == "test_known"]
    new = [t for t in truths if t.split == "test_new"]
    planted = [t for t in truths if t.planted != "none"]
    expected = oracle(world.view, world.master, truths)
    posted = [k for k, d in expected.items() if d.decision == "auto_post"]
    assert planted and len(known) == 9 and len(new) == 9
    assert (f["lines"]["known"]["exact"], f["lines"]["known"]["lines"]) == (sum(len(t.lines) for t in known),) * 2 and f["lines"]["new"]["rate"] == 1.0, "a perfect reader reads every line"
    assert f["bad_post"]["posts"] == len(posted) and f["bad_post"]["bad"] == 0 and f["bad_post"]["rate"] == 0.0 and f["bad_post"]["ci_low"] == 0.0
    assert round(f["bad_post"]["ci_high"], 6) == round(__import__("invoiceflow.metrics", fromlist=["wilson"]).wilson(0, len(posted))[1], 6)
    assert f["review_recall"]["planted"] == len(planted) and f["review_recall"]["review"] == len(planted) and f["review_recall"]["rate"] == 1.0
    assert sum(c["planted"] for c in f["review_recall"]["by_class"].values()) == len(planted)
    printed = [t for t in truths if t.qr_text is not None]
    assert f["qr"]["printed"] == len(printed) and f["qr"]["read"] == len(printed) and f["qr"]["rate"] == 1.0
    clean_known = [t for t in known if t.planted == "none" and t.qr_text is not None]
    assert f["straight_through"]["known"]["clean"] == len(clean_known) and f["straight_through"]["known"]["posted"] == sum(1 for t in clean_known if expected[t.invoice_key].decision == "auto_post")
    assert f["erp"]["posts"] == len(posted) == f["erp"]["readback_equal"] and f["erp"]["repeat_new"] == 0 and f["days"] == 3
    rows = {r["invoice_key"]: r for r in result.invoices}
    assert set(rows) == {t.invoice_key for t in truths} and all(r["cohort"] == r["invoice_key"].rsplit("-", 1)[0] and r["supplier_cohort"] in ("known", "new_test") for r in rows.values())
    # the baselines, on the same invoices: B0 posts nothing, B3 is what the flow decided (the flow read perfectly), B1 never looks at the lines
    base = {(b["baseline"], b["cohort"]): b for b in result.baselines}
    assert base[("b0", "all")]["auto_post"] == 0 and base[("b0", "all")]["bad_post"] == 0 and base[("b0", "all")]["review_recall"] == 1.0
    assert base[("b3", "all")]["auto_post"] == base[("flow", "all")]["auto_post"] == len(posted) and base[("b3", "all")]["bad_post"] == 0
    assert base[("b1", "all")]["auto_post"] >= base[("flow", "all")]["auto_post"] and ("b2", "all") not in base, "no checkpoint trees were given: B2 is not made up"
    assert f["b1_minus_flow"]["value"] >= 0 and f["baselines"]["flow:all"]["bad_post"] == 0


def test_a_line_read_wrong_is_not_exact_goes_to_review_and_is_not_a_bad_post(quick_view, tmp_path_factory, monkeypatch):
    base = build_world(quick_view, tmp_path_factory.mktemp("eval_clean_world"))
    victim = next(t for t in base.all_truths() if t.planted == "none")
    world = build_world(quick_view, tmp_path_factory.mktemp("eval_error_world"), errors={victim.invoice_key: "quantity"})
    conn, _ = run_world(world, tmp_path_factory.mktemp("eval_error_run"), monkeypatch)
    result = evaluation.evaluate_test(conn, world.view, world.master, world.all_truths(), repeat_new=0)
    row = next(r for r in result.invoices if r["invoice_key"] == victim.invoice_key)
    assert row["line_exact"] == row["lines"] - 1 and row["decision"] == "review" and row["bad_post"] == 0 and row["straight_through"] == 0
    assert result.figures["lines"][("known" if victim.split == "test_known" else "new")]["rate"] < 1.0 and result.figures["bad_post"]["bad"] == 0
    assert row["first_reason_ok"] is None, "nothing was planted in it"


def test_an_evaluation_with_invoices_that_were_not_issued_is_refused(perfect):
    world, conn = perfect
    with pytest.raises(evaluation.EvaluationError, match="no está completa"):
        evaluation.evaluate_test(conn, world.view, world.master, [*world.all_truths(), dataclasses.replace(world.all_truths()[0], invoice_key="test_known-99999")], repeat_new=0)
    with pytest.raises(evaluation.EvaluationError):
        evaluation.evaluate_test(conn, world.view, world.master, [], repeat_new=0)


def test_cord_test_split_is_opened_once_by_the_closing_evaluation_and_the_difference_is_paired_by_receipt(tmp_path, monkeypatch):
    from invoiceflow import cord

    project = make_project(tmp_path)
    view = dataclasses.replace(__import__("invoiceflow.config", fromlist=["SealedConfig"]).SealedConfig.of(project.cfg, tuple("0" * 64 for _ in project.cfg.source.cord_files), "0" * 64))
    opened: list[str] = []
    real = cord._iter_rows
    monkeypatch.setattr(cord, "_iter_rows", lambda path, *a, **k: (opened.append(path.name), real(path, *a, **k))[1])
    chosen, checkpoint = Reads({"total": {"total_price": "1"}}), Reads({})
    with pytest.raises(LeakageError, match="cierre"):
        evaluation.evaluate_cord(project.cfg, view, chosen, checkpoint, closing_run=False)
    assert opened == [] and chosen.asked == 0, "refused before a row was opened"
    figures, rows = evaluation.evaluate_cord(project.cfg, view, chosen, checkpoint, closing_run=True)
    assert opened == [FILE_NAMES["test"]] and figures["receipts"] == 4 and chosen.asked == checkpoint.asked == 4 and len(rows) == 8
    assert {r["model"] for r in rows} == {"chosen", "checkpoint"} and all(r["receipt_key"].startswith("test-") for r in rows)
    assert figures["ted_chosen"] == pytest.approx(sum(r["ted"] for r in rows if r["model"] == "chosen") / 4)
    assert figures["diff"]["value"] == pytest.approx(figures["ted_chosen"] - figures["ted_checkpoint"]) and figures["ted_checkpoint"] == 0.0
    assert figures["diff"]["ci_low"] <= figures["diff"]["value"] <= figures["diff"]["ci_high"]


def test_the_evaluation_is_stored_once_sealed_once_and_a_repetition_reproduces_it(perfect, tmp_path, monkeypatch):
    world, conn = perfect
    project = make_project(tmp_path / "project")
    view = world.view
    seal = tmp_path / "reports" / "model_seal.json"
    write_launch_seal(seal, launch_document(view))
    chosen, checkpoint = Reads({"total": {"total_price": "1"}}), Reads({})
    run = RunMeta("close-1", datetime(2026, 10, 6))
    args = dict(seal_path=seal, chosen=chosen, checkpoint=checkpoint, repeat_new=0)
    out = reporting.evaluate(conn, project.cfg, view, run, world.master, world.all_truths(), **args)
    assert out["state"] == "first" and out["seal"] == "written" and out["rows"]["eval_criteria"] == 10 and out["rows"]["eval_invoice"] == 18
    sealed = read_seal(seal)["test"]
    assert [c["id"] for c in sealed["criteria"]] == [f"C{n:02d}" for n in range(1, 11)] and sealed["metrics"]["days"] == 3 and sealed["cord"]["receipts"] == 4
    assert conn.execute("SELECT count(*) FROM eval_criteria WHERE seal_state = 'sealed'").fetchone()[0] == 10
    with pytest.raises(Exception, match="solo inserción"):
        conn.execute("UPDATE eval_criteria SET passed = 1 WHERE criterion_id = 'C03'")
    with pytest.raises(Exception, match="solo inserción"):
        conn.execute("INSERT OR REPLACE INTO eval_criteria SELECT * FROM eval_criteria WHERE criterion_id = 'C01'")
    kept = seal.read_bytes()
    again = reporting.evaluate(conn, project.cfg, view, RunMeta("close-2", datetime(2026, 10, 7)), world.master, world.all_truths(), **args)
    assert again["state"] == "repeat" and again["seal"] == "unchanged" and again["rows"] == {} and seal.read_bytes() == kept
    summary = reporting.build_summary(conn, view, seal_path=seal)
    assert summary["source"] == "warehouse" and [c["criterion_id"] for c in summary["criteria"]] == [f"C{n:02d}" for n in range(1, 11)]
    text = reporting.summary_markdown(summary)
    assert "C05" in text and "(sintético)" in text and reporting.write_summary(summary, tmp_path / "reports").read_text(encoding="utf-8") == text


def test_a_bad_post_is_an_auto_post_with_a_planted_discrepancy_or_a_qr_or_lines_that_are_not_the_truths(perfect):
    """The definition of requirements 8.1, one condition at a time (the flow posts nothing bad when it reads perfectly, so a perfect world cannot show them; this is the function that judges)."""
    from invoiceflow.baselines import truth_fields, truth_qr
    from invoiceflow.normalize import InvoiceFields

    world, _ = perfect
    clean = next(t for t in world.all_truths() if t.planted == "none" and t.qr_text is not None and len(t.lines) > 1)
    qr, fields = truth_qr(clean, world.view), truth_fields(clean)
    judge = evaluation._bad_post
    assert judge(clean, "auto_post", qr, fields, qr) is False and judge(clean, "review", qr, fields, qr) is False, "review posts nothing, so nothing is bad"
    assert judge(dataclasses.replace(clean, planted="duplicate"), "auto_post", qr, fields, qr) is True, "(a) an auto_post that had a discrepancy planted"
    for change in ({"importe": clean.totals.total + 1}, {"nro_cmp": clean.header.nro + 1}, {"pto_vta": clean.header.pto_vta + 1}, {"cuit": "30000000015"},
                   {"moneda": "DOL" if clean.header.moneda == "PES" else "PES"}, {"fecha": clean.header.fecha.replace(year=2025)}):
        moved = dataclasses.replace(qr, payload=dataclasses.replace(qr.payload, **change))
        assert judge(clean, "auto_post", moved, fields, qr) is True, change
    assert judge(clean, "auto_post", dataclasses.replace(qr, payload=None, status="not_read"), fields, qr) is True, "posted with no QR header at all"
    off = dataclasses.replace(fields, lines=(dataclasses.replace(fields.lines[0], cantidad=fields.lines[0].cantidad + 1), *fields.lines[1:]))
    assert judge(clean, "auto_post", qr, off, qr) is True, "(b) a line that is not the truth's"
    assert judge(clean, "auto_post", qr, dataclasses.replace(fields, lines=fields.lines[:-1]), qr) is True, "a line missing"
    assert judge(clean, "auto_post", qr, dataclasses.replace(fields, lines=(*fields.lines, fields.lines[0])), qr) is True, "a line of more"
    assert isinstance(InvoiceFields(), InvoiceFields)


def test_a_qr_that_was_not_read_is_not_a_read_one_and_the_page_goes_to_review(quick_view, tmp_path_factory, monkeypatch):
    from invoiceflow.qr import QrRead
    from tests.fixtures.stub_extractor import page_key

    world = build_world(quick_view, tmp_path_factory.mktemp("eval_qr_world"))
    victim = next(t for t in world.all_truths() if t.planted == "none" and t.qr_text is not None)
    blind = next(k for k, v in world.qr_by_page.items() if v.payload is not None and v.payload.nro_cmp == victim.header.nro and v.payload.cuit == victim.header.cuit_emisor)
    other = dict(world.qr_by_page)
    other[blind] = QrRead(None, "not_read", None, None)
    world = dataclasses.replace(world, qr_by_page=other)
    conn, _ = run_world(world, tmp_path_factory.mktemp("eval_qr_run"), monkeypatch)
    result = evaluation.evaluate_test(conn, world.view, world.master, world.all_truths(), repeat_new=0)
    row = next(r for r in result.invoices if r["invoice_key"] == victim.invoice_key)
    printed = sum(1 for t in world.all_truths() if t.qr_text is not None)
    assert (row["qr_printed"], row["qr_read_ok"], row["decision"], row["first_reason"]) == (1, 0, "review", "qr_not_read")
    assert result.figures["qr"]["printed"] == printed and result.figures["qr"]["read"] == printed - 1 and result.figures["qr"]["rate"] == pytest.approx((printed - 1) / printed)


# --- the analyses after the seal (D59, D60): beside the sealed figures, never in their place ---------------------------------------------


def test_c08_as_preregistered_is_computed_beside_the_sealed_one_from_the_same_decisions(perfect):
    """B1 of the evaluation (D59): requirements 8.1 define C08 over the bad-post rates (bad posts over auto-posts) of B1 and the flow; the sealed C08 is the difference per invoice. The analysis
    takes the counts the sealed baselines have, and the sealed figure is what it was."""
    world, conn = perfect
    truths = world.all_truths()
    sealed = evaluation.evaluate_test(conn, world.view, world.master, truths, repeat_new=0)
    analysis = evaluation.post_seal_analysis(conn, world.view, world.master, truths)
    b1, flow = sealed.figures["baselines"]["b1:all"], sealed.figures["baselines"]["flow:all"]
    pre = analysis["c08_preregistered"]
    assert analysis["sealed"] is False and analysis["synthetic"] is True and analysis["invoices"] == len(truths) == 18
    assert (pre["b1"]["posts"], pre["b1"]["bad"], pre["flow"]["posts"], pre["flow"]["bad"]) == (b1["auto_post"], b1["bad_post"], flow["auto_post"], flow["bad_post"])
    assert b1["auto_post"] > 0 and pre["value"] == pytest.approx(b1["bad_post"] / b1["auto_post"] - flow["bad_post"] / flow["auto_post"])
    assert pre["ci_low"] <= pre["value"] <= pre["ci_high"] and pre["passed"] is (pre["ci_low"] > 0)
    assert evaluation.evaluate_test(conn, world.view, world.master, truths, repeat_new=0).figures == sealed.figures, "computing the analysis changes nothing that is sealed"


def test_the_close_writes_the_analysis_beside_the_seal_and_the_summary_tells_c08_per_invoice_with_the_sealed_sentence_kept(perfect, tmp_path):
    from invoiceflow import texts

    world, conn = perfect
    project = make_project(tmp_path / "project")
    seal = tmp_path / "reports" / "model_seal.json"
    write_launch_seal(seal, launch_document(world.view))
    args = dict(seal_path=seal, chosen=Reads({"total": {"total_price": "1"}}), checkpoint=Reads({}), repeat_new=0)
    reporting.evaluate(conn, project.cfg, world.view, RunMeta("close-1", datetime(2026, 10, 6)), world.master, world.all_truths(), **args)
    kept, analysis_path = seal.read_bytes(), seal.with_name("post_seal_analysis.json")
    first = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert first["seal_sha256"] == __import__("hashlib").sha256(kept).hexdigest() and first["c08_preregistered"]["b1"]["posts"] > 0
    reporting.evaluate(conn, project.cfg, world.view, RunMeta("close-2", datetime(2026, 10, 7)), world.master, world.all_truths(), **args)
    assert seal.read_bytes() == kept and json.loads(analysis_path.read_text(encoding="utf-8")) == first, "a repetition leaves the seal and rewrites the same analysis"
    summary = reporting.build_summary(conn, world.view, seal_path=seal)
    assert summary["post_seal_analysis"] == first
    c08 = next(c for c in summary["criteria"] if c["criterion_id"] == "C08")
    sealed_c08 = next(c for c in read_seal(seal)["test"]["criteria"] if c["id"] == "C08")
    metrics_ = read_seal(seal)["test"]["metrics"]
    assert c08["sealed_verdict_es"] == sealed_c08["verdict_es"] and c08["verdict_es"] != sealed_c08["verdict_es"] and c08["passed"] is sealed_c08["passed"]
    b1_bad, invoices = metrics_["baselines"]["b1:all"]["bad_post"], metrics_["bad_post"]["invoices"]
    assert f"cargó mal {b1_bad} de las {invoices}" in c08["verdict_es"] and "por factura" in c08["verdict_es"]
    assert texts.es(metrics_["b1_minus_flow"]["value"] * 100, 1) in c08["verdict_es"] and "%" not in c08["verdict_es"].split("por factura")[0], "per invoice: counts, not rates per post"
    assert all("sealed_verdict_es" not in c for c in summary["criteria"] if c["criterion_id"] != "C08")
    text = reporting.summary_markdown(summary)
    assert "| ID | Criterio | Lo que prueba el umbral | Veredicto |" in text and "Análisis posteriores al sello" in text and "| Valor |" not in text
    assert texts.es(first["c08_preregistered"]["value"] * 100, 1) in text and "texto sellado" in text


def test_b1_cites_the_order_number_the_model_read_and_the_sealed_b1_is_kept_beside_it(quick_view, tmp_path_factory, monkeypatch):
    """B2 of the evaluation (D60): D48 says B1 takes the order number from what the model read; the sealed B1 built the view from the truth's order. With the order misread on a clean invoice
    that the sealed B1 posts, B1 under D48 finds no order and sends it to review; the analysis publishes B1 under D48 with its C08, both ways, and the sealed figures do not move."""
    from tests.fixtures.stub_extractor import ORDER_MISREAD

    base = build_world(quick_view, tmp_path_factory.mktemp("eval_order_base"))
    base_conn, _ = run_world(base, tmp_path_factory.mktemp("eval_order_base_run"), monkeypatch)
    sealed_b1 = evaluation.b1_decisions(base_conn, base.view, base.master, base.all_truths(), order="truth")
    victim = next(t for t in base.all_truths() if t.planted == "none" and sealed_b1[t.invoice_key].decision == "auto_post")
    world = build_world(quick_view, tmp_path_factory.mktemp("eval_order_world"), errors={victim.invoice_key: ORDER_MISREAD})
    conn, _ = run_world(world, tmp_path_factory.mktemp("eval_order_run"), monkeypatch)
    truths = world.all_truths()
    assert evaluation.read_fields(conn, victim.invoice_key).orden_compra == "OC-0000-000000", "the model misread the order number"
    as_sealed = evaluation.b1_decisions(conn, world.view, world.master, truths, order="truth")
    as_read = evaluation.b1_decisions(conn, world.view, world.master, truths, order="read")
    assert as_sealed[victim.invoice_key].decision == "auto_post" and as_read[victim.invoice_key].decision == "review"
    assert {k for k in as_read if as_read[k].decision != as_sealed[k].decision} == {victim.invoice_key}
    sealed = evaluation.evaluate_test(conn, world.view, world.master, truths, repeat_new=0)
    assert sealed.figures["baselines"]["b1:all"]["auto_post"] == sum(1 for d in as_sealed.values() if d.decision == "auto_post"), "the seal keeps the B1 it computed"
    analysis = evaluation.post_seal_analysis(conn, world.view, world.master, truths)
    read = analysis["b1_read_order"]
    assert read["baselines"]["all"]["auto_post"] == sealed.figures["baselines"]["b1:all"]["auto_post"] - 1
    assert {c: read["baselines"][c]["auto_post"] for c in ("test_known", "test_new")} == {
        c: sum(1 for t in truths if t.split == c and as_read[t.invoice_key].decision == "auto_post") for c in ("test_known", "test_new")}
    per_invoice, per_post = read["c08_per_invoice"], read["c08_preregistered"]
    flow_bad = sealed.figures["baselines"]["flow:all"]["bad_post"]
    assert per_invoice["b1_bad"] == read["baselines"]["all"]["bad_post"] and per_invoice["value"] == pytest.approx((per_invoice["b1_bad"] - flow_bad) / len(truths))
    assert per_post["b1"]["posts"] == read["baselines"]["all"]["auto_post"] and per_post["b1"]["bad"] == read["baselines"]["all"]["bad_post"]
    assert analysis["decisions"] == ["D59", "D60"] and analysis["c08_preregistered"]["b1"]["posts"] == sealed.figures["baselines"]["b1:all"]["auto_post"]
