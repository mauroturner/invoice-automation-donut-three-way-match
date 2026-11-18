"""The choice of the epoch (U10; AC18; plan section 9.3, decision D12): among the candidate epochs that keep CORD (their TED on CORD's validation is not below the
checkpoint's minus ``cord_val_tolerance``), the one with the best mean of the two synthetic exact-line rates; a tie to a millionth goes to the earliest epoch; none valid
is ``ModelChoiceError``. The hand cases are those of ``tests/fixtures/hand_cases.py``."""

from __future__ import annotations

import dataclasses
import json
import math
from types import SimpleNamespace

import pytest

from invoiceflow.model.choose import FIT_SPLITS, Candidate, Choice, ModelChoiceError, choose
from tests.fixtures import hand_cases as hc


def candidates_of(table):
    return [Candidate(epoch, ted, known, new) for epoch, (ted, known, new) in table.items()]


def with_tolerance(view, tolerance):
    return dataclasses.replace(view, model=dataclasses.replace(view.model, cord_val_tolerance=tolerance))


def test_a_candidate_that_loses_cord_is_never_chosen(view):
    """Epoch 6 reads the invoices best (0.99 and 0.90) but its TED on CORD (90.00) is below 93.65 - 2.0 = 91.65: it is dropped, whatever it scores elsewhere."""
    choice = choose(candidates_of(hc.CANDIDATES), hc.CHECKPOINT_TED, view)
    assert isinstance(choice, Choice)
    assert choice.epoch == hc.CHOSEN_EPOCH == 4
    assert choice.dropped == (6,) and choice.kept == (2, 4)
    assert choice.epoch not in choice.dropped


def test_the_score_is_the_mean_of_the_two_synthetic_rates_and_the_best_of_the_kept_wins(view):
    choice = choose(candidates_of(hc.CANDIDATES), hc.CHECKPOINT_TED, view)
    scores = {c["epoch"]: c["score"] for c in choice.to_jsonable()["candidates"]}
    assert scores[4] == pytest.approx(0.825) and scores[2] == pytest.approx(0.70) and scores[6] == pytest.approx(0.945)
    assert choice.score == pytest.approx(0.825)


def test_a_tie_to_a_millionth_goes_to_the_earliest_epoch(view):
    assert choose(candidates_of(hc.TIE_CANDIDATES), hc.CHECKPOINT_TED, view).epoch == hc.TIE_CHOSEN_EPOCH == 2
    nearly = {2: (93.0, 0.80, 0.80), 4: (93.0, 0.80 + 5e-7, 0.80 + 5e-7), 6: (93.0, 0.7, 0.7)}
    assert choose(candidates_of(nearly), hc.CHECKPOINT_TED, view).epoch == 2, "a difference under 1e-6 is a tie"
    clearly = {2: (93.0, 0.80, 0.80), 4: (93.0, 0.80 + 5e-6, 0.80 + 5e-6)}
    assert choose(candidates_of(clearly), hc.CHECKPOINT_TED, view).epoch == 4, "a difference over 1e-6 is not"


def test_no_valid_candidate_stops_the_launch_with_model_choice_error(view):
    with pytest.raises(ModelChoiceError, match=r"ninguna.*91[.,]65"):
        choose(candidates_of(hc.NONE_VALID_CANDIDATES), hc.CHECKPOINT_TED, view)
    with pytest.raises(ModelChoiceError):
        choose([], hc.CHECKPOINT_TED, view)


def test_the_error_of_a_launch_with_no_valid_candidate_says_what_each_epoch_scored_on_cord():
    """U22: the first real launch ended here after more than an hour of fine-tune and said only the floor; the figures of the epochs went with the work folder. The message carries them."""
    view = SimpleNamespace(model=SimpleNamespace(cord_val_tolerance=2.0))
    with pytest.raises(ModelChoiceError) as error:
        choose(candidates_of(hc.NONE_VALID_CANDIDATES), hc.CHECKPOINT_TED, view)
    assert "época 2: 91.0" in str(error.value) and "época 4: 90.5" in str(error.value)


