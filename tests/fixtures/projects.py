"""A project folder in ``tmp_path`` (U04): the configuration files, the tiny CORD and a stand-in for ARCA's PDF, with a configuration that reads them.

Two modes, as the plan has them: the quick mode (the sources under ``data/quick/``, their hashes empty and registered at the first view) and the accepted
mode (the sources under ``data/raw/``, each with the size and SHA-256 that this module computed from the files it wrote), so a test can break one file
and name the rule that refuses it. ``tmp_path`` is outside the working tree.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from invoiceflow.config import PipelineConfig, load_config
from tests.fixtures.make_cord_fixture import FILE_NAMES, file_facts, make_cord_fixture

PROJECT = Path(__file__).resolve().parents[2]
PDF_BYTES = b"%PDF-1.4\n% a stand-in for ARCA's QR specification: only its size and hash matter until the QR is read (U07)\n"
PDF_NAME = "QRespecificaciones.pdf"


@dataclass(frozen=True)
class ProjectFolder:

    cfg: PipelineConfig
    root: Path
    cord_dir: Path
    pdf: Path

    def snapshot(self, folder: str = "data") -> dict[str, bytes]:
        """Every file under `folder` (relative path -> content), to compare before and after a call that must write nothing."""
        base = self.root / folder
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in sorted(base.rglob("*")) if path.is_file()} if base.exists() else {}


def make_project(root: Path, *, accepted: bool = False, pdf_quick: bool = False, source_extra: str = "", extra: str = "") -> ProjectFolder:
    """Write a project into `root`. `source_extra` is lines for the ``[source]`` table, `extra` the text of any other tables. With `accepted`, the CORD files
    are pinned by size and hash; with `pdf_quick` as well, the PDF is not (it sits under data/quick/ with an empty hash: a mixed project)."""
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    for name in ("pipeline.toml", "pipeline.quick.toml"):
        shutil.copy(PROJECT / "config" / name, config / name)
    area = root / "data" / ("raw" if accepted else "quick")
    cord_dir = area / "cord"
    make_cord_fixture(cord_dir)
    pdf = (root / "data" / "quick" if pdf_quick else area) / "arca" / PDF_NAME
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(PDF_BYTES)
    lines = ['extends = "pipeline.quick.toml"', "", "[source]", source_extra]
    if accepted:
        import hashlib

        lines += ['cord_dir = "data/raw/cord"']
        if not pdf_quick:
            lines += [f'arca_spec_path = "data/raw/arca/{PDF_NAME}"', f"arca_spec_bytes = {len(PDF_BYTES)}", f'arca_spec_sha256 = "{hashlib.sha256(PDF_BYTES).hexdigest()}"']
        lines.append("")
        for split, name in FILE_NAMES.items():
            size, digest = file_facts(cord_dir)[name]
            lines += ["[[source.cord_files]]", f'split = "{split}"', f'name = "{name}"', f"bytes = {size}", f'sha256 = "{digest}"', ""]
        lines += ["[model]", 'kind = "donut"', ""]
    lines.append(extra)
    (config / "test.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ProjectFolder(cfg=load_config(config / "test.toml"), root=root, cord_dir=cord_dir, pdf=pdf)
