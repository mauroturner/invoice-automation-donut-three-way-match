"""A launch seal written by hand for the tests of the generator's guard (U06): what ``registry.write_launch_seal`` of U14 will write is a bigger document, but
the guard reads only ``launch.config_sha256``."""

from __future__ import annotations

import json
from pathlib import Path

from invoiceflow.config import SealedConfig
from invoiceflow.registry import config_fingerprint


def write_launch_seal(path: Path, view: SealedConfig, *, config_sha256: str | None = None) -> Path:
    """A seal whose ``launch`` part carries the fingerprint of `view` (or the given text)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"launch": {"config_sha256": config_sha256 or config_fingerprint(view)}}), encoding="utf-8")
    return path


# --- a whole launch part (U14): what ``registry.write_launch_seal`` writes ---------------------------------------------------------


def launch_document(view: SealedConfig, **changes) -> dict:
    """A launch part with the keys of ``LAUNCH_KEYS`` and small values, whose ``config_sha256`` is the real fingerprint of `view` (its master, its fonts and its JSON). Another value is
    given by keyword; a ``config_sha256`` that is given replaces the fingerprint, and then the part is one that the writer refuses."""
    from invoiceflow.registry import fingerprint_document, fingerprint_of

    document = fingerprint_document(view)
    base = {
        "run_key": "run-1", "as_of": "2026-10-01 00:00:00", "config_sha256": fingerprint_of(document), "sealed_config": document["view"], "source_sha256": {"arca_spec": "a" * 64},
        "master_sha256": document["master"], "fonts_sha256": document["fonts"], "model_version": "v1", "artifact_name": "model-v1", "artifact_sha256": "b" * 64,
        "tokenizer_sha256": "c" * 64, "chosen_epoch": 2,
        "choice": {"chosen_epoch": 2, "score": 0.5123456789, "floor": 91.65, "checkpoint_cord_ted": 93.65, "cord_val_tolerance": 2.0,
                   "candidates": [{"epoch": 1, "cord_ted": 92.1, "exact_known": 0.4, "exact_new": 0.1, "score": 0.25, "status": "kept", "fit_splits": "cord_train+synth_train"},
                                  {"epoch": 2, "cord_ted": 93.0, "exact_known": 0.9, "exact_new": 0.1246913578, "score": 0.5123456789, "status": "kept", "fit_splits": "cord_train+synth_train"}]},
        "checkpoint_cord_val": {"ted": 93.65, "receipts": 100}, "oracle_validation": {"invoices": 300, "wrong": 0}, "validation_digest": "d" * 64,
    }
    return {**base, **changes}


def write_full_seal(path: Path, view: SealedConfig, **changes) -> Path:
    """A seal with a whole launch part written directly (not through the registry's writer), for the tests that read it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "launch": launch_document(view, **changes)}), encoding="utf-8")
    return path
