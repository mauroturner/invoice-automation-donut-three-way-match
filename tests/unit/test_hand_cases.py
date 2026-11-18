"""The cases worked out by hand (U03): each one is checked here against a second, independent computation (stdlib, ``zss``, closed forms), so a typo in
a case is found before a unit trusts it. The code under test is never used."""

from __future__ import annotations

import base64
import json
import re
from datetime import date
from decimal import Decimal
from math import sqrt
from urllib.parse import parse_qs, urlsplit

import pytest
from zss import Node, simple_distance

from tests.fixtures import hand_cases as h


def check_digit(first10: str) -> int | None:
    total = sum(int(digit) * weight for digit, weight in zip(first10, (5, 4, 3, 2, 7, 6, 5, 4, 3, 2), strict=True))
    digit = 11 - total % 11
    return None if digit == 10 else 0 if digit == 11 else digit


def decode(text: str) -> dict:
    return json.loads(base64.b64decode(parse_qs(urlsplit(text).query)["p"][0], validate=True))


# --- CUIT ---------------------------------------------------------------------------------------------------------------


def test_every_check_digit_case_is_the_module_11_rule_and_the_sum_is_15_plus_twice_the_last_digit():
    assert len(h.CHECK_DIGIT_CASES) == 10
    for first10, digit in h.CHECK_DIGIT_CASES:
        assert check_digit(first10) == digit, first10
        assert sum(int(d) * w for d, w in zip(first10, (5, 4, 3, 2, 7, 6, 5, 4, 3, 2), strict=True)) == 15 + 2 * int(first10[-1])
    assert [digit for _, digit in h.CHECK_DIGIT_CASES].count(None) == 1 and 0 in [digit for _, digit in h.CHECK_DIGIT_CASES]


def test_the_synthetic_cuits_are_in_the_range_and_carry_their_digit_and_the_specification_example_is_one():
    for serial, cuit in h.SYNTHETIC_CUITS.items():
        first10 = f"300000{serial:04d}"
        digit = check_digit(first10)
        assert cuit == (None if digit is None else f"{first10}{digit}"), serial
        if cuit is not None:
            assert re.fullmatch(r"300000\d{5}", cuit)
    assert h.SYNTHETIC_CUITS[0] == "30000000007" == str(json.loads(h.SPEC_EXAMPLE_JSON)["cuit"])


def test_the_cuits_outside_the_range_are_what_their_comments_say():
    cases = dict(h.OUTSIDE_THE_RANGE)
    assert check_digit("3000010000") == 1 and "30000100001" in cases and not re.fullmatch(r"300000\d{5}", "30000100001")
    assert check_digit("3000000000") == 7 and re.fullmatch(r"300000\d{5}", "30000000008") and "30000000008" in cases   # in the range, wrong digit
    for cuit, valid_digit in h.OUTSIDE_THE_RANGE:
        in_range = bool(re.fullmatch(r"300000\d{5}", cuit)) and check_digit(cuit[:10]) == int(cuit[10])
        assert not in_range, cuit
        assert valid_digit == (len(cuit) == 11 and cuit.isdigit() and check_digit(cuit[:10]) == int(cuit[10])), cuit


# --- the QR ---------------------------------------------------------------------------------------------------------------


def test_the_specification_example_reproduces_byte_for_byte_and_has_the_thirteen_fields():
    assert base64.b64encode(h.SPEC_EXAMPLE_JSON.encode("utf-8")).decode("ascii") == h.SPEC_EXAMPLE_B64
    parsed = json.loads(h.SPEC_EXAMPLE_JSON)
    assert tuple(parsed) == h.QR_FIELDS and len(parsed) == 13 and parsed["ver"] == 1
    assert h.SPEC_EXAMPLE_URL.startswith("https://www.afip.gob.ar/fe/qr/?p=") and h.SPEC_EXAMPLE_URL_ARCA.startswith("https://www.arca.gob.ar/fe/qr/?p=")
    assert decode(h.SPEC_EXAMPLE_URL) == decode(h.SPEC_EXAMPLE_URL_ARCA) == parsed
    assert check_digit(str(parsed["cuit"])[:10]) == int(str(parsed["cuit"])[10])


