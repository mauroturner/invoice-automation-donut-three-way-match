"""The second half of V06 (U06; plan section 6, decision D02): besides the size and the hash of ARCA's PDF, the specification's example must come out of
``synth.qrpayload`` byte for byte, or the sources are refused with the runbook R1 and nothing is written. The first half is ``test_cord.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from invoiceflow.sources import SourceError, check_sources, fetch_source
from invoiceflow.synth import qrpayload
from tests.fixtures import hand_cases as h
from tests.fixtures.projects import make_project

RAW_PDF = Path(__file__).resolve().parents[2] / "data" / "raw" / "arca" / "QRespecificaciones.pdf"


@pytest.mark.parametrize("name", ["compact_json", "qr_text"])
def test_a_writer_that_does_not_reproduce_the_example_is_v06_and_names_the_runbook_and_writes_nothing(tmp_path, monkeypatch, name):
    project = make_project(tmp_path)
    fetch_source(project.cfg)
    before = project.snapshot()
    real = getattr(qrpayload, name)
    monkeypatch.setattr(qrpayload, name, lambda *args, **kwargs: real(*args, **kwargs).replace("1", "2", 1))
    for call in (check_sources, fetch_source):
        with pytest.raises(SourceError, match="V06") as error:
            call(project.cfg)
        assert "runbook R1" in str(error.value) and "ejemplo" in str(error.value) and error.value.problems[0]["rule_id"] == "V06"
    assert project.snapshot() == before, "a rejected source writes nothing"


def test_with_the_example_reproducing_the_sources_are_accepted_as_before(tmp_path):
    project = make_project(tmp_path)
    hashes = check_sources(project.cfg)
    assert set(hashes) >= {"arca_spec"} and len(hashes) == 4


@pytest.mark.real_data
def test_the_example_of_the_real_pdf_is_the_one_the_writer_reproduces():
    """The real ``QRespecificaciones.pdf`` (pinned by hash) carries the example's Base64 in its text: the constants of the writer are the PDF's."""
    from pypdf import PdfReader

    text = "".join(page.extract_text() for page in PdfReader(RAW_PDF).pages)
    squeezed = "".join(text.split())
    assert h.SPEC_EXAMPLE_B64 in squeezed and qrpayload.SPEC_EXAMPLE_BASE64 == h.SPEC_EXAMPLE_B64
    assert "www.afip.gob.ar/fe/qr/?p=" in squeezed
