"""The pinned sources (RF01, V01, V03 in part, V06; decision D02) and the first task, ``fetch_source``.

The six Parquet files of CORD and the PDF of ARCA's specification are untrusted data: the flow never downloads them (``research/s00_download.py`` and
``research/s08_arca_docs.py`` do, runbook R1), reads local files only and does not read a byte of one before its size and its SHA-256 are the accepted ones.

* ``check_sources`` is V01 (and V06 for the PDF): every file exists and has the accepted size and hash or, in quick mode (an empty accepted hash, a source
  under ``data/quick/``), the size and hash registered in ``source.lock.json`` by the first good view. The size is compared first: a file of another size is
  never hashed. The byte cap of V03 (``max_shard_bytes``) is held before any file is hashed. It writes nothing.
* ``fetch_source`` checks the sources, checks the footers (V02, V03, V05: ``cord.check_footers``), builds the canonical index of the train and validation
  splits and writes it, with its manifest last, only when it is missing or is not the one these hashes give, and registers the quick-mode hashes only after
  all of that went without a failing rule. A rejected source writes nothing and leaves what was there untouched. The test split's rows are never opened.

The exact reproduction of the specification's example (the other half of V06) is added with the QR payload writer, in U06.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from invoiceflow.config import PipelineConfig
from invoiceflow.files import sha256_file, write_atomic

LOCK_NAME = "source.lock.json"
LOCK_VERSION = 1
RUNBOOK_R1 = "runbook R1: research/s00_download.py y research/s08_arca_docs.py"
ARCA_KEY = "arca_spec"
_HEX64 = re.compile(r"[0-9a-f]{64}")


class SourceError(RuntimeError):
    """A rule of the sources (V01 to V06) failed; the exit code of the command line is 3. ``problems`` says what, one dict per failing item (a rule, a
    file, a split, a receipt's key: never a row's value)."""

    EXIT_CODE = 3

    def __init__(self, rule_id: str, message: str, problems: list[dict] | None = None) -> None:
        super().__init__(f"{rule_id}: {message}")
        self.rule_id = rule_id
        self.problems = problems if problems is not None else []


def rule(rule_id: str, message: str, runbook: bool | None = None, **problem: Any) -> SourceError:
    """The error of a failing rule: V01 and V06 tell where the runbook R1 is, the others do not."""
    tell = rule_id in ("V01", "V06") if runbook is None else runbook
    file = problem.get("file")
    if file and str(file) not in message:
        message = f"{message} (archivo {file})"   # a rule that names a file in its detail always names it in its message
    return SourceError(rule_id, f"{message} ({RUNBOOK_R1})" if tell else message, [{"rule_id": rule_id, **problem}])


# --- the registry of the quick mode ---------------------------------------------------------------------------------------


def lock_path(cfg: PipelineConfig) -> Path:
    """Next to the folder of the CORD files: ``data/quick/source.lock.json``."""
    return Path(cfg.source.cord_dir).parent / LOCK_NAME


def _valid_entry(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"sha256", "size"} and isinstance(value["sha256"], str) and bool(_HEX64.fullmatch(value["sha256"]))
            and isinstance(value["size"], int) and not isinstance(value["size"], bool) and value["size"] > 0)


def read_source_lock(path: Path) -> dict[str, Any] | None:
    """The hashes that quick mode registered, or None if none are. Anything else than the exact format is V01."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        valid = (isinstance(document, dict) and set(document) == {"version", "cord", "arca_spec"} and document["version"] == LOCK_VERSION
                 and isinstance(document["cord"], dict) and bool(document["cord"]) and all(_valid_entry(v) for v in document["cord"].values())
                 and _valid_entry(document["arca_spec"]))
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise rule("V01", f"{path} no es un registro válido de las huellas de las fuentes (se esperaba {{version, cord: {{archivo: {{sha256, size}}}}, arca_spec}})",
                   path=str(path), reason="lock")
    return document


def write_source_lock(path: Path, files: dict[str, tuple[str, int]]) -> None:
    """Register the hash and size of each source file; the PDF is under the key ``arca_spec``."""
    document = {
        "version": LOCK_VERSION,
        "cord": {name: {"sha256": digest, "size": size} for name, (digest, size) in files.items() if name != ARCA_KEY},
        "arca_spec": {"sha256": files[ARCA_KEY][0], "size": files[ARCA_KEY][1]},
    }
    write_atomic(Path(path), json.dumps(document, indent=2, sort_keys=True) + "\n")


# --- V01, V03 (the byte cap), V06 ---------------------------------------------------------------------------------------------


