"""The test part of the seal and the evaluation that writes it (U16; RF17, RF19; D21; AC20): the test is evaluated once, with the sealed configuration, and the first evaluation is the one that is sealed;
with another configuration before the test part exists it is refused before anything is written; after it, it is computed apart and never replaces the sealed one."""

from __future__ import annotations

import copy
import json

import pytest

from invoiceflow.registry import (
    EvaluationRefusedError,
    SealError,
    SealMismatchError,
    check_test_evaluation,
    read_seal,
    sealed_view,
    write_launch_seal,
    write_test_seal,
)
from tests.fixtures.seals import launch_document, make_test_part
from tests.unit.test_seal import observed_for, with_key


@pytest.fixture
def sealed(quick_view, tmp_path):
    seal = tmp_path / "reports" / "model_seal.json"
    launch = launch_document(quick_view)
    write_launch_seal(seal, launch)
    return seal, launch


def another_view(quick_cfg):
    return sealed_view(with_key(quick_cfg, "match.price_tolerance_pct"), observed_for(quick_cfg))


def test_evaluating_the_test_under_another_config_before_the_test_seal_is_refused(quick_view, quick_cfg, sealed):
    seal, launch = sealed
    before = seal.read_bytes()
    assert check_test_evaluation(seal, quick_view) == "first"
    with pytest.raises(EvaluationRefusedError, match="match.price_tolerance_pct") as caught:
        check_test_evaluation(seal, another_view(quick_cfg))
    assert "R7" in str(caught.value) and EvaluationRefusedError.EXIT_CODE == 2 and seal.read_bytes() == before, "nothing was written"
    with pytest.raises(EvaluationRefusedError, match="no hay sello"):
        check_test_evaluation(seal.parent / "none.json", quick_view)
    assert not (seal.parent / "none.json").exists()


def test_the_test_part_is_written_once_and_never_rewritten_and_the_launch_part_keeps_its_previous_behaviour(quick_view, quick_cfg, sealed):
    seal, launch = sealed
    part = make_test_part(launch)
    assert write_test_seal(seal, part) == "written"
    document = read_seal(seal)
    assert document["launch"] == launch and document["test"] == json.loads(json.dumps(part)) and check_test_evaluation(seal, quick_view) == "repeat"
    kept = seal.read_bytes()
    again = make_test_part(launch, run_key="run-replay", as_of="2026-10-30 00:00:00")
    assert write_test_seal(seal, again) == "unchanged" and seal.read_bytes() == kept, "another run, the same values"
    other = copy.deepcopy(part)
    other["metrics"]["lines"]["known"]["rate"] = 0.5
    with pytest.raises(SealMismatchError, match="metrics.lines.known.rate") as caught:
        write_test_seal(seal, other)
    assert "R7" in str(caught.value) and seal.read_bytes() == kept
    # another configuration after the test part: computed apart, the seal stays
    foreign = launch_document(another_view(quick_cfg))
    assert check_test_evaluation(seal, another_view(quick_cfg)) == "post_seal"
    assert write_test_seal(seal, make_test_part(foreign)) == "post_seal" and seal.read_bytes() == kept
    assert write_launch_seal(seal, launch) == "unchanged" and write_launch_seal(seal, foreign) == "post_seal" and seal.read_bytes() == kept


def test_a_test_part_without_a_seal_or_with_a_bad_shape_is_refused_before_writing(quick_view, sealed, tmp_path):
    seal, launch = sealed
    with pytest.raises(SealError, match="no hay sello"):
        write_test_seal(tmp_path / "none.json", make_test_part(launch))
    before = seal.read_bytes()
    part = make_test_part(launch)
    for bad in ({k: v for k, v in part.items() if k != "criteria"}, {**part, "extra": 1}, {**part, "config_sha256": "zz"}, {**part, "metrics": {"x": float("nan")}}):
        with pytest.raises(SealError):
            write_test_seal(seal, bad)
        assert seal.read_bytes() == before


def test_a_write_that_the_file_does_not_confirm_is_an_error(sealed, monkeypatch):
    """Two writers at once (the lock is U19's) could leave the other one's test part: the file is read back and must say what this run wrote."""
    from invoiceflow import registry

    seal, launch = sealed
    real = registry.write_atomic

    def another_writer(path, data):
        document = json.loads(data)
        document["test"]["metrics"]["days"] = 99
        return real(path, json.dumps(document))

    monkeypatch.setattr(registry, "write_atomic", another_writer)
    with pytest.raises(SealMismatchError, match="otra escritura"):
        write_test_seal(seal, make_test_part(launch))
