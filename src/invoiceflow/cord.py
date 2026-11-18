"""The strict reader of CORD and its canonical index (RF02, RF03; V02-V05; decisions D03, D10).

The six Parquet files are untrusted data: they are read with pyarrow only, after ``sources.check_sources`` accepted their size and hash, and nothing is ever
deserialized from them but JSON text and image bytes (the image is read for its header, not decoded).

* ``check_footers`` reads only the footers, of all six files, the test file's included: V02 (the exact schema), V03 (the rows and row groups of each file
  under their caps) and V05 (the rows of each split are the expected ones, ``[source] expected_rows``). No row is opened.
* ``read_split`` yields the receipts of one split, each checked when it is reached: V03 (the image's header is read before anything is decoded, and its
  pixels are under ``max_image_pixels``), V04 (``ground_truth`` is JSON with ``gt_parse`` and ``meta``, no deeper than ``max_gt_depth``) and V05 (``meta.split``
  is the split's). ``menu`` becomes a list when it is one object; a total that is a list is kept as it is and counted. The test split opens only in the
  closing evaluation: ``read_split(cfg, "test", closing_run=True)`` called from ``evaluation.evaluate_cord``, else ``LeakageError`` at the call (D10, AC23).
* ``build_index`` and ``write_index`` turn train and validation into the canonical index (the hash and the size of every image, its canonical target and what was
  found in it, the repeated images marked) and write it, with its manifest last; ``index_is_current`` says whether the one on disk is the one these hashes give.
"""

from __future__ import annotations

import io
import json
import warnings
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image, UnidentifiedImageError

from invoiceflow.config import PipelineConfig
from invoiceflow.files import write_atomic
from invoiceflow.leakage import LeakageError, called_by, caller
from invoiceflow.sources import SourceError, check_sources, rule

SPLITS = ("train", "validation", "test")
INDEXED_SPLITS = ("train", "validation")   # the test split is never indexed (AC03)
META_SPLIT = {"train": "train", "validation": "valid", "test": "test"}   # what meta.split says in the real files
CORD_SCHEMA = pa.schema([
    pa.field("image", pa.struct([pa.field("bytes", pa.binary()), pa.field("path", pa.string())])),
    pa.field("ground_truth", pa.string()),
])
MAX_ROW_GROUPS = 10_000      # a footer that names more row groups than this is not one of the six files (V03)
BATCH_ROWS = 8               # receipts held in memory at a time: the images of the real files weigh a megabyte or two each
INDEX_VERSION = 1
INDEX_FOLDER = "cord"
MANIFEST = "manifest.json"
EVALUATION_CALLER = ("invoiceflow.evaluation", "evaluate_cord")


@dataclass(frozen=True)
class CordReceipt:
    receipt_key: str          # "<split>-<index>", the index counted over the split's files in the order of the configuration
    split: str
    index: int
    file: str
    image_bytes: bytes
    image_sha256: str
    width: int
    height: int
    gt_parse: dict[str, Any]  # canonical: menu is a list
    menu_was_object: bool
    total_in_list: bool


@dataclass(frozen=True)
class IndexEntry:
    receipt_key: str
    image_sha256: str
    width: int
    height: int
    target: dict[str, Any]
    menu_was_object: bool
    total_in_list: bool
    duplicate_of: str | None  # the key of the first receipt of the split with the same image, or None


@dataclass(frozen=True)
class CordIndex:
    entries: dict[str, list[IndexEntry]]   # split -> entries, train and validation only
    source_sha256: dict[str, str]          # CORD file name -> SHA-256 of the files it was built from
    cord_commit: str


class CordIndexError(RuntimeError):
    """The index on disk is not the one its manifest describes."""


# --- V02, V03, V05: the footers --------------------------------------------------------------------------------------------


def _schema_problem(file_schema: pa.Schema) -> str | None:
    """What differs between the file's schema and ``CORD_SCHEMA`` (names, order, types), or None."""
    have, want = file_schema.names, CORD_SCHEMA.names
    extra, missing = [n for n in have if n not in want], [n for n in want if n not in have]
    if extra or missing:
        return f"las columnas no son las {len(want)} de CORD: de más {extra or 'ninguna'}, faltan {missing or 'ninguna'}"
    if have != want:
        return f"las columnas están en otro orden que el de CORD: {have}"
    for name in want:
        if file_schema.field(name).type != CORD_SCHEMA.field(name).type:
            return f"la columna {name} es {file_schema.field(name).type} y se esperaba {CORD_SCHEMA.field(name).type}"
    return None


