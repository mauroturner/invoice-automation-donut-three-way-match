"""The validation of a delivery (RF07, RF08; V10 to V16 and I01; plan section 6; decision D25).

Two rules are about the delivery and block the run: **V10** (it is the next delivery of the plan) and **V11** (the files of the inbox, their number, their sizes and their SHA-256
are the manifest's, and the day's truth file exists with the manifest's size and hash: it is measured and hashed, never read). A blocking failure raises ``ValidationError``
(exit code 3) before any file is inspected or moved, so the run writes nothing. The others are about each file and never block: **V12** to **V15** are ``intake.inspect_file``'s, and a
file that fails goes to ``quarantine/`` next to the inbox with its rule in the report (its invoice goes to review with ``file_rejected``); **V16** flags a hash that repeats
inside the delivery or is already in the warehouse, and the file is processed all the same (the fiscal duplicate is the match's rule, ``duplicate``). **I01** counts the files by format.
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from invoiceflow.config import SealedConfig
from invoiceflow.deliveries import DeliverySpec, delivery_plan, next_pending
from invoiceflow.files import sha256_file
from invoiceflow.intake import FileCheck, inspect_file

BLOCKING, WARNING, INFORMATIVE = "bloqueante", "advertencia", "informativa"
# rule: (name, severity, scope, aggregation), as in the plan's table
RULES: dict[str, tuple[str, str, str, str]] = {
    "V10": ("La entrega es la siguiente del plan", BLOCKING, "entrega", "per_run"),
    "V11": ("Archivos, cantidad y SHA-256 son los del manifiesto de la entrega; la verdad del día existe", BLOCKING, "entrega", "per_run"),
    "V12": ("El tipo por los primeros bytes es uno de los formatos y coincide con la extensión", WARNING, "archivo", "per_run"),
    "V13": ("Archivo de 1 byte al máximo permitido", WARNING, "archivo", "per_run"),
    "V14": ("Píxeles dentro del máximo, leídos de la cabecera antes de decodificar", WARNING, "archivo", "per_run"),
    "V15": ("PDF de una página, con una sola imagen y sin texto, programas ni adjuntos", WARNING, "archivo", "per_run"),
    "V16": ("Hash repetido dentro de la entrega o ya cargado", WARNING, "archivo", "per_run"),
    "I01": ("Archivos por formato", INFORMATIVE, "entrega", "per_run"),
}


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    name_es: str
    severity: str
    scope: str
    aggregation: str
    passed: bool
    files_affected: int = 0
    examples: tuple[str, ...] = ()          # file names, sorted, never contents
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationReport:
    delivery_id: str
    run_key: str
    results: tuple[RuleResult, ...]
    files: tuple[FileCheck, ...]

    @property
    def blocking_failed(self) -> tuple[str, ...]:
        return tuple(r.rule_id for r in self.results if r.severity == BLOCKING and not r.passed)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id, "run_key": self.run_key,
            "results": [{"rule_id": r.rule_id, "name_es": r.name_es, "severity": r.severity, "scope": r.scope, "aggregation": r.aggregation, "passed": r.passed,
                         "files_affected": r.files_affected, "examples": list(r.examples), "detail": r.detail} for r in self.results],
            "files": [{"name": f.name, "status": f.status, "rule_id": f.rule_id, "reason": f.reason, "format": f.file_format, "bytes": f.bytes} for f in self.files],
        }

    def to_markdown(self) -> str:
        def cell(text: object) -> str:
            return str(text).replace("|", "\\|")

        lines = [f"# Validación de la entrega {self.delivery_id}", "", f"Corrida: {cell(self.run_key)}", "",
                 "| Regla | Nombre | Severidad | Alcance | Resultado | Archivos |", "| --- | --- | --- | --- | --- | ---: |"]
        for r in self.results:
            lines.append(f"| {r.rule_id} | {cell(r.name_es)} | {r.severity} | {r.scope} | {'pasa' if r.passed else 'falla'} | {r.files_affected} |")
        lines += ["", "## Archivos", "", "| Archivo | Estado | Regla | Motivo |", "| --- | --- | --- | --- |"]
        for f in self.files:
            lines.append(f"| {cell(f.name)} | {'aceptado' if f.status == 'accepted' else 'cuarentena'} | {f.rule_id or ''} | {f.reason or ''} |")
        return "\n".join(lines) + "\n"


class ValidationError(RuntimeError):
    """A blocking rule of the delivery failed (V10, V11); the command line exits with 3. ``report`` says which."""

    EXIT_CODE = 3

    def __init__(self, report: ValidationReport) -> None:
        super().__init__(f"{report.delivery_id}: falla la regla bloqueante {', '.join(report.blocking_failed)}")
        self.report = report


def _result(rule_id: str, passed: bool, names: list[str] | None = None, **detail: Any) -> RuleResult:
    name, severity, scope, aggregation = RULES[rule_id]
    examples = tuple(sorted(names or []))
    return RuleResult(rule_id, name, severity, scope, aggregation, passed, len(examples), examples, detail)


def _v11(inbox: Path, entry: dict[str, Any] | None, truth_path: Path | None) -> tuple[bool, list[str]]:
    """Whether the inbox and the truth file are what the manifest says, and the names that differ. Sizes are compared before any hash, and a file of another size is not hashed."""
    if entry is None:
        return False, ["(manifiesto)"]
    wanted = entry.get("files", {})
    present = {p.name: p for p in inbox.iterdir()} if inbox.is_dir() else {}
    wrong = sorted(set(present) ^ set(wanted))
    for name in sorted(set(present) & set(wanted)):
        path = present[name]
        if not path.is_file() or path.stat().st_size != wanted[name].get("bytes") or sha256_file(path) != wanted[name].get("sha256"):
            wrong.append(name)
    truth = entry.get("truth")
    if truth is not None and (truth_path is None or not Path(truth_path).is_file() or Path(truth_path).stat().st_size != truth.get("bytes")
                              or sha256_file(Path(truth_path)) != truth.get("sha256")):
        wrong.append("(verdad)")
    return not wrong, wrong


def _loaded_hashes(conn: sqlite3.Connection | None) -> set[str]:
    if conn is None:
        return set()
    try:
        return {row[0] for row in conn.execute("SELECT file_sha256 FROM invoices WHERE file_sha256 IS NOT NULL")}
    except sqlite3.OperationalError:
        return set()


def validate_delivery(spec: DeliverySpec, inbox: Path, conn: sqlite3.Connection | None, view: SealedConfig, *, run_key: str,
                      manifest: dict[str, Any] | None = None, truth_path: Path | None = None) -> ValidationReport:
    """V10 to V16 and I01 for the delivery `spec` whose files are in `inbox`. `manifest` is the deliveries manifest (None: no delivery was published), `truth_path` the day's
    truth file (measured and hashed, never read) and `conn` the warehouse (for V16; None or without an ``invoices`` table loads nothing).

    A blocking failure raises ``ValidationError`` before any file is inspected or moved. Otherwise the files that fail V12 to V15 are moved to ``quarantine/`` next to the inbox, and
    the report says each file's status."""
    inbox = Path(inbox)
    manifest = manifest if manifest is not None else {"version": 1, "deliveries": {}}
    pending = next_pending(manifest, delivery_plan(view))
    first = _result("V10", pending == spec, [spec.delivery_id] if pending != spec else [], expected=pending.delivery_id if pending else None)
    entry = manifest["deliveries"].get(spec.delivery_id)
    ok, wrong = _v11(inbox, entry, truth_path)
    second = _result("V11", ok, wrong)
    if not (first.passed and second.passed):
        raise ValidationError(ValidationReport(spec.delivery_id, run_key, (first, second), ()))
    checks = tuple(inspect_file(path, view) for path in sorted(inbox.iterdir(), key=lambda p: p.name))
    results = [first, second]
    for rule_id in ("V12", "V13", "V14", "V15"):
        results.append(_result(rule_id, not any(c.rule_id == rule_id for c in checks), [c.name for c in checks if c.rule_id == rule_id]))
    accepted = [c for c in checks if c.status == "accepted"]
    counts = Counter(c.sha256 for c in accepted)
    loaded = _loaded_hashes(conn)
    repeated = [c.name for c in accepted if counts[c.sha256] > 1 or c.sha256 in loaded]
    results.append(_result("V16", not repeated, repeated))
    by_format = Counter(c.file_format or "unknown" for c in checks)
    results.append(RuleResult("I01", *RULES["I01"], True, 0, (), dict(sorted(by_format.items()))))
    quarantined = [c for c in checks if c.status == "quarantined"]
    if quarantined:
        folder = inbox.parent / "quarantine"
        folder.mkdir(parents=True, exist_ok=True)
        for check in quarantined:
            os.replace(check.path, folder / check.name)
    return ValidationReport(spec.delivery_id, run_key, tuple(results), checks)
