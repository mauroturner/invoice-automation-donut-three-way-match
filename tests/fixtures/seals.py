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