def _open_parquet(path: Path) -> pq.ParquetFile:
    """The footer of a Parquet file, or V02 when it is not one."""
    try:
        return pq.ParquetFile(path)
    except (pa.ArrowException, OSError, ValueError) as error:
        raise rule("V02", f"el archivo {path} no es un Parquet legible ({type(error).__name__})", path=str(path), file=Path(path).name) from None


def check_footers(cfg: PipelineConfig) -> dict[str, int]:
    """V02, V03 and V05 from the footers of all the files: the exact schema, the rows and row groups of each file under their caps and the rows of each
    split equal to ``[source] expected_rows``. Returns the rows of each split. No row is opened, the test file's included."""
    source = cfg.source
    totals = dict.fromkeys(SPLITS, 0)
    for entry in source.cord_files:
        path = Path(source.cord_dir) / entry.name
        footer = _open_parquet(path)
        problem = _schema_problem(footer.schema_arrow)
        if problem is not None:
            raise rule("V02", f"el archivo {path}: {problem}", path=str(path), file=entry.name)
        if footer.metadata.num_rows > source.max_rows:
            raise rule("V03", f"el archivo {path} tiene {footer.metadata.num_rows} filas, por encima del límite de {source.max_rows} ([source] max_rows)",
                       path=str(path), file=entry.name, rows=footer.metadata.num_rows, max_rows=source.max_rows)
        if footer.metadata.num_row_groups > MAX_ROW_GROUPS:
            raise rule("V03", f"el archivo {path} tiene {footer.metadata.num_row_groups} grupos de filas, por encima del límite de {MAX_ROW_GROUPS}", path=str(path),
                       file=entry.name, row_groups=footer.metadata.num_row_groups)
        totals[entry.split] += footer.metadata.num_rows
    for split in SPLITS:
        expected = getattr(source.expected_rows, split)
        if totals[split] != expected:
            raise rule("V05", f"la partición {split} tiene {totals[split]} recibos y se esperaban {expected} ([source] expected_rows)", split=split,
                       rows=totals[split], expected=expected)
    return totals


# --- the rows -----------------------------------------------------------------------------------------------------------------


def _iter_rows(path: Path, batch_rows: int = BATCH_ROWS) -> Iterator[dict[str, Any]]:
    """The rows of one file as dicts (``image`` and ``ground_truth``), in batches. Every open of a file's rows goes through here."""
    footer = _open_parquet(Path(path))
    for batch in footer.iter_batches(batch_size=batch_rows, columns=CORD_SCHEMA.names):
        yield from batch.to_pylist()


def _image_size(data: bytes, key: str, file: str, cfg: PipelineConfig) -> tuple[int, int]:
    """V03: the image's size read from its header, never decoded, under ``max_image_pixels``."""
    limit = cfg.source.max_image_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise rule("V03", f"la imagen del recibo {key} declara más píxeles de los que Pillow acepta (el límite es {limit}, [source] max_image_pixels)",
                   receipt_key=key, file=file, max_image_pixels=limit) from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise rule("V03", f"la imagen del recibo {key} no es una imagen legible", receipt_key=key, file=file) from None
    if width * height > limit:
        raise rule("V03", f"la imagen del recibo {key} declara {width} x {height} = {width * height} píxeles, por encima del límite de {limit} "
                          f"([source] max_image_pixels)", receipt_key=key, file=file, pixels=width * height, max_image_pixels=limit)
    return width, height


def _depth(value: Any) -> int:
    """How deeply the containers (objects and lists) of a parsed JSON value nest, counted without recursion."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        node, level = stack.pop()
        if isinstance(node, dict):
            deepest = max(deepest, level)
            stack.extend((child, level + 1) for child in node.values())
        elif isinstance(node, list):
            deepest = max(deepest, level)
            stack.extend((child, level + 1) for child in node)
    return deepest


def _annotation(text: Any, split: str, key: str, file: str, cfg: PipelineConfig) -> dict[str, Any]:
    """V04 and V05 on one ``ground_truth``: JSON with a ``gt_parse`` object and a ``meta`` object, no deeper than ``max_gt_depth``, ``meta.split`` the split's."""
    if not isinstance(text, str):
        raise rule("V04", f"el recibo {key} no tiene texto de anotación (ground_truth)", receipt_key=key, file=file)
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        raise rule("V04", f"la anotación del recibo {key} no es un JSON legible", receipt_key=key, file=file) from None
    if not isinstance(document, dict) or not isinstance(document.get("gt_parse"), dict) or not isinstance(document.get("meta"), dict):
        raise rule("V04", f"la anotación del recibo {key} no tiene un objeto gt_parse y un objeto meta", receipt_key=key, file=file)
    depth = _depth(document)
    if depth > cfg.source.max_gt_depth:
        raise rule("V04", f"la anotación del recibo {key} anida {depth} niveles, por encima del límite de {cfg.source.max_gt_depth} ([source] max_gt_depth)",
                   receipt_key=key, file=file, depth=depth, max_gt_depth=cfg.source.max_gt_depth)
    if document["meta"].get("split") != META_SPLIT[split]:
        raise rule("V05", f"el recibo {key} del archivo {file} dice meta.split = {document['meta'].get('split')!r} y la partición {split} lleva "
                          f"{META_SPLIT[split]!r}", split=split, receipt_key=key, file=file)
    return document["gt_parse"]


