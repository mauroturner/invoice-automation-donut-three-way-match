"""The fingerprint of the sealed view, the seal and the model registry (plan section 13; decision D21).

``config_fingerprint(view)`` is the ``config_sha256`` of the seal: the SHA-256 of the sealed view (every key that can change a sealed value and the observed hashes
of the CORD files and the PDF), of the master the view generates and of the font files the pages are drawn with. A key outside the sealed view cannot change it
(the view holds no other); a font file or a master that changes does, which is why the test invoices cannot be generated from a configuration that is not the
one that was sealed.

The seal is ``reports/model_seal.json``, ``{"version": 1, "launch": {...}, "test": {...}}``. ``write_launch_seal`` creates it exclusively (all or nothing, once) with the launch part; a
repetition with the same configuration must reproduce every sealed value to 6 decimals (``SealMismatchError`` names each one that differs; the artifact's bytes, the run and the instant are
what a repetition may change), and another configuration is ``post_seal`` and leaves the seal as it is. ``check_test_evaluation`` and ``write_test_seal`` (U16) add the test part, once.
``read_seal`` judges the whole shape; a file that is not a seal is a ``SealError`` and never an absent seal.

``seal_state`` is what the generator's guard asks, and it reads the fingerprint of the launch part only: ``none`` (no seal, or one with no ``launch`` part), ``match`` (its ``launch`` part has this fingerprint) or
``post_seal`` (it has another). A seal that is not the exact shape is an error and never a state: nothing is generated from a document nobody can read.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import math
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from invoiceflow.config import PipelineConfig, SealedConfig
from invoiceflow.files import write_atomic
from invoiceflow.synth import layouts
from invoiceflow.synth.master import fingerprint as _master_fingerprint
from invoiceflow.synth.master import master, to_jsonable

logger = logging.getLogger(__name__)

_HEX64 = frozenset("0123456789abcdef")
MODEL_VERSION = "v1"
SEAL_VERSION = 1
SEAL_DECIMALS = 6                     # a repetition must reproduce the sealed values rounded to this many decimals
SEAL_MODE = 0o600                     # owner only, where the mode is the access control
LAUNCH_KEYS = ("run_key", "as_of", "config_sha256", "sealed_config", "source_sha256", "master_sha256", "fonts_sha256", "model_version", "artifact_name", "artifact_sha256",
               "tokenizer_sha256", "chosen_epoch", "choice", "checkpoint_cord_val", "oracle_validation", "validation_digest")
# what a repetition may change: the run that computed the values, its instant and the bytes of the artifact (a fine-tune on a GPU is not deterministic to the byte; the registry
# holds the hash of the files that are used, which M02 checks)
_NOT_COMPARED = ("run_key", "as_of", "artifact_sha256")
_IDENTIFIERS = ("epoch", "key", "id", "name", "method", "scope", "version", "check", "variant", "date_key", "invoice_key")     # what names an entry of a list of objects
_NO_HARD_LINKS = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS})
_WINDOWS_NO_HARD_LINKS = frozenset({1, 50})      # ERROR_INVALID_FUNCTION and ERROR_NOT_SUPPORTED
_ABSENT = object()
REGISTRY_COLUMNS = ("model_version", "trained_as_of", "artifact_name", "artifact_sha256", "tokenizer_sha256", "config_sha256", "chosen_epoch", "choice_json", "validation_digest")


@dataclass(frozen=True)
class RunMeta:
    """The run that computes something that is sealed or registered: its key and the instant it stands at (``as_of`` of its delivery)."""

    run_key: str
    as_of: datetime


class SealError(ValueError):
    """The seal is not the document this project writes, or a part of it is not what it must be. Nothing is replaced (runbook R7). It is a ``ValueError`` so that the guard of the
    generator, which has always raised one for a seal it cannot read, keeps raising one."""


class SealMismatchError(SealError):
    """The configuration is the sealed one and the values this run computed are not (R7): the seal is never rewritten, and the message names each value."""

    EXIT_CODE = 2


class EvaluationRefusedError(SealError):
    """The test is evaluated with another configuration than the sealed one before the seal has its test part, or without a seal (R7); nothing was written. The command line exits with 2."""

    EXIT_CODE = 2


def fonts_sha256(names: tuple[str, ...]) -> str:
    """One hash of the font files `names` (each by name and content), in the order given."""
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(layouts.font_path(name).read_bytes()).digest())
    return digest.hexdigest()


def master_fingerprint(view: SealedConfig) -> str:
    """The fingerprint of the master that `view` generates (it reads the ``[synth]`` and ``[calendar]`` sections only)."""
    return _master_fingerprint(master(view))


def sealed_view(cfg: PipelineConfig, observed: Mapping[str, str]) -> SealedConfig:
    """The sealed view of `cfg` for the hashes `observed` (``sources.check_sources``: each CORD file by its name and the PDF under ``arca_spec``)."""
    from invoiceflow.sources import ARCA_KEY

    return SealedConfig.of(cfg, tuple(observed[entry.name] for entry in cfg.source.cord_files), observed[ARCA_KEY])


def fingerprint_document(view: SealedConfig) -> dict[str, Any]:
    """What ``config_sha256`` is the hash of: the sealed view as JSON, the fingerprint of the master it generates and the hash of the font files its pages are drawn with."""
    synth = view.synth
    names = tuple(sorted({*synth.fonts_known, synth.font_validation_new, synth.font_test_new}))
    return {"view": to_jsonable(view), "master": master_fingerprint(view), "fonts": fonts_sha256(names)}


def fingerprint_of(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def config_fingerprint(view: SealedConfig) -> str:
    """The ``config_sha256`` of `view`: its canonical JSON, the master's fingerprint and the fonts', hashed together."""
    return fingerprint_of(fingerprint_document(view))


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


# --- the seal (plan section 13; decisions D21, R7) -------------------------------------------------------------------------


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _flat(document: Any, prefix: str = "") -> dict[str, Any]:
    """``{dotted path: value}`` of the leaves of nested objects. A list of objects is entered entry by entry, each named by its identifiers or by its position; any other list is one leaf."""
    if isinstance(document, Mapping):
        out: dict[str, Any] = {}
        for key, value in document.items():
            out.update(_flat(value, f"{prefix}.{key}" if prefix else str(key)))
        return out if out else {prefix: {}}
    if isinstance(document, (list, tuple)) and document and all(isinstance(item, Mapping) for item in document):
        out = {}
        for position, item in enumerate(document):
            name = "|".join(str(item[k]) for k in _IDENTIFIERS if k in item) or str(position)
            out.update({f"{prefix}.{name}.{key}": value for key, value in _flat(item).items()})
        return out
    return {prefix: document}


def differing_keys(sealed: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """The keys (``section.key``) whose values differ between two documents, sorted."""
    a, b = _flat(sealed), _flat(current)
    return sorted(key for key in set(a) | set(b) if a.get(key, _ABSENT) != b.get(key, _ABSENT))


def _rounded(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SealError("un valor del sello no es un número finito")
        return round(value, SEAL_DECIMALS)
    if isinstance(value, Mapping):
        return {str(key): _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    raise SealError(f"un valor del sello no es JSON: {type(value).__name__}")


def read_seal(seal_path: Path) -> dict | None:
    """The seal at `seal_path`, or None if there is no file. A file that is not a seal is a ``SealError``: it is never taken for an absent one."""
    path = Path(seal_path)
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SealError(f"el sello {path.name} no se puede leer: {error}") from error
    if not isinstance(document, dict) or document.get("version") != SEAL_VERSION or not set(document) <= {"version", "launch", "test"}:
        raise SealError(f"el sello {path.name} no tiene la forma de la versión {SEAL_VERSION}")
    launch = document.get("launch")
    if not isinstance(launch, dict) or set(launch) != set(LAUNCH_KEYS) or not _is_sha256(launch.get("config_sha256")):
        raise SealError(f"la parte del lanzamiento del sello {path.name} no tiene sus claves o su huella")
    test = document.get("test")
    if test is not None and (not isinstance(test, dict) or not _is_sha256(test.get("config_sha256"))):
        raise SealError(f"la parte de la prueba del sello {path.name} no tiene su huella")
    return document


def _dump(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _lacks_hard_links(error: OSError) -> bool:
    return error.errno in _NO_HARD_LINKS or getattr(error, "winerror", None) in _WINDOWS_NO_HARD_LINKS


def _create_without_link(path: Path, data: bytes) -> bool:
    """The creation for a file system without hard links: the exclusive create is the check; a half file is removed if the write fails."""
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), SEAL_MODE)
    except FileExistsError:
        return False
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return True


def _create_exclusive(path: Path, data: bytes) -> bool:
    """Create `path` with `data` only if it does not exist, all or nothing: True if created, False if a file was already there. The data goes to a temporary file, flushed to disk, that is
    hard-linked to `path`, which fails if `path` exists and never shows half a file; without hard links an exclusive create is the check."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(name, path)
            return True
        except FileExistsError:
            return False
        except OSError as error:
            if not _lacks_hard_links(error):
                raise
            return _create_without_link(path, data)
    finally:
        Path(name).unlink(missing_ok=True)