def test_each_malformed_qr_text_is_broken_in_exactly_the_way_its_name_says():
    texts = h.malformed_qr_texts()
    assert list(texts) == ["foreign_host", "foreign_path", "not_https", "two_parameters", "no_parameter", "broken_base64", "json_is_a_list",
                           "other_version", "extra_field", "missing_field", "wrong_type", "bad_cuit_digit", "too_long"]
    assert len(set(texts.values())) == 13 and h.SPEC_EXAMPLE_URL_ARCA not in texts.values()
    parts = {name: urlsplit(text) for name, text in texts.items()}
    assert parts["foreign_host"].hostname == "www.example.com" and decode(texts["foreign_host"]) == json.loads(h.SPEC_EXAMPLE_JSON)
    assert parts["foreign_path"].path == "/other/" and parts["foreign_path"].hostname == "www.arca.gob.ar"
    assert parts["not_https"].scheme == "http" and parts["not_https"].hostname == "www.arca.gob.ar"
    assert list(parse_qs(parts["two_parameters"].query)) == ["p", "q"] and list(parse_qs(parts["no_parameter"].query)) == ["q"]
    broken = parse_qs(parts["broken_base64"].query)["p"][0]
    with pytest.raises(ValueError):
        base64.b64decode(broken, validate=True)
    assert decode(texts["json_is_a_list"]) == [1, 2, 3]
    base = json.loads(h.SPEC_EXAMPLE_JSON)
    assert decode(texts["other_version"]) == {**base, "ver": 2}
    assert decode(texts["extra_field"]) == {**base, "extra": 1} and len(decode(texts["extra_field"])) == 14
    assert set(base) - set(decode(texts["missing_field"])) == {"codAut"}
    assert decode(texts["wrong_type"]) == {**base, "ptoVta": "10"}
    changed = decode(texts["bad_cuit_digit"])
    assert changed == {**base, "cuit": 30000000008} and check_digit("3000000000") != 8
    long = texts["too_long"]
    assert len(long.encode("utf-8")) > h.QR_MAX_TEXT_BYTES and decode(long) == base, "valid in every other way"
    for name in texts:
        if name != "too_long":
            assert len(texts[name].encode("utf-8")) <= h.QR_MAX_TEXT_BYTES, name


# --- the match --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("order", "invoiced", "inside"), h.PRICE_CASES)
def test_price_cases(order, invoiced, inside):
    order, invoiced, tolerance = Decimal(order), Decimal(invoiced), Decimal("2.0")
    if order == 0:
        assert (invoiced == 0) is inside
    else:
        assert (abs(invoiced - order) / order * 100 <= tolerance) is inside


def test_the_price_cases_cover_the_three_edges_on_both_sides_and_the_zero_order():
    assert {(case[0], case[1]) for case in h.PRICE_CASES} >= {("100.00", "101.99"), ("100.00", "102.00"), ("100.00", "102.01"), ("0.00", "0.01")}


@pytest.mark.parametrize(("received", "before", "quantity", "inside"), h.RECEIPT_QUANTITY_CASES)
def test_receipt_quantity_cases(received, before, quantity, inside):
    assert (Decimal(before) + Decimal(quantity) <= Decimal(received)) is inside


def test_receipt_day_cases():
    for receipt_day, counts in h.RECEIPT_DAY_CASES:
        assert (receipt_day <= h.INVOICE_DAY) is counts
    assert {day for day, _ in h.RECEIPT_DAY_CASES} >= {h.INVOICE_DAY, date(2026, 10, 6)}


@pytest.mark.parametrize(("quantity", "price", "amount", "closes"), h.ROUNDING_CASES)
def test_rounding_cases(quantity, price, amount, closes):
    assert (abs(Decimal(quantity) * Decimal(price) - Decimal(amount)) <= Decimal("0.01")) is closes


# --- the normalization --------------------------------------------------------------------------------------------------------


def test_the_normalization_cases_have_their_inverse_forms_and_the_unreadable_ones():
    for text, value in h.AMOUNT_CASES:
        if value is not None:
            digits = re.sub(r"[^0-9.,]", "", text)
            assert Decimal(digits.replace(".", "").replace(",", ".")) == Decimal(value), text
        else:
            assert not re.fullmatch(r"\D*\d{1,3}(\.\d{3})*(,\d{1,2})?\D*", text) or text.count(",") > 1, text
    for text, value in h.DATE_CASES:
        if value is not None:
            parts = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d)|(\d{1,2})/(\d{1,2})/(\d{4})", text)
            year, month, day = (parts.group(1), parts.group(2), parts.group(3)) if parts.group(1) else (parts.group(6), parts.group(5), parts.group(4))
            assert date(int(year), int(month), int(day)).isoformat() == value, text
    with pytest.raises(ValueError):
        date(2026, 2, 31)   # 31/02/2026 is not a date
    for text, value in h.CUIT_TEXT_CASES:
        digits = re.sub(r"\D", "", text)
        assert (digits if len(digits) == 11 and re.fullmatch(r"[\d\- ]+", text) else None) == value, text
    assert check_digit("3000001234") is None, "the example CUIT of the text cases has no valid check digit, as its comment says"


