"""Cases worked out by hand (U03, plan section 24): the CUIT and its check digit, the QR of ARCA's specification and its malformed variants, the match's
boundaries, the normalization, the choice among three candidates, Wilson's interval, an exact line and a bad post, and a tree edit distance.

Every expected value is written here as a literal, from the source it names or from arithmetic anyone can redo; ``tests/unit/test_hand_cases.py`` checks
each one against a second, independent computation, so a typo in a case is found before a unit trusts it. The units that write the code use these as
the right answers and never compute them with the code under test. Amounts are texts: the code reads them as ``Decimal``.
"""

from __future__ import annotations

import base64
import json
from datetime import date

# --- CUIT (D05) ---------------------------------------------------------------------------------------------------------
# The module-11 rule of AFIP: weights 5 4 3 2 7 6 5 4 3 2 over the first ten digits; the digit is 11 - (sum mod 11); 11 gives 0; 10 means that no CUIT
# exists with those ten digits. For 3000000000 + n the sum is 15 + 2n, which gives these ten, worked out by hand:
CHECK_DIGIT_CASES = (
    ("3000000000", 7), ("3000000001", 5), ("3000000002", 3), ("3000000003", 1), ("3000000004", None),   # 15 + 8 = 23, 23 mod 11 = 1, 11 - 1 = 10: none
    ("3000000005", 8), ("3000000006", 6), ("3000000007", 4), ("3000000008", 2), ("3000000009", 0),      # 15 + 18 = 33, 33 mod 11 = 0, 11 - 0 = 11: zero
)
SYNTHETIC_RANGE = "30-0000xxxx-d"   # 300000 + a serial of four digits + the check digit
# Serial -> the synthetic CUIT; serial 4 has none, and serial 0 is the specification's own example (not drawn by the generator: the range starts at 1)
SYNTHETIC_CUITS = {0: "30000000007", 1: "30000000015", 2: "30000000023", 3: "30000000031", 4: None, 5: "30000000058"}
# Valid or not as a CUIT, in the synthetic range or not:
OUTSIDE_THE_RANGE = (
    ("30000100001", True),    # prefix 300001: the digit is right (3000010000 -> 21 mod 11 = 10 -> 1) but it is not 300000...
    ("30000000008", False),   # in the range with a wrong digit (the right one is 7)
    ("3000000000", False),    # ten digits
    ("300000000070", False),  # twelve digits
    ("30-00000000-7", False),  # the specification's own CUIT written with hyphens: the guard takes the eleven digits only
    ("", False),
)

# --- the QR of ARCA's specification (RF10, AC07) ------------------------------------------------------------------------------
SPEC_EXAMPLE_JSON = ('{"ver":1,"fecha":"2020-10-13","cuit":30000000007,"ptoVta":10,"tipoCmp":1,"nroCmp":94,'
                     '"importe":12100,"moneda":"DOL","ctz":65,"tipoDocRec":80,"nroDocRec":20000000001,'
                     '"tipoCodAut":"E","codAut":70417054367476}')
SPEC_EXAMPLE_B64 = ("eyJ2ZXIiOjEsImZlY2hhIjoiMjAyMC0xMC0xMyIsImN1aXQiOjMwMDAwMDAwMDA3LCJwdG9WdGEiOjEwLCJ0aXBvQ21wIjox"
                    "LCJucm9DbXAiOjk0LCJpbXBvcnRlIjoxMjEwMCwibW9uZWRhIjoiRE9MIiwiY3R6Ijo2NSwidGlwb0RvY1JlYyI6ODAsIm5yb0Rv"
                    "Y1JlYyI6MjAwMDAwMDAwMDEsInRpcG9Db2RBdXQiOiJFIiwiY29kQXV0Ijo3MDQxNzA1NDM2NzQ3Nn0=")
SPEC_EXAMPLE_URL = "https://www.afip.gob.ar/fe/qr/?p=" + SPEC_EXAMPLE_B64      # the host as the PDF prints it in its example
SPEC_EXAMPLE_URL_ARCA = "https://www.arca.gob.ar/fe/qr/?p=" + SPEC_EXAMPLE_B64  # the host of the PDF's definition
QR_FIELDS = ("ver", "fecha", "cuit", "ptoVta", "tipoCmp", "nroCmp", "importe", "moneda", "ctz", "tipoDocRec", "nroDocRec", "tipoCodAut", "codAut")
QR_MAX_TEXT_BYTES = 1024


