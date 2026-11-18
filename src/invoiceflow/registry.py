"""The fingerprint of the sealed view and the state of the seal (plan section 13; decision D21). U14 adds the seal's writers, the launch and the rest.

``config_fingerprint(view)`` is the ``config_sha256`` of the seal: the SHA-256 of the sealed view (every key that can change a sealed value and the observed hashes
of the CORD files and the PDF), of the master the view generates and of the font files the pages are drawn with. A key outside the sealed view cannot change it
(the view holds no other); a font file or a master that changes does, which is why the test invoices cannot be generated from a configuration that is not the
one that was sealed.

``seal_state`` is what the generator's guard asks: ``none`` (no seal, or one with no ``launch`` part), ``match`` (its ``launch`` part has this fingerprint) or
``post_seal`` (it has another). A seal that is not the exact shape is an error and never a state: nothing is generated from a document nobody can read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from invoiceflow.config import SealedConfig
from invoiceflow.synth import layouts
from invoiceflow.synth.master import fingerprint as _master_fingerprint
from invoiceflow.synth.master import master, to_jsonable

_HEX64 = frozenset("0123456789abcdef")


def fonts_sha256(names: tuple[str, ...]) -> str:
    """One hash of the font files `names` (each by name and content), in the order given."""
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(layouts.font_path(name).read_bytes()).digest())
    return digest.hexdigest()


def master_fingerprint(view: SealedConfig) -> str:
    """The fingerprint of the master that `view` generates (it reads the ``[synth]`` and ``[calendar]`` sections only)."""
    return _master_fingerprint(master(view))


def config_fingerprint(view: SealedConfig) -> str:
    """The ``config_sha256`` of `view`: its canonical JSON, the master's fingerprint and the fonts', hashed together."""
    synth = view.synth
    names = tuple(sorted({*synth.fonts_known, synth.font_validation_new, synth.font_test_new}))
    document = {"view": to_jsonable(view), "master": master_fingerprint(view), "fonts": fonts_sha256(names)}
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def seal_state(seal_path: Path, config_sha256: str) -> Literal["none", "match", "post_seal"]:
    """The state of the seal at `seal_path` for the fingerprint `config_sha256`. A missing file is ``none``; a file that is not JSON, not an object, or whose
    ``launch`` part is not an object with a 64-hex ``config_sha256`` is a ``ValueError`` that says it is the seal."""
    path = Path(seal_path)
    if not path.exists():
        return "none"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"el sello {path.name} no se puede leer como JSON") from error
    if not isinstance(document, dict):
        raise ValueError(f"el sello {path.name} no es un objeto JSON")
    if "launch" not in document:
        return "none"
    launch = document["launch"]
    digest = launch.get("config_sha256") if isinstance(launch, dict) else None
    if not (isinstance(digest, str) and len(digest) == 64 and set(digest) <= _HEX64):
        raise ValueError(f"el sello {path.name} tiene una parte launch sin su config_sha256")
    return "match" if digest == config_sha256 else "post_seal"
