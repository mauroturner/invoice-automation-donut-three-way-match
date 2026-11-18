"""The tiny model of the tests and what it is trained on (U10; plan sections 9.2 and 24, decision D36): a ``TinyKit`` holds a fresh tiny processor and model built from the
vocabulary of the invoice targets of ``validation_known`` of the quick configuration (8 invoices) and the training items made from them. Building one costs about a tenth of a second;
the invoices are made once per session (``tiny_truths`` in ``conftest.py``)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from PIL import Image

from invoiceflow.config import SealedConfig
from invoiceflow.model.serialize import invoice_target, json2token
from invoiceflow.model.tiny import tiny_model, vocab_of
from invoiceflow.model.train import TrainItem, prepare_tokens
from invoiceflow.synth.render import draw_page


@dataclass
class TinyKit:
    processor: Any
    model: Any
    targets: list[dict]
    items: list[TrainItem]
    view: SealedConfig


_PAGES: dict[tuple[str, int], Image.Image] = {}


def page_of(truth, view: SealedConfig) -> Image.Image:
    """The clean page of an invoice as an RGB image (no scan effect, no file): enough for a tiny model to tell eight invoices apart. Drawn once per session and kept (the tests
    only read it)."""
    key = (truth.invoice_key, view.synth.dpi)
    if key not in _PAGES:
        _PAGES[key] = draw_page(truth, view).image.convert("RGB")
    return _PAGES[key]


def with_model(view: SealedConfig, **changes) -> SealedConfig:
    return dataclasses.replace(view, model=dataclasses.replace(view.model, **changes))


def make_kit(view: SealedConfig, truths, *, extra_cord: int = 0) -> TinyKit:
    targets = [invoice_target(t) for t in truths]
    sequences = [view.model.task_invoice + json2token(t) for t in targets]
    cord_targets = [{"menu": [{"nm": f"item{i}", "cnt": "1", "price": "1000"}], "total": {"total_price": f"{i}000"}} for i in range(extra_cord)]
    sequences += [view.model.task_cord + json2token(t) for t in cord_targets]
    processor, model = tiny_model(vocab_of(sequences), view)
    prepare_tokens(processor, model, targets + cord_targets, view)
    white = Image.new("RGB", (96, 128), "white")
    items = [TrainItem((lambda truth=truth: page_of(truth, view)), view.model.task_invoice + json2token(target), view.model.task_invoice, "synth")
             for truth, target in zip(truths, targets, strict=True)]
    items += [TrainItem((lambda: white), view.model.task_cord + json2token(target), view.model.task_cord, "cord") for target in cord_targets]
    return TinyKit(processor, model, targets + cord_targets, items, view)

