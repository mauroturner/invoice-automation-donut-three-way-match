"""The tiny CORD (U03, AC02 in part): the real schema, both forms of ``menu``, the receipts with a total in a list and with no total, the repeated
image, determinism, and each of the eleven malformed variants carrying exactly the defect it names."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from PIL import Image

from tests.fixtures.make_cord_fixture import (
    CORD_SCHEMA,
    DEEP,
    FILE_NAMES,
    FIXTURE_COUNTS,
    IMAGE_SIZE,
    MALFORMED_KINDS,
    META_SPLIT,
    OVERSIZE_MIN_BYTES,
    PIXEL_BOMB_SIDE,
    file_facts,
    make_cord_fixture,
    make_malformed,
)

# What `pq.read_schema` of every real shard prints (data/raw/cord/*.parquet, read from the footers of train and validation only)
REAL_SCHEMA_TEXT = "image: struct<bytes: binary, path: string>\n  child 0, bytes: binary\n  child 1, path: string\nground_truth: string"


def depth(value, level: int = 1) -> int:
    if isinstance(value, dict):
        return max([depth(child, level + 1) for child in value.values()] + [level])
    if isinstance(value, list):
        return max([depth(child, level + 1) for child in value] + [level])
    return level


def read(folder: Path, split: str) -> list[dict]:
    return pq.read_table(folder / FILE_NAMES[split]).to_pylist()


def test_cord_fixture_has_the_real_schema_and_both_menu_forms(cord_fixture):
    assert set(cord_fixture) == {"train", "validation", "test"}
    assert IMAGE_SIZE == (96, 128) and FIXTURE_COUNTS == {"train": 16, "validation": 4, "test": 4}
    assert Image.open(io.BytesIO(pq.read_table(cord_fixture["validation"]).to_pylist()[0]["image"]["bytes"])).size == (96, 128)
    for split, path in cord_fixture.items():
        assert path.name == FILE_NAMES[split]
        schema = pq.read_schema(path)
        assert str(schema) == REAL_SCHEMA_TEXT and schema.equals(CORD_SCHEMA) and schema.metadata is None
        table = pq.read_table(path)
        assert table.num_rows == FIXTURE_COUNTS[split] and pq.ParquetFile(path).metadata.num_row_groups == 1
        for row in table.to_pylist():
            assert row["image"]["path"] is None and set(row["image"]) == {"bytes", "path"}
            parsed = json.loads(row["ground_truth"])
            assert set(parsed) == {"gt_parse", "meta", "valid_line", "roi", "repeating_symbol", "dontcare"}
            assert parsed["meta"]["split"] == META_SPLIT[split] and parsed["meta"]["version"] == "2.0.0"
            assert parsed["meta"]["image_size"] == {"width": IMAGE_SIZE[0], "height": IMAGE_SIZE[1]}
            image = Image.open(io.BytesIO(row["image"]["bytes"]))
            assert image.format == "PNG" and image.size == IMAGE_SIZE
    for split in ("train", "validation", "test"):
        menus = [json.loads(row["ground_truth"])["gt_parse"]["menu"] for row in read(cord_fixture["train"].parent, split)]
        assert any(isinstance(menu, list) for menu in menus) and any(isinstance(menu, dict) for menu in menus), split
    train = [json.loads(row["ground_truth"]) for row in read(cord_fixture["train"].parent, "train")]
    assert [parsed["meta"]["image_id"] for parsed in train] == list(range(16))
    totals = [parsed["gt_parse"].get("total", {}).get("total_price") for parsed in train]
    assert sum(isinstance(total, list) for total in totals) == 1 and sum(total is None for total in totals) == 1, "one total in a list, one receipt with none"
    assert all(isinstance(total, (str, list)) for total in totals if total is not None)
    assert sum("void_menu" in parsed["gt_parse"] for parsed in train) == 1 and sum("sub_total" in parsed["gt_parse"] for parsed in train) >= 3
    assert all(set(parsed["gt_parse"]) <= {"menu", "sub_total", "total", "void_menu"} for parsed in train)


def test_the_images_differ_except_the_two_repeated_ones_of_train(cord_fixture):
    folder = cord_fixture["train"].parent
    hashes = {split: [hashlib.sha256(row["image"]["bytes"]).hexdigest() for row in read(folder, split)] for split in FIXTURE_COUNTS}
    assert len(hashes["train"]) - len(set(hashes["train"])) == 1 and hashes["train"][14] == hashes["train"][15]
    assert len(set(hashes["validation"])) == 4 and len(set(hashes["test"])) == 4
    assert not set(hashes["train"]) & set(hashes["validation"]) and not set(hashes["train"]) & set(hashes["test"])


def test_the_same_call_writes_the_same_bytes(cord_fixture, tmp_path):
    again = make_cord_fixture(tmp_path / "again")
    assert file_facts(again["train"].parent) == file_facts(cord_fixture["train"].parent)


def test_file_facts_are_the_size_and_the_sha256_of_each_file(cord_fixture):
    facts = file_facts(cord_fixture["train"].parent)
    assert set(facts) == set(FILE_NAMES.values())
    for name, (size, digest) in facts.items():
        content = (cord_fixture["train"].parent / name).read_bytes()
        assert (size, digest) == (len(content), hashlib.sha256(content).hexdigest())


@pytest.mark.real_data
def test_the_fixture_schema_is_the_schema_of_the_real_files(project_root):
    """Only the footers of the train and validation shards are read; the test shard is never opened."""
    raw = project_root / "data" / "raw" / "cord"
    for path in sorted(raw.glob("train-*.parquet")) + sorted(raw.glob("validation-*.parquet")):
        assert pq.read_schema(path).equals(CORD_SCHEMA, check_metadata=False), path.name


def test_each_malformed_variant_carries_exactly_its_defect_and_leaves_the_base_alone(cord_fixture):
    base = cord_fixture["train"].parent
    before = file_facts(base)
    train_schema = str(pq.read_schema(base / FILE_NAMES["train"]))
    for kind in MALFORMED_KINDS:
        copy = make_malformed(base, kind)
        assert copy.parent == base.parent and copy.name == f"malformed-{kind}" and set(file_facts(copy)) == set(FILE_NAMES.values()), kind
        assert file_facts(base) == before, f"{kind} touched the base"
        changed = {name for name, facts in file_facts(copy).items() if facts != before[name]}
        expected = {FILE_NAMES["validation"]} if kind in ("wrong_counts", "crossed_split") else {FILE_NAMES["train"]}
        assert changed == expected, kind
    schema = lambda kind: pq.read_schema(base.parent / f"malformed-{kind}" / FILE_NAMES["train"])  # noqa: E731
    assert schema("extra_column").names == ["image", "ground_truth", "extra"]
    assert schema("missing_column").names == ["image"]
    assert str(schema("wrong_type")).endswith("ground_truth: binary") and str(schema("wrong_type")).startswith(train_schema.split("ground_truth")[0])
    for kind in ("bad_json", "no_gt_parse", "too_deep", "pixel_bomb", "oversize"):
        assert str(schema(kind)) == train_schema, kind


def test_the_content_defects_are_the_ones_the_rules_look_for(cord_fixture):
    base = cord_fixture["train"].parent
    rows_of = lambda kind, split="train": read(base.parent / f"malformed-{kind}", split)  # noqa: E731
    with pytest.raises(json.JSONDecodeError):
        json.loads(rows_of("bad_json")[1]["ground_truth"])
    assert "gt_parse" not in json.loads(rows_of("no_gt_parse")[1]["ground_truth"])
    deep = json.loads(rows_of("too_deep")[1]["ground_truth"])["gt_parse"]
    assert depth(deep) == DEEP + 2 > 8, "deeper than max_gt_depth"
    bomb = Image.open(io.BytesIO(rows_of("pixel_bomb")[1]["image"]["bytes"]))
    assert bomb.size == (PIXEL_BOMB_SIDE, PIXEL_BOMB_SIDE) and bomb.size[0] * bomb.size[1] > 16_000_000 and len(rows_of("pixel_bomb")[1]["image"]["bytes"]) < 100_000
    assert (base.parent / "malformed-oversize" / FILE_NAMES["train"]).stat().st_size > OVERSIZE_MIN_BYTES
    assert len(rows_of("wrong_counts", "validation")) == FIXTURE_COUNTS["validation"] + 1 and len(rows_of("wrong_counts")) == FIXTURE_COUNTS["train"]
    crossed = [json.loads(row["ground_truth"])["meta"]["split"] for row in rows_of("crossed_split", "validation")]
    assert crossed == ["test", "valid", "valid", "valid"]
    a, b = (base / FILE_NAMES["train"]).read_bytes(), (base.parent / "malformed-wrong_hash" / FILE_NAMES["train"]).read_bytes()
    assert len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1 and a[:4] == b[:4] == b"PAR1" and a[-4:] == b[-4:] == b"PAR1"


def test_an_unknown_kind_is_refused_and_a_repeated_call_replaces_the_copy(cord_fixture):
    base = cord_fixture["train"].parent
    with pytest.raises(ValueError, match="unknown kind 'nothing'"):
        make_malformed(base, "nothing")
    first = make_malformed(base, "extra_column", name="again")
    (first / "leftover.txt").write_text("stale", encoding="utf-8")
    second = make_malformed(base, "extra_column", name="again")
    assert second == first and not (second / "leftover.txt").exists()
