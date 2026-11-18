"""The choice of the epoch (AC18; plan section 9.3; decision D12): measured on the validation partitions only, never on a test one.

Each candidate epoch brings three figures: the tree-edit-distance accuracy (TED) on CORD's validation and the exact-line rate on ``validation_known`` and on ``validation_new``.
A candidate is **dropped** when its TED is below the checkpoint's own on the same validation minus ``[model] cord_val_tolerance`` (it forgot CORD; the border, equal to the
floor, stays). Among the others the one with the best **mean of the two synthetic rates** wins; scores that differ by at most a millionth are a tie and the earliest epoch wins.
With none left the launch stops (``ModelChoiceError``, M04, runbook R5). Every figure is kept with ``fit_splits``, the partitions the model that produced it was fitted with
(AC22), so that no validation figure is read as out of sample for a partition it saw.

The floor is computed in ``Decimal`` from the text of the two floats, so that ``93.65 - 2.0`` is exactly ``91.65`` and not ``91.65000000000001``. A figure that is not a finite number is
an error, not a comparison: ``nan < floor`` is false and would let a candidate through the filter.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from invoiceflow.config import SealedConfig

FIT_SPLITS = "cord_train+synth_train"
TIE = Decimal("0.000001")


class ModelChoiceError(RuntimeError):
    """No candidate epoch keeps CORD, or the figures cannot be compared. The launch stops (exit code 2, runbook R5)."""

    EXIT_CODE = 2


@dataclass(frozen=True)
class Candidate:
    """The validation figures of one candidate epoch: TED on CORD's validation (points, 0-100) and the exact-line rates (0-1) on the two synthetic validations."""

    epoch: int
    cord_ted: float
    exact_known: float
    exact_new: float
    fit_splits: str = FIT_SPLITS


@dataclass(frozen=True)
class Choice:
    epoch: int
    score: float                           # the mean of the two synthetic rates of the chosen epoch
    floor: float                           # the checkpoint's TED minus the tolerance
    checkpoint_ted: float
    tolerance: float
    kept: tuple[int, ...]
    dropped: tuple[int, ...]
    candidates: tuple[Candidate, ...]      # in epoch order

    def to_jsonable(self) -> dict[str, Any]:
        """What the registry stores (``choice_json``): the rule's inputs and each candidate with its figures, its score, its status and its ``fit_splits``."""
        return {
            "chosen_epoch": self.epoch, "score": self.score, "floor": self.floor, "checkpoint_cord_ted": self.checkpoint_ted, "cord_val_tolerance": self.tolerance,
            "candidates": [{"epoch": c.epoch, "cord_ted": c.cord_ted, "exact_known": c.exact_known, "exact_new": c.exact_new, "score": _score(c),
                            "status": "kept" if c.epoch in self.kept else "dropped_cord", "fit_splits": c.fit_splits} for c in self.candidates],
        }


def _score(candidate: Candidate) -> float:
    return float((_d(candidate.exact_known) + _d(candidate.exact_new)) / 2)


def _d(value: float) -> Decimal:
    return Decimal(repr(float(value)))


def _check(candidates: Iterable[Candidate], checkpoint_ted: float) -> list[Candidate]:
    if not math.isfinite(checkpoint_ted):
        raise ModelChoiceError(f"el TED del checkpoint en la validación de CORD no es un número finito ({checkpoint_ted!r})")
    ordered = sorted(candidates, key=lambda c: c.epoch)
    epochs = [c.epoch for c in ordered]
    if len(set(epochs)) != len(epochs):
        raise ModelChoiceError(f"hay épocas candidatas repetidas: {epochs}")
    for c in ordered:
        for name, value in (("cord_ted", c.cord_ted), ("exact_known", c.exact_known), ("exact_new", c.exact_new)):
            if not (isinstance(value, (int, float)) and math.isfinite(value)):
                raise ModelChoiceError(f"la época {c.epoch} tiene {name} = {value!r}: no es un número finito y no se compara")
        for name, value in (("exact_known", c.exact_known), ("exact_new", c.exact_new)):
            if not 0 <= value <= 1:
                raise ModelChoiceError(f"la época {c.epoch} tiene la tasa {name} = {value!r}, fuera de 0 a 1")
    return ordered


def choose(candidates: Iterable[Candidate], checkpoint_ted: float, view: SealedConfig) -> Choice:
    """The epoch of the rule above for `candidates`; ``ModelChoiceError`` when none keeps CORD or a figure is not a finite number."""
    ordered = _check(candidates, checkpoint_ted)
    tolerance = view.model.cord_val_tolerance
    floor = _d(checkpoint_ted) - _d(tolerance)
    kept = [c for c in ordered if _d(c.cord_ted) >= floor]
    dropped = tuple(c.epoch for c in ordered if _d(c.cord_ted) < floor)
    if not kept:
        raise ModelChoiceError(f"ninguna época candidata conserva CORD: todas están por debajo de {floor} (el TED del checkpoint, {checkpoint_ted}, menos {tolerance}); "
                               f"épocas: {[c.epoch for c in ordered] or 'ninguna'} (runbook R5)")
    best = max(_d(c.exact_known) + _d(c.exact_new) for c in kept) / 2
    winner = next(c for c in kept if best - (_d(c.exact_known) + _d(c.exact_new)) / 2 <= TIE)   # kept is in epoch order: the earliest of the tied
    return Choice(winner.epoch, _score(winner), float(floor), checkpoint_ted, tolerance, tuple(c.epoch for c in kept), dropped, tuple(ordered))
