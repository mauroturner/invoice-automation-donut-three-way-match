"""A delivery and the report of its validation, made by hand, to load into a warehouse (U12): two accepted files and one quarantined, every blocking rule passed."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

from invoiceflow.deliveries import DeliverySpec
from invoiceflow.intake import FileCheck
from invoiceflow.validate import RULES, RuleResult, ValidationReport

SHA = "a" * 64
CONFIG_SHA = "b" * 64
STARTED = datetime(2026, 10, 2, 8, 30, 15)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def spec_of(delivery_id: str = "D2026-10-01", kind: str = "daily") -> DeliverySpec:
    day = date.fromisoformat(delivery_id[1:])
    return DeliverySpec(delivery_id, kind, day, datetime(day.year, day.month, day.day) + timedelta(days=1))


def accepted(name: str, content: str | None = None, *, fmt: str = "png") -> FileCheck:
    return FileCheck(name, Path(name), "accepted", None, None, fmt, 1000 + len(name), 4000, content if content and len(content) == 64 else sha(content or name))


def quarantined(name: str, rule: str = "V14") -> FileCheck:
    return FileCheck(name, Path(name), "quarantined", rule, "too_many_pixels", "png", 123, None, None)


def report_of(spec: DeliverySpec, run_key: str, files: tuple[FileCheck, ...] | None = None, *, failed: tuple[str, ...] = (), drop: tuple[str, ...] = ()) -> ValidationReport:
    files = (accepted("D-00001.png"), accepted("D-00002.jpg", fmt="jpeg"), quarantined("D-00003.png")) if files is None else files
    results = []
    for rule_id in ("V10", "V11", "V12", "V13", "V14", "V15", "V16", "I01"):
        if rule_id in drop:
            continue
        name, severity, scope, aggregation = RULES[rule_id]
        results.append(RuleResult(rule_id, name, severity, scope, aggregation, rule_id not in failed, 1 if rule_id == "V14" else 0, ("D-00003.png",) if rule_id == "V14" else (), {}))
    return ValidationReport(spec.delivery_id, run_key, tuple(results), tuple(files))
