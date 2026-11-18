"""Hand-made figures of the closing evaluation, in the shape ``evaluation.criteria_of`` reads (U16)."""

from __future__ import annotations

LIMITS = {"c01_min_ted_diff": -2.0, "c02_min_line_exact": 0.90, "c03_min_line_exact": 0.80, "c04_min_qr_read": 0.97, "c05_max_bad_post_upper": 0.02, "c06_min_straight_through": 0.85,
          "c07_min_review_recall": 0.99}


def figures(**changes):
    base = {
        "cord": {"receipts": 100, "ted_chosen": 91.2, "ted_checkpoint": 93.0, "diff": {"value": -1.8, "ci_low": -1.9, "ci_high": -1.0}},
        "lines": {"known": {"exact": 279, "lines": 300, "rate": 0.931, "ci_low": 0.9, "ci_high": 0.95, "invoices": 300}, "new": {"exact": 205, "lines": 300, "rate": 0.6833, "ci_low": 0.63, "ci_high": 0.73, "invoices": 300}},
        "qr": {"printed": 540, "intact": 540, "read": 535, "rate": 0.9907},
        "bad_post": {"posts": 400, "bad": 0, "rate": 0.0, "ci_low": 0.0, "ci_high": 0.0095, "invoices": 600},
        "straight_through": {"known": {"posted": 258, "clean": 280, "rate": 0.9214}, "new": {"posted": 100, "clean": 280, "rate": 0.3571}},
        "review_recall": {"planted": 80, "review": 80, "rate": 1.0},
        "b1_minus_flow": {"value": 0.05, "ci_low": 0.03, "ci_high": 0.07, "b1_rate": 0.06, "flow_rate": 0.01},
        "erp": {"posts": 400, "readback_equal": 400, "repeat_new": 0},
        "variants": 0, "days": 20,
    }
    return {**base, **changes}
