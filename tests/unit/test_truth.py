"""The synthetic truth and who may read it (U06; AC10, D23, M01; plan section 10): a file with one JSON line per invoice, written atomically and only with synthetic
CUIT, read back exactly, and readable only by the evaluation, the baselines and the tests; no module of the flow imports the reader."""

from __future__ import annotations

import ast
import hashlib
import json
import types
from pathlib import Path

import pytest

from invoiceflow import truth as truth_module
from invoiceflow.leakage import LeakageError
from invoiceflow.synth.cuit import SyntheticCuitError
from invoiceflow.synth.invoices import invoices
from invoiceflow.truth import read_truth, write_truth

SRC = Path(__file__).resolve().parents[2] / "src" / "invoiceflow"
# The modules of the flow that must not even import the truth (AC10): the reading, the QR, the match, the decision, the ERP and the runs
FLOW_MODULES = ("extract.py", "qr.py", "match.py", "decide.py", "daily.py", "launch.py", "api.py", "pipeline.py", "intake.py", "normalize.py", "validate.py")
FLOW_PACKAGES = ("erp", "model")
MAY_READ = {"evaluation.py", "baselines.py", "truth.py"}


@pytest.fixture(scope="module")
def sample(quick_view, quick_master, tmp_path_factory):
    return invoices(quick_view, quick_master, "train", None, seal_path=tmp_path_factory.mktemp("seal") / "none.json")


def forged(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    exec(source, module.__dict__)
    return module


def test_the_truth_is_written_one_json_line_per_invoice_and_read_back_exactly(sample, tmp_path):
    path = tmp_path / "truth" / "train.jsonl"
    digest = write_truth(path, sample)
    data = path.read_bytes()
    assert digest == hashlib.sha256(data).hexdigest() and data.endswith(b"\n")
    lines = data.decode("utf-8").splitlines()
    assert len(lines) == len(sample)
    first = json.loads(lines[0])
    assert first["invoice_key"] == sample[0].invoice_key and first["header"]["fecha"] == sample[0].header.fecha.isoformat()
    assert isinstance(first["lines"][0]["unit_price"], str), "a Decimal is written as its text, never as a float"
    assert read_truth(path) == sample
    assert write_truth(path, sample) == digest, "the same truth writes the same bytes"
    assert [p.name for p in path.parent.iterdir()] == ["train.jsonl"], "no temporary file is left"


def test_a_truth_with_a_cuit_outside_the_range_is_refused_and_leaves_the_file_as_it_was(sample, tmp_path):
    path = tmp_path / "train.jsonl"
    write_truth(path, sample[:2])
    before = path.read_bytes()
    bad = [sample[0].__class__(**{**sample[0].__dict__, "header": sample[0].header.__class__(**{**sample[0].header.__dict__, "cuit_emisor": "20123456786"})})]
    with pytest.raises(SyntheticCuitError):
        write_truth(path, bad)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    bad_buyer = [sample[0].__class__(**{**sample[0].__dict__, "header": sample[0].header.__class__(**{**sample[0].header.__dict__, "cuit_receptor": "20123456786"})})]
    with pytest.raises(SyntheticCuitError):
        write_truth(path, bad_buyer)
    assert path.read_bytes() == before


def test_a_line_that_is_not_a_truth_is_refused_when_it_is_read(sample, tmp_path):
    path = tmp_path / "train.jsonl"
    write_truth(path, sample[:2])
    broken = path.read_text(encoding="utf-8").splitlines()
    path.write_text(broken[0] + "\n" + json.dumps({"invoice_key": "x"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="línea 2"):
        read_truth(path)


def test_the_evaluation_and_the_baselines_and_the_tests_may_read_it_and_nobody_else(sample, tmp_path):
    path = tmp_path / "train.jsonl"
    write_truth(path, sample)
    source = "def run(read, path):\n    return read(path)\n"
    for allowed in ("invoiceflow.evaluation", "invoiceflow.baselines"):
        assert forged(allowed, source).run(read_truth, path) == sample
    for refused in ("invoiceflow.extract", "invoiceflow.qr", "invoiceflow.match", "invoiceflow.decide", "invoiceflow.erp.client", "invoiceflow.daily",
                    "invoiceflow.launch", "invoiceflow.api", "invoiceflow.pipeline", "invoiceflow.deliveries", "invoiceflow.evaluation_extra", "other"):
        with pytest.raises(LeakageError, match="verdad"):
            forged(refused, source).run(read_truth, path)
    assert read_truth(path) == sample, "this test module is allowed: a test"


def test_a_wrapper_inside_the_flow_does_not_hide_the_caller(sample, tmp_path):
    path = tmp_path / "train.jsonl"
    write_truth(path, sample)
    flow = forged("invoiceflow.daily", "def run(read, path):\n    return read(path)\n")
    helper = forged("invoiceflow.warehouse", "def wrapped(task, read, path):\n    return task(read, path)\n")
    with pytest.raises(LeakageError, match="invoiceflow.daily"):
        helper.wrapped(flow.run, read_truth, path)


def test_a_refusal_comes_before_the_file_is_opened(sample, tmp_path, monkeypatch):
    opened = []
    real = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (opened.append(self.name), real(self, *a, **k))[1])
    with pytest.raises(LeakageError):
        forged("invoiceflow.extract", "def run(read, path):\n    return read(path)\n").run(read_truth, tmp_path / "never.jsonl")
    assert opened == []


def reads_the_truth(source: str) -> bool:
    """Whether a module's source imports the truth module or the reader: ``import invoiceflow.truth``, ``from invoiceflow import truth``,
    ``from invoiceflow.truth import ...`` (relative forms too) and any use of the name ``read_truth``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[:2] == ["invoiceflow", "truth"] for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in ("invoiceflow.truth", "truth") or (module == "invoiceflow" and any(a.name == "truth" for a in node.names)) or (node.level and module == "truth"):
                return True
            if node.level and not module and any(a.name == "truth" for a in node.names):
                return True
        if (isinstance(node, ast.Name) and node.id == "read_truth") or (isinstance(node, ast.Attribute) and node.attr == "read_truth"):
            return True
    return False


def uses_the_reader(source: str) -> bool:
    """Whether a module's source names ``read_truth`` anywhere (a call, an attribute, an import): writing the truth (``write_truth``) is not reading it."""
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Name) and node.id == "read_truth") or (isinstance(node, ast.Attribute) and node.attr == "read_truth"):
            return True
        if isinstance(node, ast.ImportFrom) and any(alias.name == "read_truth" for alias in node.names):
            return True
    return False


