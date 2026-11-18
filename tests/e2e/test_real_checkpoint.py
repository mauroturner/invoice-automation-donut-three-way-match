"""The launch measures the unchanged checkpoint the way the planner did (U22; plan section 24 «el checkpoint en la validación de CORD (repite p01)»; decisions D12, D51; marks ``real_data``, ``model`` and ``gpu``).

The first real launch measured the checkpoint at 49.08 on CORD's 100 validation receipts, where the planner had 93.65 (p01), because the tree distance read the order of the keys of an object
and the canonical target has them sorted: a reading that was the truth word for word scored 0.45. This test is what was missing: it runs the launch's own measure (``launch.cord_ted`` with the
launch's own extractor of the checkpoint) on the real model and the real validation receipts, and asks for the planner's figure within a point. It needs the index of CORD that the task
``fetch_source`` writes (``python -m invoiceflow task fetch_source``) and reads no receipt of the test split.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from invoiceflow import cord
from invoiceflow.cord import read_split
from invoiceflow.launch import _checkpoint_extractor, cord_ted
from invoiceflow.model.train import load_base

pytestmark = [pytest.mark.real_data, pytest.mark.model, pytest.mark.gpu]

PLANNER_TED = 93.65        # research/plan p01: the unchanged checkpoint on CORD's validation split, in points
TOLERANCE = 1.0            # a reimplementation of the metric: the same figure within a point


def test_the_launch_measure_of_the_unchanged_checkpoint_on_cord_validation_is_the_planners(cfg, view):
    entries = cord.read_index(cfg).entries["validation"]
    assert len(entries) == 100
    images = {r.receipt_key: r.image_bytes for r in read_split(cfg, "validation")}

    def page(key: str) -> Image.Image:
        with Image.open(io.BytesIO(images[key])) as image:
            return image.convert("RGB")

    processor, model = load_base(view)
    height, width = view.model.input_size
    processor.image_processor.size = {"height": height, "width": width}
    measured = cord_ted(_checkpoint_extractor(view, processor, model), entries, page)
    assert abs(measured - PLANNER_TED) <= TOLERANCE, f"{measured:.2f} against the planner's {PLANNER_TED} (p01)"
