"""The criteria and the texts that tell them (U16; AC22; plan section 16, decisions D19 and D20): each verdict is built from its own figures and names the comparison it makes, a criterion that cannot be
computed is never passed, a tie never beats, and a figure from synthetic data says so. The figures are worked out by hand."""

from __future__ import annotations

import copy
import dataclasses

import pytest

from invoiceflow import texts
from invoiceflow.evaluation import criteria_of
from invoiceflow.texts import es
from tests.fixtures.figures import LIMITS, figures



def by_id(criteria):
    return {c.criterion_id: c for c in criteria}


def test_each_verdict_names_its_own_comparison():
    got = by_id(criteria_of(figures(), LIMITS))
    assert [c.criterion_id for c in got.values()] == [f"C{n:02d}" for n in range(1, 11)]
    assert got["C01"].passed is True and "TED" in got["C01"].verdict_es and "checkpoint" in got["C01"].verdict_es and "91,2" in got["C01"].verdict_es and "93,0" in got["C01"].verdict_es
    assert got["C02"].verdict_es == "Sobre 300 facturas sintéticas de proveedores conocidos, el 93,1 % de las líneas se leyó exacto; el umbral era 90 %."
    assert got["C03"].passed is False and "diseño nuevo" in got["C03"].verdict_es and "68,3 %" in got["C03"].verdict_es and "80 %" in got["C03"].verdict_es
    assert got["C04"].passed is True and "99,1 %" in got["C04"].verdict_es and "535" in got["C04"].verdict_es
    assert got["C05"].passed is True and "0,95 %" in got["C05"].verdict_es and "2 %" in got["C05"].verdict_es and "0 de 400" in got["C05"].verdict_es
    assert got["C06"].passed is True and "92,1 %" in got["C06"].verdict_es and "85 %" in got["C06"].verdict_es
    assert got["C07"].passed is True and "80 de 80" in got["C07"].verdict_es and "99 %" in got["C07"].verdict_es
    assert got["C08"].passed is True and "B1" in got["C08"].verdict_es and "6,0 %" in got["C08"].verdict_es and "1,0 %" in got["C08"].verdict_es
    assert got["C09"].passed is True and "400 de 400" in got["C09"].verdict_es
    assert got["C10"].passed is None and "informativ" in got["C10"].verdict_es.lower()
    for cid in ("C02", "C03", "C05", "C06", "C07", "C08"):
        assert "sintétic" in got[cid].verdict_es, cid
    assert "sintétic" not in got["C01"].verdict_es and "sintétic" not in got["C09"].verdict_es.replace("sintético", ""), "CORD's figure is real data"
    assert got["C02"].threshold_es.startswith("Tasa de líneas exactas") or "≥" in got["C02"].threshold_es
    assert (got["C03"].seen_by_research, got["C03"].seen_by_planner) == ("no", "no") and got["C01"].seen_by_research == "sí"


def test_a_limit_that_is_reached_passes_where_the_criterion_says_at_least_or_at_most_and_a_tie_never_beats():
    exact = figures(lines={"known": {"exact": 270, "lines": 300, "rate": 0.90, "ci_low": 0.86, "ci_high": 0.93, "invoices": 300}, "new": {"exact": 240, "lines": 300, "rate": 0.80, "ci_low": 0.75, "ci_high": 0.84, "invoices": 300}},
                    bad_post={"posts": 400, "bad": 8, "rate": 0.02, "ci_low": 0.01, "ci_high": 0.02, "invoices": 600},
                    cord={"receipts": 100, "ted_chosen": 91.0, "ted_checkpoint": 93.0, "diff": {"value": -2.0, "ci_low": -2.0, "ci_high": -1.0}},
                    b1_minus_flow={"value": 0.0, "ci_low": 0.0, "ci_high": 0.02, "b1_rate": 0.02, "flow_rate": 0.02})
    got = by_id(criteria_of(exact, LIMITS))
    assert got["C02"].passed and got["C03"].passed and got["C05"].passed, "at least / at most: the limit itself passes"
    assert got["C01"].passed is False, "the lower limit of the difference must be above -2.0, not equal to it"
    assert got["C08"].passed is False, "a tie with B1 does not beat it"


def test_a_criterion_that_cannot_be_computed_is_not_passed_and_says_why():
    empty = by_id(criteria_of({"variants": 0, "days": 0}, LIMITS))
    for cid in [f"C{n:02d}" for n in range(1, 10)]:
        assert empty[cid].passed is False and empty[cid].value is None and "no se puede calcular" in empty[cid].verdict_es, cid
    no_posts = by_id(criteria_of(figures(bad_post={"posts": 0, "bad": 0, "rate": None, "ci_low": None, "ci_high": None, "invoices": 600}, erp={"posts": 0, "readback_equal": 0, "repeat_new": 0}), LIMITS))
    assert no_posts["C05"].passed is False and no_posts["C09"].passed is False, "nothing was posted: nothing to prove, not a pass"
    broken = by_id(criteria_of(figures(erp={"posts": 400, "readback_equal": 399, "repeat_new": 0}), LIMITS))
    assert broken["C09"].passed is False and "399 de 400" in broken["C09"].verdict_es
    repeated = by_id(criteria_of(figures(erp={"posts": 400, "readback_equal": 400, "repeat_new": 2}), LIMITS))
    assert repeated["C09"].passed is False and "2" in repeated["C09"].verdict_es


def test_the_numbers_of_a_text_are_written_the_way_spanish_does():
    assert es(0.931, 1, percent=True) == "93,1 %" and es(1234.5, 2) == "1.234,50" and es(-1.8, 1) == "−1,8" and es(None) == "sin dato" and es(float("nan")) == "sin dato"
    assert es(-0.0001, 2) == "0,00", "a number that rounds to zero has no minus sign"
    assert es(0.02, 0, percent=True) == "2 %" and es(0.0095, 2, percent=True) == "0,95 %"


def test_the_verdict_of_a_failed_criterion_says_it_did_not_pass_and_the_one_of_a_passed_one_does_not():
    got = by_id(criteria_of(figures(), LIMITS))
    assert "no se cumple" in got["C03"].verdict_es.lower() or "no alcanzó" in got["C03"].verdict_es.lower() or "umbral era" in got["C03"].verdict_es
    changed = copy.deepcopy(figures())
    changed["lines"]["known"].update(exact=240, rate=0.80)
    assert by_id(criteria_of(changed, LIMITS))["C02"].passed is False and "80 %" in by_id(criteria_of(changed, LIMITS))["C02"].verdict_es
    assert texts.CRITERIA["C10"].threshold_es.startswith("sin umbral")
