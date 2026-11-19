"""The extractor of the tests (U09; plan section 9.5, decision D23): it reads the synthetic truth, by the page it is shown, with read errors seeded where a test says. It lives
here and never in ``src/``: the real configuration refuses it, and only a test may register it (``extractors.register``).

``StubExtractor.from_truths`` writes the page of each truth into a folder, loads it the way the flow does (``intake.load_page``) and recognises it afterwards by the hash of its
pixels, so what it is shown is the file and not the truth object. The seeded errors are the four the match has to catch: a quantity one more, a price a tenth higher, a total one
peso higher and a field read as nothing.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image

from invoiceflow.config import SealedConfig
from invoiceflow.intake import load_page
from invoiceflow.model.serialize import invoice_target
from invoiceflow.normalize import parse_amount, parse_quantity
from invoiceflow.synth import printed
from invoiceflow.synth.render import render

ERROR_KINDS = ("quantity", "price", "total", "empty_field")
ORDER_MISREAD = "order"            # the order number read as one no order has: what B1 has to take from the model, not from the truth (D48, D60)
EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "pdf": ".pdf"}


def page_key(image: Image.Image) -> str:
    return hashlib.sha256(image.convert("RGB").tobytes() + str(image.size).encode()).hexdigest()


def seed_error(tree: dict[str, Any], kind: str) -> dict[str, Any]:
    """A copy of the model's tree with one read error."""
    tree = copy.deepcopy(tree)
    line, totals = tree["lines"][0], tree["totals"]
    if kind == "quantity":
        line["cantidad"] = printed.quantity((parse_quantity(line["cantidad"]) or Decimal(0)) + 1)
    elif kind == "price":
        line["precio_unitario"] = printed.amount((parse_amount(line["precio_unitario"]) or Decimal(0)) * Decimal("1.1"))
    elif kind == "total":
        totals["total"] = printed.amount((parse_amount(totals["total"]) or Decimal(0)) + 1)
    elif kind == "empty_field":
        line["importe"] = ""
    elif kind == ORDER_MISREAD:
        tree["header"]["orden_compra"] = "OC-0000-000000"
    else:
        raise ValueError(kind)
    return tree


class StubExtractor:
    model_version = "stub-1"

    def __init__(self, readings: Mapping[str, dict[str, Any]]) -> None:
        self.readings = dict(readings)

    @classmethod
    def from_truths(cls, truths, view: SealedConfig, folder: Path, *, errors: Mapping[str, str] | None = None) -> StubExtractor:
        """Write each truth's page into `folder` (as ``<invoice_key>.<ext>``) and learn what to answer when it is shown."""
        folder.mkdir(parents=True, exist_ok=True)
        readings = {}
        for truth in truths:
            path = folder / f"{truth.invoice_key}{EXTENSIONS[truth.file_format]}"
            path.write_bytes(render(truth, view))
            tree = invoice_target(truth)
            kind = (errors or {}).get(truth.invoice_key)
            readings[page_key(load_page(path, view))] = seed_error(tree, kind) if kind else tree
        return cls(readings)

    def extract(self, image: Image.Image) -> dict[str, Any]:
        return copy.deepcopy(self.readings.get(page_key(image), {}))
