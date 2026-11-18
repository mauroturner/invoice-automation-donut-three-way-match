"""The fingerprint and the launch part of the seal (U14; RF17, RF19; D21; M03; AC20): every key that can change a sealed value is in ``config_sha256``, a key declared unsealed
changes nothing sealed, the seal is written once, a repetition reproduces it and another configuration is post-seal. The launch itself is ``tests/integration/test_launch.py``."""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from invoiceflow.config import FINGERPRINT_KEYS, UNSEALED_KEYS, PipelineConfig, SealedConfig
from invoiceflow.registry import (
    SealError,
    SealMismatchError,
    config_fingerprint,
    differing_keys,
    fingerprint_document,
    launch_seal_outcome,
    read_seal,
    seal_state,
    sealed_view,
    write_launch_seal,
)
from invoiceflow.sources import ARCA_KEY
from tests.fixtures.seals import launch_document

SRC = Path(__file__).resolve().parents[2] / "src" / "invoiceflow"
SEALED_MODULES = ("qr.py", "match.py", "decide.py", "normalize.py", "baselines.py", "metrics.py")


def changed(value):
    """Another value of the same type as `value`."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value * 1.5 + 0.01
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, date):
        return value + timedelta(days=1)
    if isinstance(value, (tuple, list)):
        return (*value[:-1], changed(value[-1]))
    raise AssertionError(f"no way to change {value!r}")


# a font is looked up by name when the fingerprint hashes the font files, so its other value is another font that exists
OTHER_FONT = {"synth.fonts_known": ("DejaVuSans", "DejaVuSerif"), "synth.font_validation_new": "DejaVuSans", "synth.font_test_new": "DejaVuSansMono"}


def with_key(cfg: PipelineConfig, key: str) -> PipelineConfig:
    """`cfg` with the leaf `key` (``section.key`` or ``section.table.key``) changed to another value of its type."""
    *path, name = key.split(".")

    def rebuild(node, rest):
        if not rest:
            return dataclasses.replace(node, **{name: OTHER_FONT[key] if key in OTHER_FONT else changed(getattr(node, name))})
        return dataclasses.replace(node, **{rest[0]: rebuild(getattr(node, rest[0]), rest[1:])})

    return rebuild(cfg, path)


OBSERVED = {ARCA_KEY: "a" * 64}


def observed_for(cfg: PipelineConfig, digest: str = "1" * 64) -> dict[str, str]:
    return {**{f.name: digest for f in cfg.source.cord_files}, ARCA_KEY: "a" * 64}


def fingerprint_of_config(cfg: PipelineConfig, observed: dict[str, str] | None = None) -> str:
    return config_fingerprint(sealed_view(cfg, observed or observed_for(cfg)))


def test_changing_any_fingerprinted_key_changes_config_sha256(quick_cfg):
    """AC20: one key at a time, every one of ``FINGERPRINT_KEYS`` moves the hash. (The quick configuration holds the same keys and a small master.)"""
    base = fingerprint_of_config(quick_cfg)
    for key in FINGERPRINT_KEYS:
        assert fingerprint_of_config(with_key(quick_cfg, key)) != base, key
    assert fingerprint_of_config(quick_cfg, observed_for(quick_cfg, "2" * 64)) != base, "the hash of the CORD files that were read"
    assert fingerprint_of_config(quick_cfg, {**observed_for(quick_cfg), ARCA_KEY: "b" * 64}) != base, "the hash of the PDF that was read"


def test_changing_a_key_declared_unsealed_changes_nothing_that_is_sealed(quick_cfg):
    base = fingerprint_of_config(quick_cfg)
    for key in UNSEALED_KEYS:
        if key == "source.cord_files":
            continue          # a list of tables: the hashes it holds are the ones that were read (the previous test)
        *path, name = key.split(".")
        node = quick_cfg
        for part in path:
            node = getattr(node, part)
        value = getattr(node, name)
        other = Path(str(value) + "x") if isinstance(value, Path) else changed(value)

        def rebuild(root, rest):
            if not rest:
                return dataclasses.replace(root, **{name: other})
            return dataclasses.replace(root, **{rest[0]: rebuild(getattr(root, rest[0]), rest[1:])})

        assert fingerprint_of_config(rebuild(quick_cfg, path)) == base, key


def test_the_fingerprint_hashes_the_sealed_view_the_master_and_the_fonts_and_never_the_toml_file(quick_view):
    document = fingerprint_document(quick_view)
    assert set(document) == {"view", "master", "fonts"} and re.fullmatch(r"[0-9a-f]{64}", document["master"]) and re.fullmatch(r"[0-9a-f]{64}", document["fonts"])
    text = json.dumps(document, sort_keys=True)
    assert "config_file_sha256" not in text and quick_view.arca_sha256_seen in text
    assert document["view"]["calendar"]["test_last_day"] == "2026-10-05" and document["view"]["match"]["price_tolerance_pct"] == 2.0
    assert "paths" not in document["view"] and "service" not in document["view"] and set(document["view"]["erp"]) == {"post_as"}


def test_sealed_calculations_receive_only_the_sealed_view():
    for module in SEALED_MODULES:
        text = (SRC / module).read_text(encoding="utf-8")
        assert not re.search(r"\bPipelineConfig\b", text), f"{module} names PipelineConfig"
        assert not re.search(r"\.paths\b|\.site\b|\.service\b", text), f"{module} reads an unsealed section"
    assert {f.name for f in dataclasses.fields(SealedConfig)} == {"source", "calendar", "synth", "qr", "intake", "model", "match", "erp", "evaluation", "criteria", "cord_sha256_seen",
                                                                   "arca_sha256_seen"}


# --- the launch part of the seal ---------------------------------------------------------------------------------------------


def test_the_seal_is_written_once_and_a_repetition_with_the_same_configuration_reproduces_it(quick_view, tmp_path):
    seal = tmp_path / "reports" / "model_seal.json"
    document = launch_document(quick_view)
    assert read_seal(seal) is None and seal_state(seal, document["config_sha256"]) == "none"
    assert launch_seal_outcome(seal, document) == "written" and not seal.exists(), "the outcome is decided before anything is written"
    assert write_launch_seal(seal, document) == "written"
    raw = seal.read_bytes()
    assert b"\r" not in raw and json.loads(raw)["version"] == 1 and json.loads(raw)["launch"]["config_sha256"] == document["config_sha256"]
    assert seal_state(seal, document["config_sha256"]) == "match" and seal_state(seal, "f" * 64) == "post_seal"
    again = launch_document(quick_view, run_key="run-2", as_of="2026-10-02 00:00:00", artifact_sha256="e" * 64)       # what a repetition may change
    assert launch_seal_outcome(seal, again) == "unchanged" and write_launch_seal(seal, again) == "unchanged" and seal.read_bytes() == raw
    choice = copy.deepcopy(document["choice"])
    choice["candidates"][1]["exact_new"] = 0.1246913579                                       # the 10th decimal: equal at 6 decimals
    assert write_launch_seal(seal, launch_document(quick_view, choice=choice)) == "unchanged" and seal.read_bytes() == raw


def test_the_same_configuration_with_another_sealed_value_is_refused_and_names_each_value(quick_view, tmp_path):
    seal = tmp_path / "model_seal.json"
    write_launch_seal(seal, launch_document(quick_view))
    before = seal.read_bytes()
    choice = copy.deepcopy(launch_document(quick_view)["choice"])
    choice["candidates"][1]["exact_new"] = 0.31
    choice["checkpoint_cord_ted"] = 90.0
    other = launch_document(quick_view, choice=choice, chosen_epoch=1, checkpoint_cord_val={"ted": 80.0, "receipts": 100}, validation_digest="9" * 64)
    for call in (launch_seal_outcome, write_launch_seal):
        with pytest.raises(SealMismatchError) as caught:
            call(seal, other)
        message = str(caught.value)
        assert "choice.candidates.2.exact_new" in message and "0.31" in message and "chosen_epoch" in message, message
    assert seal.read_bytes() == before and SealMismatchError.EXIT_CODE == 2


def test_another_configuration_is_post_seal_and_the_seal_stays(quick_view, quick_cfg, tmp_path):
    seal = tmp_path / "model_seal.json"
    write_launch_seal(seal, launch_document(quick_view))
    before = seal.read_bytes()
    other_view = sealed_view(with_key(quick_cfg, "match.price_tolerance_pct"), observed_for(quick_cfg))
    other = launch_document(other_view)
    assert other["config_sha256"] != launch_document(quick_view)["config_sha256"]
    assert launch_seal_outcome(seal, other) == "post_seal" and write_launch_seal(seal, other) == "post_seal" and seal.read_bytes() == before
    assert "match.price_tolerance_pct" in differing_keys(json.loads(before)["launch"]["sealed_config"], other["sealed_config"])
    assert seal_state(seal, other["config_sha256"]) == "post_seal"


def test_a_file_that_is_not_a_seal_is_an_error_and_never_an_absent_one(quick_view, tmp_path):
    seal = tmp_path / "model_seal.json"
    whole = launch_document(quick_view)
    for content in ("not json", json.dumps({"version": 2}), json.dumps({"version": 1, "launch": {"config_sha256": "x"}}), json.dumps({"version": 1, "extra": 1}),
                    json.dumps({"version": 1, "launch": {**whole, "extra": 1}}), json.dumps({"version": 1, "launch": {k: v for k, v in whole.items() if k != "choice"}}),
                    json.dumps({"version": 1, "launch": whole, "test": {"config_sha256": "no"}}), json.dumps([1])):
        seal.write_text(content, encoding="utf-8")
        with pytest.raises(SealError, match="sello"):
            read_seal(seal)
        with pytest.raises(ValueError, match="sello"):
            write_launch_seal(seal, whole)
        assert seal.read_text(encoding="utf-8") == content, "nothing was replaced"


def test_a_seal_part_with_a_missing_key_or_a_bad_fingerprint_or_a_value_that_is_not_a_number_is_refused_before_writing(quick_view, tmp_path):
    seal = tmp_path / "reports" / "model_seal.json"
    good = launch_document(quick_view)
    bad_choice = copy.deepcopy(good["choice"])
    bad_choice["score"] = float("nan")
    for bad in ({k: v for k, v in good.items() if k != "choice"}, {**good, "extra": 1}, {**good, "config_sha256": "zz"}, {**good, "config_sha256": "0" * 64},
                {**good, "choice": bad_choice}, {**good, "sealed_config": {"other": 1}}, {**good, "master_sha256": "0" * 64}, {**good, "fonts_sha256": "0" * 64}):
        with pytest.raises(SealError):
            write_launch_seal(seal, bad)
        assert not seal.exists() and not seal.parent.exists(), "a refusal writes nothing, not even the folder"


def test_two_writers_that_race_for_the_seal_leave_one_whole_file(quick_view, tmp_path):
    seal = tmp_path / "model_seal.json"
    document = launch_document(quick_view)
    outcomes: list[str] = []
    barrier = threading.Barrier(4)

    def write():
        barrier.wait()
        outcomes.append(write_launch_seal(seal, launch_document(quick_view, run_key=f"run-{threading.get_ident()}")))

    threads = [threading.Thread(target=write) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes) == ["unchanged", "unchanged", "unchanged", "written"]
    assert json.loads(seal.read_text(encoding="utf-8"))["launch"]["config_sha256"] == document["config_sha256"] and not list(seal.parent.glob("*.tmp"))
