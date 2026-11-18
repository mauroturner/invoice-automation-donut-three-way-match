"""The fingerprint of the sealed view and the state of the seal as the generator's guard reads it (U06; plan section 13, decision D21). U14 adds the seal's writers
and the rest of the registry; the fingerprint and the state are what the generation of the test needs."""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from invoiceflow import registry
from invoiceflow.registry import config_fingerprint, seal_state


def test_the_fingerprint_is_a_hash_of_the_sealed_view_the_master_and_the_fonts(view, monkeypatch):
    digest = config_fingerprint(view)
    assert re.fullmatch(r"[0-9a-f]{64}", digest) and config_fingerprint(view) == digest
    for section, key, value in (("synth", "seed_test", 304), ("synth", "n_train", 1999), ("qr", "max_text_bytes", 1000), ("match", "price_tolerance_pct", 2.5),
                                ("model", "epochs", 5), ("calendar", "test_last_day", view.calendar.test_last_day.replace(day=27)), ("criteria", "c03_min_line_exact", 0.7)):
        changed = dataclasses.replace(view, **{section: dataclasses.replace(getattr(view, section), **{key: value})})
        assert config_fingerprint(changed) != digest, f"{section}.{key}"
    assert config_fingerprint(dataclasses.replace(view, arca_sha256_seen="1" * 64)) != digest
    assert config_fingerprint(dataclasses.replace(view, cord_sha256_seen=("1" * 64, *view.cord_sha256_seen[1:]))) != digest
    assert config_fingerprint(dataclasses.replace(view, erp=dataclasses.replace(view.erp, post_as="posted"))) != digest
    monkeypatch.setattr(registry, "fonts_sha256", lambda names: "f" * 64)
    assert config_fingerprint(view) != digest, "the font files are part of it"
    monkeypatch.undo()
    monkeypatch.setattr(registry, "master_fingerprint", lambda v: "e" * 64)
    assert config_fingerprint(view) != digest, "and so is the generated master"


def test_the_state_of_a_seal_is_none_match_or_post_seal(tmp_path):
    digest = "a" * 64
    path = tmp_path / "model_seal.json"
    assert seal_state(path, digest) == "none"
    path.write_text(json.dumps({}), encoding="utf-8")
    assert seal_state(path, digest) == "none"
    path.write_text(json.dumps({"test": {"config_sha256": digest}}), encoding="utf-8")
    assert seal_state(path, digest) == "none", "only the launch part opens the generation of the test"
    path.write_text(json.dumps({"launch": {"config_sha256": digest}}), encoding="utf-8")
    assert seal_state(path, digest) == "match" and seal_state(path, "b" * 64) == "post_seal"


@pytest.mark.parametrize("text", ["{no", "[1]", '{"launch": []}', '{"launch": {"config_sha256": 5}}', '{"launch": {}}', ""])
def test_a_seal_that_is_not_the_exact_shape_is_an_error_not_a_state(tmp_path, text):
    path = tmp_path / "model_seal.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="sello"):
        seal_state(path, "a" * 64)
