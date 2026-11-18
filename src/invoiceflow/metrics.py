"""The measures of the document model (RF16, AC18, AC22 in part; plan sections 9.3 and 16; decision D12): what the launch chooses an epoch with and what the closing evaluation reports.

* ``ted_accuracy`` is the research's (``research/s02``): ``1 - TED(prediction, truth) / TED(nothing, truth)`` over ordered trees (a dict's keys are nodes, **taken in sorted order: the order of the keys of an
  object is not a fact of the receipt, and the canonical target and the model write them in different ones (U22)**; a list's items are nodes
  called ``item``, in their order, a scalar is a leaf holding its normal form), with the tree edit distance of ``zss``. It is a reimplementation, not the authors' script.
* ``parse_money`` and ``normalise`` read CORD's annotated amounts in the formats they come in (``60.000``, ``Rp 16.500``, ``1,591,600``, ``226,500.00``, ``2.00``) as ``Decimal``: no
  ``float`` is made, and a leaf compares by its normal form (an amount to cents, a text in lower case with its whitespace collapsed).
* ``leaf_match`` is the recall of the leaves, by path, with repeated leaves counted once each.
* ``exact_lines`` counts the lines of an invoice that were read exactly: the code, the quantity, the unit price and the amount are the truth's, after normalizing. A read line is matched with
  a line of the truth wherever it was read and is used once.

U16 adds the intervals and the rates of the closing evaluation to this module.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from zss import Node, simple_distance

from invoiceflow.normalize import InvoiceFields

CENT = Decimal("0.01")
_COMMA_THOUSANDS = re.compile(r"\d{1,3}(,\d{3})+(\.\d+)?")
_DOT_THOUSANDS = re.compile(r"\d{1,3}(\.\d{3})+(,\d+)?")
_PLAIN = re.compile(r"\d+(\.\d{1,2})?")
_COMMA_DECIMALS = re.compile(r"\d+,\d{1,2}")
_BLANKS = re.compile(r"\s+")


def parse_money(text: Any) -> Decimal | None:
    """An annotated amount as ``Decimal``, or None. The annotation mixes conventions: ``60.000`` and ``Rp 16.500`` (a dot between thousands), ``1,591,600`` and ``226,500.00`` (a comma
    between thousands), ``2.00`` (a plain decimal). A text with both separators is read by the last one: up to two digits after it are decimals."""
    if not isinstance(text, str):
        return None
    cleaned = text.replace("Rp", "").replace("RP", "").strip().replace(" ", "")
    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-")
    if _COMMA_THOUSANDS.fullmatch(cleaned):
        value = cleaned.replace(",", "")
    elif _DOT_THOUSANDS.fullmatch(cleaned):
        value = cleaned.replace(".", "").replace(",", ".")
    elif _PLAIN.fullmatch(cleaned):
        value = cleaned
    elif _COMMA_DECIMALS.fullmatch(cleaned):
        value = cleaned.replace(",", ".")
    else:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return -number if negative else number


def normalise(value: Any) -> str:
    """The normal form of a leaf: an amount to cents, any other text in lower case with its whitespace collapsed; nothing is the empty text."""
    if value is None:
        return ""
    text = str(value).strip()
    amount = parse_money(text)
    if amount is not None:
        return str(amount.quantize(CENT, rounding=ROUND_HALF_UP))
    return _BLANKS.sub(" ", text).lower()


def _tree(value: Any, label: str = "root") -> Node:
    node = Node(label)
    if isinstance(value, dict):
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):      # the keys of an object have no order; the items of a list do
            node.addkid(_tree(item, str(key)))
    elif isinstance(value, list):
        for item in value:
            node.addkid(_tree(item, "item"))
    else:
        node.addkid(Node(normalise(value)))
    return node


def ted_accuracy(prediction: Any, truth: Any) -> float:
    """``1 - TED(prediction, truth) / TED(nothing, truth)``, never below 0; 1 when the truth is empty (there is nothing to lose)."""
    target = _tree(truth)
    from_nothing = simple_distance(Node("root"), target)
    if from_nothing == 0:
        return 1.0
    return max(0.0, 1.0 - simple_distance(_tree(prediction), target) / from_nothing)


def leaves(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """``(path, normal form)`` of every scalar leaf, in reading order; the items of a list share the path ``name[]``."""
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            out += leaves(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for item in value:
            out += leaves(item, f"{prefix}[]")
    else:
        out.append((prefix, normalise(value)))
    return out


def leaf_match(prediction: Any, truth: Any) -> tuple[int, int, dict[str, tuple[int, int]]]:
    """``(matched, total, {path: (total, matched)})`` over the leaves of `truth`: a leaf of the prediction with the same path and the same normal form matches one leaf of the truth, once."""
    pool = Counter(leaves(prediction))
    matched = 0
    per_path: dict[str, tuple[int, int]] = {}
    truth_leaves = leaves(truth)
    for path, normal in truth_leaves:
        hit = pool[(path, normal)] > 0
        if hit:
            pool[(path, normal)] -= 1
            matched += 1
        total, ok = per_path.get(path, (0, 0))
        per_path[path] = (total + 1, ok + int(hit))
    return matched, len(truth_leaves), per_path


def exact_lines(fields: InvoiceFields, lines: Sequence[Any]) -> tuple[int, int]:
    """``(exact, lines of the truth)``: how many lines of the truth `lines` (code, quantity, unit price and amount) appear among the lines read, each read line used once."""
    pool = Counter((line.codigo, line.cantidad, line.precio_unitario, line.importe) for line in fields.lines)
    exact = 0
    for line in lines:
        key = (line.code, line.qty, line.unit_price, line.amount)
        if pool[key] > 0:
            pool[key] -= 1
            exact += 1
    return exact, len(lines)


# --- the intervals of the closing evaluation (D20) --------------------------------------------------------------------------------------


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """The Wilson score interval of a proportion (95 % for the default `z`), within 0 and 1; ``(None, None)`` when there is nothing to count. It behaves near 0 and near 1, where the normal
    interval does not: 0 bad posts in 300 have an upper limit of 1.26 %."""
    if n == 0:
        return None, None
    if not 0 <= successes <= n:
        raise ValueError(f"wilson: {successes} aciertos de {n} no es una proporción")
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denominator
    return (0.0 if successes == 0 else max(0.0, centre - half)), (1.0 if successes == n else min(1.0, centre + half))


def paired_difference(a: Sequence[float], b: Sequence[float], *, resamples: int, seed: int) -> tuple[float | None, float | None, float | None]:
    """``(mean(a - b), low, high)``: the mean difference over the pairs and the 95 % interval of resampling the pairs (the same unit for `a` and `b`) `resamples` times with the seed `seed`.
    ``(None, None, None)`` with no pairs; with no difference at all the interval is that point."""
    import numpy as np

    if len(a) != len(b):
        raise ValueError(f"paired_difference: hay {len(a)} valores de un lado y {len(b)} del otro; se esperaban pares")
    if not a:
        return None, None, None
    differences = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    value = float(differences.mean())
    if not differences.any():
        return value, value, value
    draws = np.random.default_rng(seed).integers(0, len(differences), size=(resamples, len(differences)))
    means = differences[draws].mean(axis=1)
    return value, float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
