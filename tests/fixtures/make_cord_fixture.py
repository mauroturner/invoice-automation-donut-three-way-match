"""A tiny CORD with the real schema (U03, plan section 24): three Parquet files of 16, 4 and 4 receipts, 96 x 128 px images drawn with Pillow and a
``gt_parse`` of the shape of CORD's, and the eleven malformed variants of AC02.

The real files (research/s00, ``data/raw/cord/``) have two columns, ``image`` as ``struct<bytes: binary, path: string>`` (``path`` is null) and
``ground_truth`` as a JSON string with ``gt_parse``, ``meta``, ``valid_line``, ``roi``, ``repeating_symbol`` and ``dontcare``; ``meta.split`` is
``train``, ``valid`` or ``test``. ``menu`` is a list in some receipts and a single object in others; two receipts of the real files have a total in a
list, 28 have none, and two images repeat within their partition: the fixture has each of those shapes. The ``test`` file is part of the fixture so
that the guard that keeps it closed has something to guard; it holds invented receipts, not CORD's.
"""

from __future__ import annotations

import functools
import io
import json
import os
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image, ImageDraw

CORD_SCHEMA = pa.schema([
    pa.field("image", pa.struct([pa.field("bytes", pa.binary()), pa.field("path", pa.string())])),
    pa.field("ground_truth", pa.string()),
])
FIXTURE_COUNTS = {"train": 16, "validation": 4, "test": 4}
FILE_NAMES = {split: f"{split}-00000-of-00001.parquet" for split in FIXTURE_COUNTS}   # the names config/pipeline.quick.toml lists
META_SPLIT = {"train": "train", "validation": "valid", "test": "test"}                 # what meta.split says in the real files
IMAGE_SIZE = (96, 128)                                                                  # width, height
MALFORMED_KINDS = (
    "extra_column", "missing_column", "wrong_type", "bad_json", "no_gt_parse", "too_deep", "pixel_bomb", "wrong_counts", "crossed_split",
    "oversize", "wrong_hash",
)
OVERSIZE_MIN_BYTES = 1_000_000   # a test lowers max_shard_bytes under this, so no test has to write a file of the real limit
PIXEL_BOMB_SIDE = 4100           # 4,100 x 4,100 = 16.81 million pixels, over max_image_pixels (16 million), in a PNG of a few kilobytes
DEEP = 10                        # levels of the ``too_deep`` receipt, over max_gt_depth (8)
ITEMS = [("NASI GORENG", 24000), ("ES TEH MANIS", 6000), ("AYAM BAKAR", 32000), ("KOPI SUSU", 15000), ("ROTI BAKAR", 18000), ("SATE AYAM", 28000)]


def money(amount: int) -> str:
    return f"{amount:,}"


def gt_parse(split: str, index: int) -> dict:
    """The annotation of receipt `index` of `split`: the shapes of the real files in a fixed pattern."""
    count = 1 + (index + len(split)) % 3
    chosen = [ITEMS[(index + step) % len(ITEMS)] for step in range(count)]
    lines = [{"nm": name, "cnt": "1", "price": money(price)} for name, price in chosen]
    total = sum(price for _, price in chosen)
    parse: dict = {"menu": lines if index % 2 == 0 and count > 1 else lines[0]}      # a list, or a single object
    if index % 3 == 0:
        tax = total // 11
        parse["sub_total"] = {"subtotal_price": money(total - tax), "tax_price": money(tax)}
    if split == "train" and index == 5:
        return parse                                                                  # a receipt with no total
    parse["total"] = {"total_price": money(total), "cashprice": money(total + 1000), "changeprice": money(1000)}
    if split == "train" and index == 3:
        parse["total"]["total_price"] = [money(total), money(total + 500)]            # a total in a list
    if split == "train" and index == 7:
        parse["void_menu"] = [{"nm": "VOID ES TEH", "cnt": "1", "price": money(6000)}]
    return parse


def draw(split: str, index: int) -> bytes:
    """The receipt's image as PNG bytes: white paper, the lines of the annotation in Pillow's default font. The last two of ``train`` are the
    same image (two of the real receipts repeat within their partition)."""
    shown = index - 1 if split == "train" and index == FIXTURE_COUNTS["train"] - 1 else index
    image = Image.new("RGB", IMAGE_SIZE, "white")
    pen = ImageDraw.Draw(image)
    pen.text((4, 3), f"R-{split[:2]}-{shown:02d}", fill="black")
    menu = gt_parse(split, shown)["menu"]
    for row, line in enumerate(menu if isinstance(menu, list) else [menu]):
        pen.text((4, 18 + row * 11), f"{line['nm'][:11]} {line['price']}", fill="black")
    pen.rectangle((4 + shown % 40, 100, 20 + shown % 40, 120), outline="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def ground_truth(split: str, index: int, *, meta_split: str | None = None) -> str:
    return json.dumps({
        "gt_parse": gt_parse(split, index),
        "meta": {"version": "2.0.0", "split": meta_split or META_SPLIT[split], "image_id": index,
                 "image_size": {"width": IMAGE_SIZE[0], "height": IMAGE_SIZE[1]}},
        "valid_line": [], "roi": {}, "repeating_symbol": [], "dontcare": [],
    })


def rows(split: str, count: int | None = None) -> list[dict]:
    return [{"image": {"bytes": draw(split, index), "path": None}, "ground_truth": ground_truth(split, index)}
            for index in range(FIXTURE_COUNTS[split] if count is None else count)]


def write_table(table: pa.Table, path: Path) -> None:
    pq.write_table(table, path, compression="snappy", row_group_size=1000)


@functools.cache
def _fixture_files() -> dict[str, bytes]:
    """The three files' bytes, made once per process: every test that needs the tiny CORD copies them instead of drawing the receipts again."""
    made = {}
    for split, name in FILE_NAMES.items():
        buffer = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(rows(split), schema=CORD_SCHEMA), buffer, compression="snappy", row_group_size=1000)
        made[split] = buffer.getvalue()
    return made