def test_the_floor_is_the_checkpoint_minus_the_tolerance_and_its_border_is_inside(view):
    at_the_floor = {2: (91.65, 0.5, 0.5), 4: (91.649, 0.9, 0.9)}
    choice = choose(candidates_of(at_the_floor), hc.CHECKPOINT_TED, view)
    assert choice.epoch == 2 and choice.dropped == (4,), "91.65 is exactly 93.65 - 2.0 and stays; 91.649 does not"
    assert choice.floor == pytest.approx(91.65)


def test_the_tolerance_comes_from_the_configuration(view):
    wide = choose(candidates_of(hc.CANDIDATES), hc.CHECKPOINT_TED, with_tolerance(view, 5.0))
    assert wide.epoch == 6 and wide.dropped == (), "with 5 points epoch 6 (90.00) keeps CORD and reads best"
    with pytest.raises(ModelChoiceError):
        choose(candidates_of(hc.CANDIDATES), hc.CHECKPOINT_TED, with_tolerance(view, 0.5))   # only 93.15 or more: epoch 2 has 93.00


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_figure_that_is_not_a_finite_number_is_refused_not_compared(view, bad):
    """``nan < floor`` is false: a candidate whose TED is nan would otherwise pass the CORD filter. A rule that cannot be evaluated is an error."""
    table = {2: (bad, 0.9, 0.9), 4: (92.0, 0.5, 0.5)}
    with pytest.raises(ModelChoiceError, match="2"):
        choose(candidates_of(table), hc.CHECKPOINT_TED, view)
    table = {2: (93.0, bad, 0.9), 4: (92.0, 0.5, 0.5)}
    with pytest.raises(ModelChoiceError):
        choose(candidates_of(table), hc.CHECKPOINT_TED, view)
    with pytest.raises(ModelChoiceError):
        choose(candidates_of({2: (93.0, 0.9, 0.9)}), bad, view)


def test_a_repeated_epoch_or_a_rate_outside_zero_one_is_refused(view):
    with pytest.raises(ModelChoiceError, match="repet"):
        choose([Candidate(2, 93.0, 0.5, 0.5), Candidate(2, 93.0, 0.6, 0.6)], hc.CHECKPOINT_TED, view)
    with pytest.raises(ModelChoiceError, match="tasa"):
        choose([Candidate(2, 93.0, 1.5, 0.5)], hc.CHECKPOINT_TED, view)


def test_every_figure_is_kept_with_the_splits_its_model_saw(view):
    """AC22: a validation figure says with which partitions the model that produced it was fitted (here, CORD train and the synthetic train)."""
    choice = choose(candidates_of(hc.CANDIDATES), hc.CHECKPOINT_TED, view)
    document = choice.to_jsonable()
    assert FIT_SPLITS == "cord_train+synth_train"
    assert document["checkpoint_cord_ted"] == hc.CHECKPOINT_TED and document["chosen_epoch"] == 4 and document["cord_val_tolerance"] == 2.0
    assert [c["epoch"] for c in document["candidates"]] == [2, 4, 6]
    assert all(c["fit_splits"] == FIT_SPLITS for c in document["candidates"])
    assert {c["epoch"]: c["status"] for c in document["candidates"]} == {2: "kept", 4: "kept", 6: "dropped_cord"}
    json.dumps(document)   # it is what the registry stores


def test_the_choice_does_not_depend_on_the_order_of_the_candidates(view):
    forward = candidates_of(hc.CANDIDATES)
    assert choose(list(reversed(forward)), hc.CHECKPOINT_TED, view).epoch == choose(forward, hc.CHECKPOINT_TED, view).epoch
    assert choose(list(reversed(forward)), hc.CHECKPOINT_TED, view).to_jsonable() == choose(forward, hc.CHECKPOINT_TED, view).to_jsonable()