def check_sources(cfg: PipelineConfig) -> dict[str, str]:
    """V01 and V06 (and the byte cap of V03): every source file exists and is the accepted one or the registered one. Returns the SHA-256 of each CORD file
    (by its name) and of the PDF (under ``arca_spec``). Writes nothing and downloads nothing: a missing or different file is refused with the runbook R1.
    The existence, the size and the cap of every file are checked before any file is hashed."""
    source = cfg.source
    items = [(f.name, Path(source.cord_dir) / f.name, f.bytes, f.sha256, "V01") for f in source.cord_files]
    items.append((ARCA_KEY, Path(source.arca_spec_path), source.arca_spec_bytes, source.arca_spec_sha256, "V06"))
    registered = read_source_lock(lock_path(cfg))
    for key, path, accepted_bytes, accepted_sha, rule_id in items:
        if not path.is_file():
            raise rule(rule_id, f"no existe el archivo de la fuente {path}: hay que colocarlo ahí", path=str(path), file=path.name, reason="missing")
        size = path.stat().st_size
        if accepted_sha and size != accepted_bytes:
            raise rule(rule_id, f"el archivo {path} pesa {size} bytes y se aceptan {accepted_bytes}; no se cambió nada", path=str(path), file=path.name,
                       reason="size", expected=accepted_bytes, actual=size)
        entry = (registered["arca_spec"] if key == ARCA_KEY else registered["cord"].get(key)) if registered is not None else None
        if not accepted_sha and entry is not None and size != entry["size"]:
            raise rule(rule_id, f"el archivo {path} pesa {size} bytes y el tamaño registrado es {entry['size']}; no se cambió nada", path=str(path),
                       file=path.name, reason="size", expected=entry["size"], actual=size)
        if rule_id == "V01" and size > source.max_shard_bytes:     # the cap is held before any file is read or hashed
            raise rule("V03", f"el archivo {path} pesa {size} bytes, por encima del límite de {source.max_shard_bytes} ([source] max_shard_bytes)",
                       path=str(path), file=path.name, size=size, max_shard_bytes=source.max_shard_bytes)
    digests: dict[str, str] = {}
    for key, path, _accepted_bytes, accepted_sha, rule_id in items:
        digest = sha256_file(path)
        entry = (registered["arca_spec"] if key == ARCA_KEY else registered["cord"].get(key)) if registered is not None else None
        expected = accepted_sha or (entry["sha256"] if entry is not None else "")
        if expected and digest != expected:
            what = "aceptada" if accepted_sha else "registrada"
            raise rule(rule_id, f"el archivo {path} tiene la huella SHA-256 {digest} y la {what} es {expected}; no se cambió nada", path=str(path),
                       file=path.name, reason="sha256", expected=expected, actual=digest)
        digests[key] = digest
    return digests


# --- the first task ----------------------------------------------------------------------------------------------------------


def fetch_source(cfg: PipelineConfig) -> dict[str, Any]:
    """The first task: check the sources (V01, V06), their footers (V02, V03, V05), read train and validation (V02-V05) into the canonical index and write it
    (with its manifest last) when it is missing or is not the one these hashes give; register the quick-mode hashes after all of that. The test file's rows are
    never opened. Returns what the run records: the files, the rows of each split, whether the index was written and whether the registry was."""
    from invoiceflow import cord

    hashes = check_sources(cfg)
    rows = cord.check_footers(cfg)
    if cord.index_is_current(cfg, hashes):
        index_status = "unchanged"
    else:
        index = cord.build_index(cfg, hashes)
        cord.write_index(cfg, index, hashes)
        index_status = "written"
    pinned = all(f.sha256 for f in cfg.source.cord_files) and bool(cfg.source.arca_spec_sha256)
    lock = lock_path(cfg)
    if pinned:
        lock_status = "not needed"
    elif read_source_lock(lock) is None:
        write_source_lock(lock, {key: (digest, _size_of(cfg, key)) for key, digest in hashes.items()})
        lock_status = "registered"
    else:
        lock_status = "held"
    return {"files": len(cfg.source.cord_files) + 1, "rows": rows, "index": index_status, "lock": lock_status, "source_sha256": hashes}


def _size_of(cfg: PipelineConfig, key: str) -> int:
    path = Path(cfg.source.arca_spec_path) if key == ARCA_KEY else Path(cfg.source.cord_dir) / key
    return path.stat().st_size


__all__ = ["ARCA_KEY", "LOCK_NAME", "SourceError", "check_sources", "fetch_source", "lock_path", "read_source_lock", "rule", "write_source_lock"]