def _canonical(parse: dict[str, Any], key: str, file: str) -> tuple[dict[str, Any], bool, bool]:
    """``menu`` as a list (RF03); a total in a list is kept and counted. Returns the annotation, whether ``menu`` was one object and whether the total was a list."""
    canonical = dict(parse)
    menu_was_object = False
    if "menu" in canonical:
        menu = canonical["menu"]
        if isinstance(menu, dict):
            canonical["menu"], menu_was_object = [menu], True
        elif not isinstance(menu, list):
            raise rule("V04", f"el menu del recibo {key} no es una lista ni un objeto", receipt_key=key, file=file)
    total = canonical.get("total")
    return canonical, menu_was_object, isinstance(total, dict) and isinstance(total.get("total_price"), list)


def _read(cfg: PipelineConfig, split: str) -> Iterator[CordReceipt]:
    index = 0
    for entry in (e for e in cfg.source.cord_files if e.split == split):
        path = Path(cfg.source.cord_dir) / entry.name
        for raw in _iter_rows(path):
            key = f"{split}-{index}"
            image = raw.get("image")
            data = image.get("bytes") if isinstance(image, dict) else None
            if not isinstance(data, (bytes, bytearray)) or not data:
                raise rule("V03", f"el recibo {key} no trae los bytes de su imagen", receipt_key=key, file=entry.name)
            width, height = _image_size(bytes(data), key, entry.name, cfg)
            parse, menu_was_object, total_in_list = _canonical(_annotation(raw.get("ground_truth"), split, key, entry.name, cfg), key, entry.name)
            yield CordReceipt(key, split, index, entry.name, bytes(data), sha256_hex(bytes(data)), width, height, parse, menu_was_object, total_in_list)
            index += 1
    expected = getattr(cfg.source.expected_rows, split)
    if index != expected:
        raise rule("V05", f"se leyeron {index} recibos de la partición {split} y se esperaban {expected} ([source] expected_rows)", split=split, rows=index,
                   expected=expected)


def sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def read_split(cfg: PipelineConfig, split: str, *, closing_run: bool = False) -> Iterator[CordReceipt]:
    """The receipts of `split`, each checked when it is reached. ``"test"`` opens only from ``evaluation.evaluate_cord`` of the closing run
    (``closing_run=True``): anything else is ``LeakageError``, raised here at the call, before a row is opened (D10, AC23)."""
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}; expected one of {', '.join(SPLITS)}")
    if split == "test" and not (closing_run and called_by({EVALUATION_CALLER}, {__name__})):
        module, function = caller({__name__})
        raise LeakageError(
            f"la partición test de CORD solo se abre en {EVALUATION_CALLER[1]} del cierre (D10, AC23): la pidió {module or '?'}.{function or '?'} "
            f"con closing_run={closing_run}"
        )
    return _read(cfg, split)


# --- the canonical index ------------------------------------------------------------------------------------------------------


def index_dir(cfg: PipelineConfig) -> Path:
    return Path(cfg.paths.interim_dir) / INDEX_FOLDER


def build_index(cfg: PipelineConfig, hashes: dict[str, str] | None = None) -> CordIndex:
    """Read train and validation (V02-V05) into the canonical index, in memory. `hashes` are the ones ``check_sources`` returned; without them it checks the
    sources first. The test split is not read."""
    hashes = check_sources(cfg) if hashes is None else hashes
    entries: dict[str, list[IndexEntry]] = {}
    for split in INDEXED_SPLITS:
        first_of: dict[str, str] = {}
        entries[split] = []
        for receipt in read_split(cfg, split):
            duplicate_of = first_of.setdefault(receipt.image_sha256, receipt.receipt_key)
            entries[split].append(IndexEntry(receipt.receipt_key, receipt.image_sha256, receipt.width, receipt.height, receipt.gt_parse, receipt.menu_was_object,
                                             receipt.total_in_list, None if duplicate_of == receipt.receipt_key else duplicate_of))
    source = {e.name: hashes[e.name] for e in cfg.source.cord_files}
    return CordIndex(entries=entries, source_sha256=source, cord_commit=cfg.source.cord_commit)