def make_cord_fixture(folder: Path) -> dict[str, Path]:
    """Write the three files into `folder` and return split -> path. The same call gives the same bytes on the same machine."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for split, name in FILE_NAMES.items():
        paths[split] = folder / name
        paths[split].write_bytes(_fixture_files()[split])
    return paths


@functools.cache
def noise_png(side: int) -> bytes:
    """A PNG that does not compress: random pixels."""
    buffer = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(buffer, format="PNG")
    return buffer.getvalue()


@functools.cache
def bomb_png() -> bytes:
    """A PNG whose header declares more pixels than the limit and whose data is a few kilobytes: one bit per pixel, all white."""
    buffer = io.BytesIO()
    Image.new("1", (PIXEL_BOMB_SIDE, PIXEL_BOMB_SIDE), 1).save(buffer, format="PNG")
    return buffer.getvalue()


def make_malformed(base: Path, kind: str, *, name: str | None = None) -> Path:
    """A copy of the fixture folder `base` with one defect of `kind` (``MALFORMED_KINDS``), next to it; returns the new folder. `base` is not touched.

    extra_column / missing_column / wrong_type: the train file has another column, no ``ground_truth``, or ``ground_truth`` as binary.
    bad_json: a receipt of train whose ``ground_truth`` is cut in the middle. no_gt_parse: one without ``gt_parse``. too_deep: one whose annotation is
    ``DEEP`` levels deep. pixel_bomb: one image whose header declares ``PIXEL_BOMB_SIDE`` squared pixels. wrong_counts: the validation file with
    one receipt more. crossed_split: a receipt of validation whose ``meta.split`` says ``test``. oversize: a train image that does not compress,
    so the file is over ``OVERSIZE_MIN_BYTES``. wrong_hash: one byte of the train file changed, so it is the same size and another hash.
    """
    if kind not in MALFORMED_KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {MALFORMED_KINDS}")
    copy = base.parent / (name or f"malformed-{kind}")
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(base, copy)
    train, validation = copy / FILE_NAMES["train"], copy / FILE_NAMES["validation"]
    table = pq.read_table(train)
    data = table.to_pylist()
    if kind == "extra_column":
        write_table(table.append_column("extra", pa.array(["x"] * table.num_rows)), train)
    elif kind == "missing_column":
        write_table(table.drop_columns(["ground_truth"]), train)
    elif kind == "wrong_type":
        as_binary = pa.array([row["ground_truth"].encode("utf-8") for row in data], type=pa.binary())
        write_table(pa.table({"image": table["image"], "ground_truth": as_binary}), train)
    elif kind in ("bad_json", "no_gt_parse", "too_deep", "pixel_bomb", "oversize"):
        row = data[1]
        if kind == "bad_json":
            row["ground_truth"] = row["ground_truth"][:40]
        elif kind == "no_gt_parse":
            row["ground_truth"] = json.dumps({"meta": json.loads(row["ground_truth"])["meta"]})
        elif kind == "too_deep":
            deep: dict = {"nm": "x"}
            for _ in range(DEEP):
                deep = {"menu": deep}
            parsed = json.loads(row["ground_truth"])
            parsed["gt_parse"] = deep
            row["ground_truth"] = json.dumps(parsed)
        elif kind == "pixel_bomb":
            row["image"] = {"bytes": bomb_png(), "path": None}
        else:
            row["image"] = {"bytes": noise_png(700), "path": None}
        write_table(pa.Table.from_pylist(data, schema=CORD_SCHEMA), train)
    elif kind == "wrong_counts":
        write_table(pa.Table.from_pylist(rows("validation", FIXTURE_COUNTS["validation"] + 1), schema=CORD_SCHEMA), validation)
    elif kind == "crossed_split":
        crossed = pq.read_table(validation).to_pylist()
        crossed[0]["ground_truth"] = ground_truth("validation", 0, meta_split="test")
        write_table(pa.Table.from_pylist(crossed, schema=CORD_SCHEMA), validation)
    else:   # wrong_hash
        content = bytearray(train.read_bytes())
        middle = len(content) // 2
        content[middle] ^= 0xFF
        train.write_bytes(bytes(content))
    return copy


def file_facts(folder: Path) -> dict[str, tuple[int, str]]:
    """File name -> (bytes, sha256) of every file of a fixture folder, for the configurations that accept a source by its size and hash."""
    import hashlib

    return {path.name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(folder.glob("*.parquet"))}