def test_writing_the_truth_is_not_reading_it():
    assert not uses_the_reader("from invoiceflow.truth import write_truth; write_truth(p, [])") and uses_the_reader("from invoiceflow.truth import read_truth")
    assert uses_the_reader("truth.read_truth(p)") and uses_the_reader("f = read_truth")


@pytest.mark.parametrize(("source", "expected"), [
    ("import invoiceflow.truth", True), ("import invoiceflow.truth as t", True), ("from invoiceflow import truth", True), ("from invoiceflow.truth import read_truth", True),
    ("from . import truth", True), ("from .truth import write_truth", True), ("x = module.read_truth(p)", True), ("import invoiceflow.cord", False),
    ("from invoiceflow import cord, files", False), ("truth = 5", False), ("import truthy", False),
])
def test_the_static_check_knows_every_way_to_import_the_truth(source, expected):
    assert reads_the_truth(source) is expected


def test_no_flow_module_imports_the_truth_reader():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC)
        in_flow = relative.name in FLOW_MODULES and len(relative.parts) == 1 or relative.parts[0] in FLOW_PACKAGES
        if in_flow and reads_the_truth(path.read_text(encoding="utf-8")):
            offenders.append(relative.as_posix())
        if relative.name not in MAY_READ and uses_the_reader(path.read_text(encoding="utf-8")):
            offenders.append(f"{relative.as_posix()} (only the evaluation and the baselines use read_truth)")
    assert offenders == []


def test_the_module_that_guards_is_named_where_the_plan_says():
    assert truth_module.__name__ == "invoiceflow.truth" and truth_module.READERS == ("invoiceflow.evaluation", "invoiceflow.baselines")