def _index_file(split: str) -> str:
    return f"index-{split}.jsonl"


def _index_bytes(entries: list[IndexEntry]) -> bytes:
    return "".join(json.dumps(asdict(entry), sort_keys=True) + "\n" for entry in entries).encode("utf-8")


def write_index(cfg: PipelineConfig, index: CordIndex, hashes: dict[str, str] | None = None) -> list[Path]:
    """Write the index files and, last, their manifest (RNF06): the manifest names the hashes of the sources the index was built from and the hash, size and
    rows of each index file, so a reader that finds a manifest finds an index that is whole."""
    del hashes   # the sources' hashes are in the index
    folder = index_dir(cfg)
    written, facts = [], {}
    for split in INDEXED_SPLITS:
        content = _index_bytes(index.entries[split])
        written.append(write_atomic(folder / _index_file(split), content))
        facts[_index_file(split)] = {"sha256": sha256_hex(content), "bytes": len(content), "rows": len(index.entries[split])}
    manifest = {
        "version": INDEX_VERSION, "cord_commit": index.cord_commit, "source": index.source_sha256, "files": facts,
        "splits": {split: len(index.entries[split]) for split in INDEXED_SPLITS},
        "repeated_images": {split: sum(e.duplicate_of is not None for e in index.entries[split]) for split in INDEXED_SPLITS},
        "menu_objects": {split: sum(e.menu_was_object for e in index.entries[split]) for split in INDEXED_SPLITS},
        "totals_in_a_list": {split: sum(e.total_in_list for e in index.entries[split]) for split in INDEXED_SPLITS},
    }
    written.append(write_atomic(folder / MANIFEST, json.dumps(manifest, indent=2, sort_keys=True) + "\n"))
    return written


def _manifest(cfg: PipelineConfig) -> dict[str, Any] | None:
    path = index_dir(cfg) / MANIFEST
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = (isinstance(document, dict) and document.get("version") == INDEX_VERSION and isinstance(document.get("files"), dict)
          and set(document.get("files", {})) == {_index_file(split) for split in INDEXED_SPLITS} and isinstance(document.get("source"), dict))
    return document if ok else None


def _files_match(cfg: PipelineConfig, manifest: dict[str, Any]) -> bool:
    folder = index_dir(cfg)
    for name, facts in manifest["files"].items():
        try:
            content = (folder / name).read_bytes()
        except OSError:
            return False
        if not (isinstance(facts, dict) and facts.get("bytes") == len(content) and facts.get("sha256") == sha256_hex(content)):
            return False
    return True


def index_is_current(cfg: PipelineConfig, hashes: dict[str, str]) -> bool:
    """Whether the index on disk is whole (its files are the ones its manifest names) and was built from these sources and this commit."""
    manifest = _manifest(cfg)
    if manifest is None or manifest.get("cord_commit") != cfg.source.cord_commit:
        return False
    return manifest["source"] == {e.name: hashes[e.name] for e in cfg.source.cord_files} and _files_match(cfg, manifest)


def read_index(cfg: PipelineConfig) -> CordIndex:
    """The index on disk, after checking that its files are the ones its manifest names."""
    manifest = _manifest(cfg)
    if manifest is None or not _files_match(cfg, manifest):
        raise CordIndexError(f"el índice de CORD en {index_dir(cfg)} no está entero: falta el manifiesto o un archivo no es el que nombra (corra fetch_source)")
    entries = {}
    for split in INDEXED_SPLITS:
        lines = (index_dir(cfg) / _index_file(split)).read_text(encoding="utf-8").splitlines()
        entries[split] = [IndexEntry(**json.loads(line)) for line in lines]
    return CordIndex(entries=entries, source_sha256=manifest["source"], cord_commit=manifest["cord_commit"])


__all__ = [
    "CORD_SCHEMA", "CordIndex", "CordIndexError", "CordReceipt", "IndexEntry", "INDEXED_SPLITS", "META_SPLIT", "SPLITS", "SourceError", "build_index",
    "check_footers", "index_dir", "index_is_current", "read_index", "read_split", "write_index",
]