def _url(payload: str, host: str = "www.arca.gob.ar", path: str = "/fe/qr/", scheme: str = "https", query: str | None = None) -> str:
    return f"{scheme}://{host}{path}?{query if query is not None else 'p=' + payload}"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def malformed_qr_texts() -> dict[str, str]:
    """The variants of AC07, each broken in one way only (the others stay as the specification's example): name -> the text a QR would hold."""
    base = json.loads(SPEC_EXAMPLE_JSON)

    def changed(**edits) -> str:
        data = {**base, **edits}
        return _b64(json.dumps({key: value for key, value in data.items() if value is not None}, separators=(",", ":")))

    return {
        "foreign_host": _url(SPEC_EXAMPLE_B64, host="www.example.com"),
        "foreign_path": _url(SPEC_EXAMPLE_B64, path="/other/"),
        "not_https": _url(SPEC_EXAMPLE_B64, scheme="http"),
        "two_parameters": _url(SPEC_EXAMPLE_B64, query="p=" + SPEC_EXAMPLE_B64 + "&q=1"),
        "no_parameter": _url(SPEC_EXAMPLE_B64, query="q=" + SPEC_EXAMPLE_B64),
        "broken_base64": _url(SPEC_EXAMPLE_B64[:40] + "!!" + SPEC_EXAMPLE_B64[42:]),
        "json_is_a_list": _url(_b64("[1,2,3]")),
        "other_version": _url(changed(ver=2)),
        "extra_field": _url(changed(extra=1)),
        "missing_field": _url(changed(codAut=None)),
        "wrong_type": _url(changed(ptoVta="10")),
        "bad_cuit_digit": _url(changed(cuit=30000000008)),
        "too_long": _url(_b64(SPEC_EXAMPLE_JSON + " " * 900)),
    }


# --- the match's boundaries (D14, AC13): "inside the tolerance" includes the border ------------------------------------------
# (the order's unit price, the invoiced unit price, price_tolerance_pct = 2.0, inside?): |invoiced - order| / order * 100 <= 2.0
PRICE_CASES = (
    ("100.00", "101.99", True), ("100.00", "102.00", True), ("100.00", "102.01", False),   # 1.99 %, 2.00 %, 2.01 %
    ("100.00", "98.00", True), ("100.00", "97.99", False),                                 # the same, below the order's price
    ("0.00", "0.00", True), ("0.00", "0.01", False),                                       # an order with price 0 is never divided by
)
# (received until the invoice's day, invoiced before, quantity of this line, the line is within what was received): before + quantity <= received
RECEIPT_QUANTITY_CASES = (
    ("10", "0", "10", True), ("10", "0", "11", False), ("10", "0", "9", True),             # the same, one more, one less than received
    ("10", "4", "6", True), ("10", "4", "7", False),                                       # what was invoiced before counts
    ("0", "0", "1", False),                                                                # nothing received
)
INVOICE_DAY = date(2026, 10, 5)
# (the receipt's date, does it count for an invoice of INVOICE_DAY): the receipt counts when its date is not after the day
RECEIPT_DAY_CASES = ((date(2026, 10, 5), True), (date(2026, 10, 4), True), (date(2026, 10, 6), False))
# (quantity, unit price, printed amount, rounding_per_line = 0.01, the line closes): |quantity x price - amount| <= 0.01
ROUNDING_CASES = (
    ("3", "10.00", "30.00", True), ("3", "10.00", "30.01", True), ("3", "10.00", "30.02", False),
    ("3", "10.00", "29.99", True), ("3", "10.00", "29.98", False),
    ("2.5", "4.00", "10.00", True), ("0", "4.00", "0.00", True),
)

# --- the normalization (AC12): the text as printed -> the value, or None when it cannot be read -----------------------------------
AMOUNT_CASES = (("1.234,56", "1234.56"), ("1234,56", "1234.56"), ("$ 1.234,56", "1234.56"), ("USD 12,00", "12.00"), ("12,00", "12.00"),
                ("", None), ("abc", None), ("$", None), ("1.234,56,78", None))