# --- the choice ---------------------------------------------------------------------------------------------------------------


def choose(candidates: dict[int, tuple[float, float, float]]) -> int | None:
    kept = {epoch: (known + new) / 2 for epoch, (ted, known, new) in candidates.items() if ted >= h.CHECKPOINT_TED - h.CORD_VAL_TOLERANCE}
    if not kept:
        return None
    best = max(kept.values())
    return min(epoch for epoch, mean in kept.items() if best - mean < 1e-6)


def test_the_choice_cases():
    assert choose(h.CANDIDATES) == h.CHOSEN_EPOCH == 4
    assert choose(h.TIE_CANDIDATES) == h.TIE_CHOSEN_EPOCH == 2
    assert choose(h.NONE_VALID_CANDIDATES) is None
    # the best reader loses CORD, so it is the case that shows why the rule exists
    assert max(h.CANDIDATES, key=lambda epoch: sum(h.CANDIDATES[epoch][1:])) == 6 and h.CANDIDATES[6][0] < h.CHECKPOINT_TED - h.CORD_VAL_TOLERANCE
    assert 91.65 - 1e-9 < h.CHECKPOINT_TED - h.CORD_VAL_TOLERANCE < 91.65 + 1e-9


# --- Wilson, the lines, the posts and the tree ---------------------------------------------------------------------------------


def wilson(successes: int, trials: int, z: float = h.WILSON_Z) -> tuple[float, float]:
    p, denominator = successes / trials, 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


@pytest.mark.parametrize(("counts", "interval"), h.WILSON_CASES)
def test_wilson_cases(counts, interval):
    lower, upper = wilson(*counts)
    assert lower == pytest.approx(interval[0], abs=5e-7) and upper == pytest.approx(interval[1], abs=5e-7)


def test_zero_bad_posts_in_300_have_an_upper_bound_of_1_26_percent_as_decision_d20_says():
    assert round(wilson(0, 300)[1] * 100, 2) == 1.26 and wilson(0, 300)[1] == pytest.approx(h.WILSON_Z**2 / (300 + h.WILSON_Z**2))


def test_the_exact_lines_the_bad_posts_and_the_straight_through_cases():
    exact = sum(truth == read for truth, read in zip(h.TRUTH_LINES, h.READ_LINES, strict=True))
    assert (exact, len(h.TRUTH_LINES)) == h.EXACT_LINES
    posted = [name for name, (_, decision, _, _) in h.BAD_POST_CASES.items() if decision == "auto_post"]
    bad = [name for name in posted if h.BAD_POST_CASES[name][0] is not None or not h.BAD_POST_CASES[name][2]]
    assert posted == ["i1", "i2", "i3"] and tuple(bad) == h.BAD_POSTS
    clean = [name for name, (planted, _, _, qr) in h.BAD_POST_CASES.items() if planted is None and qr]
    through = [name for name in clean if h.BAD_POST_CASES[name][1] == "auto_post" and h.BAD_POST_CASES[name][2]]
    assert clean == ["i1", "i3", "i5"] and tuple(through) == h.STRAIGHT_THROUGH
    assert (len(bad), len(posted)) == (2, 3) and (len(through), len(clean)) == (1, 3)


def tree(value, label="root") -> Node:
    node = Node(label)
    if isinstance(value, dict):
        for key, child in value.items():
            node.addkid(tree(child, str(key)))
    elif isinstance(value, list):
        for child in value:
            node.addkid(tree(child, "item"))
    else:
        node.addkid(Node(str(value)))
    return node


@pytest.mark.parametrize(("prediction", "truth", "distance", "empty", "accuracy"), h.TED_CASES)
def test_ted_cases(prediction, truth, distance, empty, accuracy):
    assert simple_distance(tree(prediction), tree(truth)) == distance
    assert simple_distance(Node("root"), tree(truth)) == empty
    assert 1 - distance / empty == pytest.approx(accuracy)
