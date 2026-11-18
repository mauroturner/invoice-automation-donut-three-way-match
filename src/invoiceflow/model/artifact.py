"""The artifact of the model: what the flow reads, and only after it has checked it (AC19; plan section 9.4; decision D13; runbook R6).

A folder with ``model.safetensors``, ``config.json``, ``generation_config.json``, the tokenizer's and the processor's files and one more, ``artifact.json``: the **manifest**, with the
size and the SHA-256 of every other file. The SHA-256 of the manifest is the ``artifact_sha256`` the model registry holds, so one hash in the registry covers every file; the hash of
``tokenizer.json`` is registered alone as ``tokenizer_sha256``.

``verify_artifact(folder, expected_sha256)`` is what stands between a file and the code that would read it, in this order, and stops at the first failure with an ``ArtifactError``:

1. the registered hash is 64 lowercase hexadecimal characters;
2. the folder holds no pickle format (``.bin``, ``.pt``, ``.pth``, ``.pkl``, ``.ckpt``, ``.pickle``) and no sub-folder, wherever the manifest says (a listed pickle is still refused);
3. the manifest is there, its SHA-256 is the registered one, and it is exactly the shape ``{"files": {name: {"bytes": n, "sha256": h}}}``;
4. the files of the folder are the manifest's files and the manifest, no more, no less;
5. each file has the size and the SHA-256 the manifest says (the size first: a file of another size is not hashed).

Only then does ``load_artifact`` read the weights, with ``use_safetensors=True`` and ``local_files_only=True``, and never ``trust_remote_code``. The hashes protect against a file that changed;
they do not protect against whoever changes the files and the registry together (D13).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from invoiceflow.files import sha256_file, write_atomic

FORBIDDEN_SUFFIXES = frozenset({".bin", ".pt", ".pth", ".pkl", ".ckpt", ".pickle"})
MANIFEST = "artifact.json"
REQUIRED = ("model.safetensors", "config.json", "tokenizer.json")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class ArtifactError(RuntimeError):
    """The artifact is not the one that was registered, or it holds something the flow never reads (runbook R6)."""


@dataclass(frozen=True)
class ArtifactRecord:
    folder: Path
    files: dict[str, dict[str, Any]]      # name -> {"bytes", "sha256"}
    sha256: str                           # the manifest's: the registry's ``artifact_sha256``
    tokenizer_sha256: str


def _manifest_text(files: dict[str, dict[str, Any]]) -> str:
    return json.dumps({"files": {name: {"bytes": entry["bytes"], "sha256": entry["sha256"]} for name, entry in sorted(files.items())}}, sort_keys=True, indent=2) + "\n"


def _refuse_pickles_and_folders(folder: Path) -> list[Path]:
    """The files directly in `folder`; a pickle format or a sub-folder anywhere is an ``ArtifactError`` naming it."""
    entries = sorted(folder.iterdir(), key=lambda path: path.name)
    for entry in entries:
        if entry.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ArtifactError(f"{entry.name}: un archivo de formato pickle ({entry.suffix}) no se lee nunca; el artefacto solo tiene safetensors (D13, R6)")
        if entry.is_dir() or entry.is_symlink():
            raise ArtifactError(f"{entry.name}: una carpeta o un enlace dentro del artefacto; solo hay archivos (R6)")
    return entries


def seal_folder(folder: Path) -> ArtifactRecord:
    """Write the manifest of a folder that holds a saved model (what ``fine_tune`` leaves for each candidate): it is refused if it has a manifest already, a pickle format, a
    sub-folder or lacks ``model.safetensors``, ``config.json`` or ``tokenizer.json``. The manifest is written last, atomically, so a folder with a manifest is a complete one."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ArtifactError(f"{folder.name} no es una carpeta")
    entries = _refuse_pickles_and_folders(folder)
    if any(entry.name == MANIFEST for entry in entries):
        raise ArtifactError(f"{folder.name} ya tiene {MANIFEST}: un artefacto sellado no se vuelve a sellar")
    names = {entry.name for entry in entries}
    missing = [name for name in REQUIRED if name not in names]
    if missing:
        raise ArtifactError(f"{folder.name} no tiene {', '.join(missing)}")
    files = {entry.name: {"bytes": entry.stat().st_size, "sha256": sha256_file(entry)} for entry in entries}
    text = _manifest_text(files)
    write_atomic(folder / MANIFEST, text)
    return ArtifactRecord(folder, files, sha256_file(folder / MANIFEST), files["tokenizer.json"]["sha256"])