DATE_CASES = (("14/10/2026", "2026-10-14"), ("2026-10-14", "2026-10-14"), ("1/2/2026", "2026-02-01"), ("31/02/2026", None), ("", None), ("14 de octubre", None))
# The CUIT of the specification's own text: its check digit is wrong (3000001234 -> 45 mod 11 = 1 -> 10, so no CUIT has those ten digits). Normalizing a
# CUIT is only about its eleven digits; whether the digit is right is the QR's and the match's business (qr_invalid, supplier_mismatch).
CUIT_TEXT_CASES = (("30-00001234-5", "30000012345"), ("30000012345", "30000012345"), ("30 00001234 5", "30000012345"), ("30-0000", None), ("abc", None), ("", None))

# --- the choice (D12, AC18): three candidates; the checkpoint's TED on CORD's validation is 93.65 and cord_val_tolerance is 2.0 ----------------------
CHECKPOINT_TED = 93.65
CORD_VAL_TOLERANCE = 2.0
# epoch -> (TED on CORD's validation, exact-line rate on validation_known, exact-line rate on validation_new)
CANDIDATES = {2: (93.00, 0.90, 0.50), 4: (91.70, 0.95, 0.70), 6: (90.00, 0.99, 0.90)}
# Epoch 6 reads best but forgets CORD (90.00 < 91.65) and is dropped; of the others the mean of the two rates is 0.70 for epoch 2 and 0.825 for epoch 4
CHOSEN_EPOCH = 4
TIE_CANDIDATES = {2: (93.00, 0.90, 0.70), 4: (92.00, 0.80, 0.80), 6: (92.00, 0.70, 0.90)}   # all three average 0.80: the earliest wins
TIE_CHOSEN_EPOCH = 2
NONE_VALID_CANDIDATES = {2: (91.00, 0.90, 0.90), 4: (90.50, 0.95, 0.95)}                     # both lose more than two points: ModelChoiceError

# --- Wilson's interval (D20), z = 1.959963984540054: (successes, trials) -> (lower, upper) at 6 decimals --------------------------------
WILSON_Z = 1.959963984540054
WILSON_CASES = (((0, 300), (0.0, 0.012643)), ((150, 300), (0.443780, 0.556220)), ((300, 300), (0.987357, 1.0)), ((3, 100), (0.010255, 0.084519)))

# --- an exact line and a bad post (requirements.md 8.1) ---------------------------------------------------------------------------------------------
# (code, quantity, unit price, amount) of the truth and of what was read; the second line has the price read as 5.05 instead of 5.50
TRUTH_LINES = (("A1", "2", "10.00", "20.00"), ("B2", "1", "5.50", "5.50"), ("C3", "3", "1.00", "3.00"))
READ_LINES = (("A1", "2", "10.00", "20.00"), ("B2", "1", "5.05", "5.50"), ("C3", "3", "1.00", "3.00"))
EXACT_LINES = (2, 3)   # two of three lines are exact
# invoice -> (planted class or None, decision, the ERP's fields equal the truth's, the QR is intact)
BAD_POST_CASES = {
    "i1": (None, "auto_post", True, True),             # a good post
    "i2": ("price_off", "auto_post", True, True),      # (a) loaded although a discrepancy was planted
    "i3": (None, "auto_post", False, True),            # (b) loaded with a field different from the truth (the total)
    "i4": ("over_receipt", "review", True, True),
    "i5": (None, "review", True, True),
}
BAD_POSTS = ("i2", "i3")        # of the three auto_post invoices (i1, i2, i3): a rate of 2/3
STRAIGHT_THROUGH = ("i1",)      # of the unplanted invoices with an intact QR (i1, i3, i5): auto_post and loaded right -> 1/3

# --- a tree edit distance (research/s02's ordered tree: dict keys and list items are nodes, a scalar is a leaf; update cost 1 if the labels differ) --------
# (prediction, ground truth, TED, TED of the empty tree to the ground truth, accuracy = 1 - TED / TED of the empty tree)
TED_CASES = (
    ({"a": "x", "b": "y"}, {"a": "x", "b": "y"}, 0, 4, 1.0),
    ({}, {"a": "x", "b": "y"}, 4, 4, 0.0),
    ({"a": "x"}, {"a": "x", "b": "y"}, 2, 4, 0.5),
    ({"a": "x", "b": "z"}, {"a": "x", "b": "y"}, 1, 4, 0.75),
    ({"m": [{"n": "p"}]}, {"m": [{"n": "p"}, {"n": "q"}]}, 3, 7, 4 / 7),
    ({"m": [{"n": "p"}, {"n": "r"}]}, {"m": [{"n": "p"}, {"n": "q"}]}, 1, 7, 6 / 7),
)