def _check_part(part: Mapping[str, Any], keys: tuple[str, ...], what: str) -> None:
    missing, extra = [k for k in keys if k not in part], sorted(set(part) - set(keys))
    if missing or extra:
        raise SealError(f"la parte {what} debe tener exactamente las claves {list(keys)}; faltan {missing}, sobran {extra}")
    if not _is_sha256(part["config_sha256"]):
        raise SealError(f"config_sha256 de la parte {what} debe ser un SHA-256 de 64 dígitos hexadecimales")
    if "sealed_config" in keys:       # the part that carries its configuration (the launch's): the fingerprint is the one of that configuration, the master and the fonts
        document = {"view": part["sealed_config"], "master": part["master_sha256"], "fonts": part["fonts_sha256"]}
        if fingerprint_of(document) != part["config_sha256"]:
            raise SealError("config_sha256 no es la huella de la configuración sellada, del maestro y de las fuentes de la parte del lanzamiento")
    _rounded(dict(part))                 # refuses a value that is not JSON, or not finite, before anything is compared or written


def _differences(sealed: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    a = _flat(_rounded({k: v for k, v in sealed.items() if k not in _NOT_COMPARED}))
    b = _flat(_rounded({k: v for k, v in current.items() if k not in _NOT_COMPARED}))
    return sorted(key for key in set(a) | set(b) if a.get(key, _ABSENT) != b.get(key, _ABSENT))


def launch_seal_outcome(seal_path: Path, launch: Mapping[str, Any]) -> Literal["written", "unchanged", "post_seal"]:
    """What writing the launch part would do, decided **before** anything is written: ``written`` (there is no seal), ``unchanged`` (the seal has this configuration and every sealed value,
    rounded to 6 decimals, is this one) or ``post_seal`` (the seal has another configuration; M03). The same configuration with another sealed value is a ``SealMismatchError`` that names
    each value (runbook R7)."""
    _check_part(launch, LAUNCH_KEYS, "del lanzamiento")
    document = read_seal(seal_path)
    if document is None:
        return "written"
    sealed = document["launch"]
    if sealed["config_sha256"] != launch["config_sha256"]:
        return "post_seal"
    differ = _differences(sealed, launch)
    if differ:
        then, now = _flat(_rounded(sealed)), _flat(_rounded(dict(launch)))
        named = "; ".join(f"{key}: {then.get(key)!r} sellado, {now.get(key)!r} en esta corrida" for key in differ[:6])
        raise SealMismatchError(f"la configuración es la sellada pero el lanzamiento no la reproduce: {named}{' y otros' if len(differ) > 6 else ''} (docs/runbook.md, R7)")
    return "unchanged"


def write_launch_seal(seal_path: Path, launch: Mapping[str, Any]) -> Literal["written", "unchanged", "post_seal"]:
    """Write the launch part of the seal once (an exclusive creation) and say what happened (``launch_seal_outcome``). An existing seal is never rewritten."""
    path = Path(seal_path)
    outcome = launch_seal_outcome(path, launch)
    if outcome == "written":
        if _create_exclusive(path, _dump({"version": SEAL_VERSION, "launch": dict(launch)})):
            logger.info("seal: launch part written to %s (config %s)", path.name, launch["config_sha256"][:12])
            return "written"
        return launch_seal_outcome(path, launch)         # someone created it between the decision and the write: judge against what is there
    if outcome == "post_seal":
        logger.warning("seal: this configuration (%s) is not the sealed one: post_seal; the keys that differ: %s", launch["config_sha256"][:12],
                       ", ".join(differing_keys((read_seal(path) or {})["launch"]["sealed_config"], launch["sealed_config"])))
    return outcome


# --- the registry of the model (plan section 5, ``model_registry``; decision D13) -------------------------------------------------


def register_model(conn: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    """Insert the one row of the model into ``model_registry`` (insert-only: a second row of the same version is refused by the table). In a transaction of its own."""
    missing = [c for c in REGISTRY_COLUMNS if c not in row]
    if missing:
        raise ValueError(f"la fila de model_registry no tiene {missing}")
    names = ", ".join(REGISTRY_COLUMNS)
    marks = ", ".join(f":{c}" for c in REGISTRY_COLUMNS)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"INSERT INTO model_registry ({names}) VALUES ({marks})", {c: row[c] for c in REGISTRY_COLUMNS})
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def read_registry(conn: sqlite3.Connection, version: str = MODEL_VERSION) -> dict[str, Any] | None:
    """The row of model `version`, or None."""
    cursor = conn.execute(f"SELECT {', '.join(REGISTRY_COLUMNS)} FROM model_registry WHERE model_version = ?", (version,))
    found = cursor.fetchone()
    return None if found is None else dict(zip(REGISTRY_COLUMNS, found, strict=True))