def save_artifact(model: Any, processor: Any, folder: Path) -> ArtifactRecord:
    """Save `model` (``safetensors``) and `processor` into the new folder `folder` and seal it. The folder appears whole or not at all; one that exists is never written over."""
    folder = Path(folder)
    if folder.exists():
        raise ArtifactError(f"{folder.name} ya existe: un artefacto registrado no se sobrescribe")
    folder.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(dir=folder.parent, prefix=folder.name + ".", suffix=".partial"))
    try:
        model.save_pretrained(scratch, safe_serialization=True)
        processor.save_pretrained(scratch)
        record = seal_folder(scratch)
        os.replace(scratch, folder)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return replace(record, folder=folder)


def _manifest_files(text: str) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ArtifactError(f"{MANIFEST} no es JSON") from error
    if not (isinstance(data, dict) and set(data) == {"files"} and isinstance(data["files"], dict)):
        raise ArtifactError(f"{MANIFEST} no tiene la forma {{files: {{nombre: {{bytes, sha256}}}}}}")
    files = data["files"]
    for name, entry in files.items():
        plain = isinstance(name, str) and name and name == Path(name).name and name not in (".", "..", MANIFEST) and "\\" not in name
        if not (plain and isinstance(entry, dict) and set(entry) == {"bytes", "sha256"} and isinstance(entry["bytes"], int) and not isinstance(entry["bytes"], bool)
                and entry["bytes"] >= 0 and isinstance(entry["sha256"], str) and _HEX64.fullmatch(entry["sha256"])):
            raise ArtifactError(f"{MANIFEST}: la entrada {name!r} no es un archivo con su tamaño y su SHA-256")
    if "tokenizer.json" not in files or "model.safetensors" not in files:
        raise ArtifactError(f"{MANIFEST} no lista model.safetensors y tokenizer.json")
    return files


def verify_artifact(folder: Path, expected_sha256: Any) -> ArtifactRecord:
    """Check `folder` against the registered hash (see the module's text); nothing is read as a model. Returns the record, or raises ``ArtifactError`` naming the first thing wrong."""
    folder = Path(folder)
    if not (isinstance(expected_sha256, str) and _HEX64.fullmatch(expected_sha256)):
        raise ArtifactError(f"la huella registrada del artefacto no son 64 caracteres hexadecimales en minúscula: {expected_sha256!r}")
    if not folder.is_dir():
        raise ArtifactError(f"{folder.name}: no hay una carpeta de artefacto (R6)")
    entries = _refuse_pickles_and_folders(folder)
    manifest_path = folder / MANIFEST
    if not manifest_path.is_file():
        raise ArtifactError(f"{folder.name} no tiene {MANIFEST} (R6)")
    manifest_sha = sha256_file(manifest_path)
    if manifest_sha != expected_sha256:
        raise ArtifactError(f"la huella registrada ({expected_sha256[:12]}…) no es la del manifiesto de {folder.name} ({manifest_sha[:12]}…): otro artefacto (R6)")
    files = _manifest_files(manifest_path.read_text(encoding="utf-8"))
    present = {entry.name for entry in entries} - {MANIFEST}
    for name in sorted(present - set(files)):
        raise ArtifactError(f"{name}: un archivo que el manifiesto no lista (R6)")
    for name in sorted(set(files) - present):
        raise ArtifactError(f"{name}: lo lista el manifiesto y no está (R6)")
    for name, entry in sorted(files.items()):
        path = folder / name
        if path.stat().st_size != entry["bytes"]:
            raise ArtifactError(f"{name}: tiene {path.stat().st_size} bytes y el manifiesto dice {entry['bytes']} (R6)")
        if sha256_file(path) != entry["sha256"]:
            raise ArtifactError(f"{name}: su SHA-256 no es el del manifiesto (R6)")
    return ArtifactRecord(folder, files, expected_sha256, files["tokenizer.json"]["sha256"])


def _read_model(folder: Path) -> tuple[Any, Any]:
    """The one place the weights are read: ``(processor, model)`` from a folder that ``verify_artifact`` accepted, from ``safetensors`` and local files only."""
    from transformers import DonutProcessor, VisionEncoderDecoderModel

    model = VisionEncoderDecoderModel.from_pretrained(folder, use_safetensors=True, local_files_only=True, trust_remote_code=False)
    processor = DonutProcessor.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    return processor, model


def load_artifact(folder: Path, expected_sha256: Any) -> tuple[Any, Any]:
    """``(processor, model)`` of the artifact in `folder`, read only after ``verify_artifact`` accepted it; the model is in evaluation mode."""
    verify_artifact(folder, expected_sha256)
    return _read_model(Path(folder))
