"""Everything the project publishes, read byte by byte (U18; AC28, RNF12; plan, sections 21 and 24.2; D05): every CUIT in it is in the synthetic range, no file carries a path of this machine,
and no image of CORD is among them. The published files are the Parquet files of the star model, the five JSON files of the site, the HTML design and the figures."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from invoiceflow.dashboard.semantic_model import MODEL
from invoiceflow.synth.cuit import is_synthetic_cuit, non_synthetic_cuits

HASH = __import__("re").compile(r"[0-9a-f]{32,}")
LOCAL = ("C:\\", "C:/", "/Users/", "\\Users\\", "mauro", "AppData")


def published(star_export, site_files, rendered_html, figures_written) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for folder in (star_export.out, site_files.out, figures_written.out):
        files.update({f"{folder.name}/{p.name}": p.read_bytes() for p in sorted(Path(folder).iterdir()) if p.is_file()})
    files["powerbi-design.html"] = rendered_html.path.read_bytes()
    return files


def strings_of(name: str, data: bytes, path: Path | None = None) -> list[str]:
    """The text a published file holds: its decoded text, or every string cell and every metadata value of a Parquet file. A SHA-256 is not read as numbers: eleven digits inside one are not a CUIT."""
    return [HASH.sub(" ", text) for text in _strings_of(name, data, path)]


def _strings_of(name: str, data: bytes, path: Path | None) -> list[str]:
    if name.endswith(".parquet"):
        table = pq.read_table(path)
        out = [v.decode("utf-8", "replace") for v in (table.schema.metadata or {}).values()]
        for column in table.columns:
            out += [str(v) for v in column.to_pylist() if isinstance(v, str)]
        return out
    return [data.decode("utf-8", "replace")] if not name.endswith(".png") else []


def test_every_cuit_in_published_bytes_is_synthetic(star_export, site_files, rendered_html, figures_written):
    files = published(star_export, site_files, rendered_html, figures_written)
    assert len(files) >= len(MODEL.tables) + 6 + 7 + 1
    seen = set()
    for name, data in files.items():
        folder = star_export.out if name.startswith(star_export.out.name + "/") else None
        texts = strings_of(name, data, star_export.out / name.split("/")[1] if folder else None)
        for text in texts:
            assert non_synthetic_cuits(text) == [], name
        seen |= {c for text in texts for c in __import__("re").findall(r"(?<!\d)\d{11}(?!\d)", text)}
    suppliers = star_export.out / "dim_supplier.parquet"
    cuits = [c for c in pq.read_table(suppliers).column("cuit").to_pylist() if c is not None]
    assert cuits and all(is_synthetic_cuit(c) for c in cuits) and set(cuits) <= seen
    # the guard sees a real-looking CUIT in each kind of file
    assert non_synthetic_cuits("proveedor 20-12345678-6 y 30-00000000-7") == ["20123456786"] and non_synthetic_cuits("20123456786") == ["20123456786"]


def test_published_files_carry_no_local_path(star_export, site_files, rendered_html, figures_written):
    files = published(star_export, site_files, rendered_html, figures_written)
    root = str(star_export.folder).encode()
    for name, data in files.items():
        assert root not in data and root.replace(b"\\", b"/") not in data, name
        for needle in LOCAL:
            assert needle.encode() not in data, (name, needle)
    for name, data in files.items():
        if name.endswith(".json") and name != "manifest.json":
            assert not any(str(part).startswith(("/", "C:")) for part in _strings(json.loads(data.decode("utf-8")))), name


def _strings(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


def test_no_image_of_cord_is_published(star_export, site_files, rendered_html, figures_written):
    files = published(star_export, site_files, rendered_html, figures_written)
    for name, data in files.items():
        if name.endswith(".parquet"):
            assert not any(str(f.type) in ("binary", "large_binary") for f in pq.read_schema(star_export.out / name.split("/")[1])), name
        if name.endswith((".json", ".html")):
            text = data.decode("utf-8")
            assert "data:image" not in text and "base64," not in text and "<img" not in text.lower(), name
        if name.endswith(".png"):
            assert data.startswith(b"\x89PNG") and len(data) < 200_000, "a figure is a chart, not a receipt"
    assert sorted(n for n in files if n.endswith(".png")) == sorted(f"{figures_written.out.name}/{p.name}" for p in figures_written.paths)
