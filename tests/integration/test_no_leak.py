"""No leak (U04 in part; AC23, D10): CORD's test split opens only in the closing evaluation, which is the one function ``evaluation.evaluate_cord`` of the closing
run; nothing else in the flow opens it, not even to count. U06 and U15 add the synthetic truth and the future (AC10, AC11) to this file."""

from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

from invoiceflow import cord
from invoiceflow.leakage import LeakageError
from invoiceflow.sources import check_sources, fetch_source
from tests.fixtures.make_cord_fixture import FILE_NAMES
from tests.fixtures.projects import make_project

SRC = Path(__file__).resolve().parents[2] / "src" / "invoiceflow"
TEST_FILE = FILE_NAMES["test"]


def forged_evaluation() -> types.ModuleType:
    """A module that says it is ``invoiceflow.evaluation``, with ``evaluate_cord`` and another function: what the guard looks at is the name of the
    calling module and function, as the truth reader's guard does (a tripwire against a mistake, not a defence against someone who forges a module)."""
    module = types.ModuleType("invoiceflow.evaluation")
    exec(
        "def evaluate_cord(read, cfg, closing):\n    return list(read(cfg, 'test', closing_run=closing))\n"
        "def evaluate_something_else(read, cfg):\n    return list(read(cfg, 'test', closing_run=True))\n",
        module.__dict__,
    )
    return module


@pytest.fixture
def rows_opened(monkeypatch):
    """The names of the files whose rows were opened."""
    opened: list[str] = []
    real = cord._iter_rows
    monkeypatch.setattr(cord, "_iter_rows", lambda path, *args, **kwargs: (opened.append(Path(path).name), real(path, *args, **kwargs))[1])
    return opened


def test_the_cord_test_split_opens_only_in_the_closing_evaluation(tmp_path, rows_opened):
    project = make_project(tmp_path)
    cfg = project.cfg
    # from the tests, whatever the flag: refused at the call, before any row is opened
    for kwargs in ({}, {"closing_run": False}, {"closing_run": True}):
        with pytest.raises(LeakageError, match=r"test.*cierre"):
            cord.read_split(cfg, "test", **kwargs)
    evaluation = forged_evaluation()
    # from the evaluation module but another function, or from evaluate_cord of a run that is not the closing one: refused
    with pytest.raises(LeakageError, match=r"evaluate_cord"):
        evaluation.evaluate_something_else(cord.read_split, cfg)
    with pytest.raises(LeakageError, match=r"cierre"):
        evaluation.evaluate_cord(cord.read_split, cfg, False)
    assert rows_opened == [], "every refusal came before a row was opened"
    # from evaluate_cord of the closing run: the four receipts of the test file
    receipts = evaluation.evaluate_cord(cord.read_split, cfg, True)
    assert [r.receipt_key for r in receipts] == [f"test-{n}" for n in range(4)] and all(r.split == "test" for r in receipts)
    assert rows_opened == [TEST_FILE]


def test_the_other_splits_have_no_guard_and_an_unknown_one_is_refused(tmp_path, rows_opened):
    cfg = make_project(tmp_path).cfg
    assert len(list(cord.read_split(cfg, "train"))) == 16 and len(list(cord.read_split(cfg, "validation"))) == 4
    assert rows_opened == [FILE_NAMES["train"], FILE_NAMES["validation"]]
    with pytest.raises(ValueError, match="dev"):
        cord.read_split(cfg, "dev")


def test_checking_fetching_and_indexing_never_open_the_rows_of_the_test_file(tmp_path, rows_opened):
    project = make_project(tmp_path)
    hashes = check_sources(project.cfg)
    cord.check_footers(project.cfg)
    cord.build_index(project.cfg, hashes)
    fetch_source(project.cfg)
    fetch_source(project.cfg)
    assert TEST_FILE not in rows_opened and set(rows_opened) == {FILE_NAMES["train"], FILE_NAMES["validation"]}


def test_the_test_file_is_known_by_its_size_its_hash_and_its_footer_only(tmp_path):
    project = make_project(tmp_path, accepted=True)
    assert check_sources(project.cfg)[TEST_FILE] and cord.check_footers(project.cfg)["test"] == 4
    result = fetch_source(project.cfg)
    assert result["rows"] == {"train": 16, "validation": 4, "test": 4}


def test_no_module_but_the_closing_evaluation_reads_the_test_split():
    """Static: a call ``read_split(<cfg>, "test", ...)`` or one that passes the test split by name is allowed in ``evaluation.py`` only (U16 writes it)."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "cord.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                if name == "read_split" and any(isinstance(arg, ast.Constant) and arg.value == "test" for arg in [*node.args, *(k.value for k in node.keywords)]):
                    offenders.append(f"{path.relative_to(SRC).as_posix()}:{node.lineno}")
    assert [o for o in offenders if o.split(":")[0] != "evaluation.py"] == []
