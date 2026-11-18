"""The synthetic truth: a file with one JSON line per invoice, and the one reader (RF05, RF09; AC10; decision D23; plan section 10).

The generator knows everything about an invoice (its fields, its class, the decision it expects); a flow that read that file, even by accident, would give perfect and
false results. ``read_truth`` therefore looks at who calls it and refuses (``LeakageError``, M01) anyone but the evaluation, the baselines and the tests: a tripwire
against a mistake, not a defence against code that forges a module name; the static test that no module of the flow imports the reader is the other half.
``write_truth`` guards the CUIT it writes (M07), writes atomically, and returns the SHA-256 of the file, which a delivery's manifest records.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from invoiceflow.files import write_atomic
from invoiceflow.leakage import LeakageError, caller
from invoiceflow.synth.cuit import require_synthetic_cuit
from invoiceflow.synth.invoices import InvoiceHeader, InvoiceLine, InvoiceTotals, InvoiceTruth
from invoiceflow.synth.master import to_jsonable

READERS = ("invoiceflow.evaluation", "invoiceflow.baselines")
TEST_PACKAGES = ("tests", "test_", "conftest")

__all__ = ["LeakageError", "READERS", "read_truth", "write_truth"]


def _allowed(module: str) -> bool:
    return module in READERS or module.split(".")[0] in TEST_PACKAGES or module.split(".")[-1].startswith("test_")


def _decimal(value: Any) -> Decimal:
    return Decimal(value)


def from_jsonable(data: dict[str, Any]) -> InvoiceTruth:
    """The inverse of ``to_jsonable`` for an invoice; a missing or extra field is a ``KeyError`` or ``TypeError`` that the reader turns into a line number."""
    h, t = data["header"], data["totals"]
    header = InvoiceHeader(h["tipo"], h["pto_vta"], h["nro"], date.fromisoformat(h["fecha"]), h["cuit_emisor"], h["razon_social"], h["cuit_receptor"], h["orden_compra"],
                           h["moneda"], _decimal(h["cotizacion"]), h["cae"], date.fromisoformat(h["cae_vto"]))
    lines = tuple(InvoiceLine(x["code"], x["description"], _decimal(x["qty"]), x["unit"], _decimal(x["unit_price"]), _decimal(x["amount"])) for x in data["lines"])
    totals = InvoiceTotals(_decimal(t["neto"]), _decimal(t["iva_rate"]), _decimal(t["iva"]), _decimal(t["total"]))
    scalars = {k: data[k] for k in ("invoice_key", "split", "cohort", "supplier_key", "layout", "font", "po_number", "qr_text", "qr_cm", "scan_profile", "file_format",
                                    "planted", "expected_decision")}
    extra = set(data) - set(scalars) - {"header", "lines", "totals", "received_on", "expected_reasons"}
    if extra:
        raise TypeError(f"campos que la verdad no tiene: {sorted(extra)}")
    return InvoiceTruth(header=header, lines=lines, totals=totals, received_on=date.fromisoformat(data["received_on"]), expected_reasons=tuple(data["expected_reasons"]),
                        **scalars)


def write_truth(path: Path, items: list[InvoiceTruth]) -> str:
    """Write `items` to `path`, one JSON line each (sorted keys, Decimal as text, dates as ISO text), atomically; every CUIT must be synthetic (M07). Returns the
    SHA-256 of the file. A refusal leaves the file as it was."""
    for item in items:
        require_synthetic_cuit(item.header.cuit_emisor, "truth.write_truth (emisor)")
        require_synthetic_cuit(item.header.cuit_receptor, "truth.write_truth (receptor)")
    text = "".join(json.dumps(to_jsonable(item), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n" for item in items)
    write_atomic(Path(path), text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_truth(path: Path) -> list[InvoiceTruth]:
    """The invoices of a truth file. Only the evaluation, the baselines and the tests may call it; anyone else gets a ``LeakageError`` before the file is opened."""
    module, function = caller({__name__})
    if not _allowed(module):
        raise LeakageError(f"{module or '?'}.{function or '?'} abrió la verdad sintética: solo la evaluación y las líneas base la leen (D23, M01)")
    items = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        try:
            items.append(from_jsonable(json.loads(line)))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"la verdad {Path(path).name} no tiene el formato esperado en la línea {number}") from error
    return items
