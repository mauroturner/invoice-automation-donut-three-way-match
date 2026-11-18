"""The oracle B3 (U11; AC13, AC25 in part; M06; plan section 15, decision D18): the true fields of every synthetic invoice, the true QR and an ERP view made from the master, through the same
``decide`` the flow uses. It is the proof that the rules say what the generator meant: every planted discrepancy gives its expected first reason and every invoice without one is posted. The
generator's own default configuration is used (2,300 invoices of the launch partitions, each of the 13 classes between 40 and 57 times)."""

from __future__ import annotations

import random
from collections import Counter

import pytest

from invoiceflow.baselines import oracle
from invoiceflow.synth.invoices import FIT_SPLITS, PLANTED_CLASSES, invoices


@pytest.fixture(scope="module")
def truths(view, default_master, tmp_path_factory):
    seal = tmp_path_factory.mktemp("seal") / "none.json"
    return [t for split in FIT_SPLITS for t in invoices(view, default_master, split, None, seal_path=seal)]


@pytest.fixture(scope="module")
def decisions(view, default_master, truths):
    return oracle(view, default_master, truths)


def test_the_oracle_sends_every_planted_invoice_to_review(truths, decisions):
    """M06, AC25: with the true fields, 100 % of the planted invoices are review and every invoice with nothing planted is posted; all thirteen classes are there to be tested."""
    assert set(decisions) == {t.invoice_key for t in truths} and len(decisions) == 2300
    assert {t.planted for t in truths} == {"none", *PLANTED_CLASSES}
    wrong = [(t.invoice_key, t.planted, decisions[t.invoice_key].decision, [r.code for r in decisions[t.invoice_key].reasons][:3]) for t in truths
             if decisions[t.invoice_key].decision != t.expected_decision]
    assert wrong == [], f"{len(wrong)} of {len(truths)} invoices got another decision than the generator meant, for example {wrong[:5]}"
    planted = [t for t in truths if t.planted != "none"]
    assert planted and all(decisions[t.invoice_key].decision == "review" for t in planted)
    assert all(decisions[t.invoice_key].decision == "auto_post" for t in truths if t.planted == "none")


def test_every_planted_class_gives_its_expected_first_reason_with_the_true_fields(truths, decisions):
    """AC13: ``expected_reasons`` is what the generator wanted the rules to say first (the first two for ``over_order``). The oracle's first reasons are those, class by class."""
    bad = Counter()
    for t in truths:
        if t.planted == "none":
            continue
        got = [r.code for r in decisions[t.invoice_key].reasons]
        if got[:len(t.expected_reasons)] != list(t.expected_reasons):
            bad[t.planted] += 1
    assert not bad, f"classes whose first reasons are not the expected ones: {dict(bad)}"
    by_class = Counter(t.planted for t in truths)
    assert min(by_class[c] for c in PLANTED_CLASSES) >= 30


def test_the_oracle_does_not_depend_on_the_order_the_invoices_are_handed_over(view, default_master, truths, decisions):
    """It processes them in the order they reach accounts payable (received_on, then partition and ordinal), so a duplicate always comes after what it duplicates."""
    shuffled = list(truths)
    random.Random(5).shuffle(shuffled)
    assert oracle(view, default_master, shuffled) == decisions


def test_a_duplicate_is_seen_only_after_the_invoice_it_repeats_was_posted(view, default_master, truths):
    originals = {t.header.nro: t for t in truths if t.planted == "none"}
    pairs = [(originals[t.header.nro], t) for t in truths if t.planted == "duplicate" and t.header.nro in originals]
    assert len(pairs) >= 30
    alone = oracle(view, default_master, [t for _, t in pairs[:5]])
    assert all(alone[t.invoice_key].first_reason != "duplicate" for _, t in pairs[:5]), "with no earlier voucher in the world there is nothing to duplicate"
    together = oracle(view, default_master, [x for pair in pairs[:5] for x in pair])
    assert all(together[t.invoice_key].first_reason == "duplicate" for _, t in pairs[:5])
