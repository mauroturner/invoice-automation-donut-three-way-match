"""The sources and the strict reading of CORD (U04, item 2: untrusted files; AC01, AC02, AC03): every malformed variant of the tiny CORD fails its own
rule and writes nothing, the pinned sizes and hashes are held before anything is read, the first view of the quick mode is registered only after a
good read, and the canonical index says what it found."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from invoiceflow import cord, sources
from invoiceflow.sources import SourceError, check_sources, fetch_source
from tests.fixtures.make_cord_fixture import FILE_NAMES, MALFORMED_KINDS, make_cord_fixture, make_malformed
from tests.fixtures.projects import PDF_BYTES, make_project

RULE_OF = {
    "extra_column": "V02", "missing_column": "V02", "wrong_type": "V02",
    "oversize": "V03", "pixel_bomb": "V03",
    "bad_json": "V04", "no_gt_parse": "V04", "too_deep": "V04",
    "wrong_counts": "V05", "crossed_split": "V05",
    "wrong_hash": "V01",
}
NAMED = {"extra_column": FILE_NAMES["train"], "missing_column": FILE_NAMES["train"], "wrong_type": FILE_NAMES["train"], "bad_json": FILE_NAMES["train"],
         "no_gt_parse": FILE_NAMES["train"], "too_deep": FILE_NAMES["train"], "pixel_bomb": FILE_NAMES["train"], "oversize": FILE_NAMES["train"],
         "wrong_hash": FILE_NAMES["train"], "wrong_counts": "validation", "crossed_split": FILE_NAMES["validation"]}   # what each message names
CAP = "max_shard_bytes = 900000"   # under the oversize file (over 1,000,000 bytes) and over every other file of the fixture
TRAIN, VALIDATION, TEST = FILE_NAMES["train"], FILE_NAMES["validation"], FILE_NAMES["test"]


def base_folder(path: Path) -> Path:
    return make_cord_fixture(path)["train"].parent


def replace_with(project, variant: Path, *names: str) -> None:
    for name in names:
        (project.cord_dir / name).write_bytes((variant / name).read_bytes())


# --- AC02: each malformed variant fails its rule and nothing is written --------------------------------------------------


def test_each_malformed_variant_fails_its_rule_and_writes_nothing(tmp_path):
    assert set(RULE_OF) == set(MALFORMED_KINDS)
    for kind in MALFORMED_KINDS:
        project = make_project(tmp_path / kind, source_extra=CAP)
        variant = make_malformed(base_folder(tmp_path / f"base-{kind}"), kind)
        if kind == "wrong_hash":
            fetch_source(project.cfg)   # the first view registers the hashes of the good files
        replace_with(project, variant, *FILE_NAMES.values())
        before = project.snapshot("data")
        with pytest.raises(SourceError) as caught:
            fetch_source(project.cfg)
        error = caught.value
        assert error.rule_id == RULE_OF[kind] and error.EXIT_CODE == 3 and str(error).startswith(f"{RULE_OF[kind]}: "), (kind, str(error))
        assert error.problems and all(problem["rule_id"] == RULE_OF[kind] for problem in error.problems), kind
        assert project.snapshot("data") == before, f"{kind}: a rejected source wrote something"
        assert ("runbook R1" in str(error)) == (error.rule_id in ("V01", "V06")), kind
        assert NAMED[kind] in str(error), f"{kind}: the message names {NAMED[kind]}"
        if kind != "wrong_hash":
            assert not (project.root / "data" / "quick" / sources.LOCK_NAME).exists() and not project.cfg.paths.interim_dir.exists(), kind


def test_a_file_that_is_not_parquet_at_all_is_v02_and_names_no_row(tmp_path):
    project = make_project(tmp_path)
    (project.cord_dir / TRAIN).write_bytes(b"this is not a parquet file")
    with pytest.raises(SourceError) as caught:
        fetch_source(project.cfg)
    assert caught.value.rule_id == "V02" and not project.cfg.paths.interim_dir.exists()


def test_the_pixel_bomb_is_refused_from_its_header_before_it_is_decoded(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    replace_with(project, make_malformed(base_folder(tmp_path / "base"), "pixel_bomb"), TRAIN)
    from PIL import Image

    def never(self, *args, **kwargs):  # noqa: ANN001
        raise AssertionError("an image was decoded")

    monkeypatch.setattr(Image.Image, "load", never)
    with pytest.raises(SourceError, match=r"V03.*16000000|V03.*píxeles"):
        fetch_source(project.cfg)


# --- AC01: the pinned files, held before anything is read -----------------------------------------------------------------


def accepted_project(tmp_path):
    return make_project(tmp_path, accepted=True)


def test_every_pinned_file_with_its_size_and_hash_is_accepted_and_the_hashes_come_back(tmp_path):
    project = accepted_project(tmp_path)
    hashes = check_sources(project.cfg)
    assert set(hashes) == set(FILE_NAMES.values()) | {"arca_spec"}
    for name in FILE_NAMES.values():
        assert hashes[name] == hashlib.sha256((project.cord_dir / name).read_bytes()).hexdigest()
    assert hashes["arca_spec"] == hashlib.sha256(PDF_BYTES).hexdigest()


def test_a_missing_file_a_wrong_size_and_a_wrong_hash_are_v01_naming_the_runbook_and_never_hash_a_file_of_another_size(tmp_path, monkeypatch):
    hashed: list[str] = []
    real = sources.sha256_file
    monkeypatch.setattr(sources, "sha256_file", lambda path, *args, **kwargs: (hashed.append(Path(path).name), real(path, *args, **kwargs))[1])
    # missing
    project = accepted_project(tmp_path / "missing")
    (project.cord_dir / VALIDATION).unlink()
    with pytest.raises(SourceError, match=r"V01.*runbook R1") as caught:
        check_sources(project.cfg)
    assert caught.value.problems[0]["reason"] == "missing" and caught.value.problems[0]["file"] == VALIDATION
    # another size: refused before any file is hashed
    project = accepted_project(tmp_path / "size")
    with (project.cord_dir / TEST).open("ab") as handle:
        handle.write(b"x")
    hashed.clear()
    with pytest.raises(SourceError, match=r"V01.*runbook R1") as caught:
        check_sources(project.cfg)
    assert caught.value.problems[0]["reason"] == "size" and caught.value.problems[0]["file"] == TEST and hashed == []
    # the same size, another hash
    project = accepted_project(tmp_path / "hash")
    content = bytearray((project.cord_dir / VALIDATION).read_bytes())
    content[len(content) // 2] ^= 0xFF
    (project.cord_dir / VALIDATION).write_bytes(bytes(content))
    with pytest.raises(SourceError, match=r"V01.*runbook R1") as caught:
        check_sources(project.cfg)
    assert caught.value.problems[0]["reason"] == "sha256" and caught.value.problems[0]["file"] == VALIDATION


def test_the_byte_cap_is_held_before_a_file_is_hashed(tmp_path, monkeypatch):
    project = make_project(tmp_path, source_extra="max_shard_bytes = 1000")   # quick mode: no accepted size can exceed the cap
    hashed: list[str] = []
    monkeypatch.setattr(sources, "sha256_file", lambda path, *args, **kwargs: hashed.append(Path(path).name) or "0" * 64)
    with pytest.raises(SourceError, match=r"V03.*max_shard_bytes") as caught:
        check_sources(project.cfg)
    assert hashed == [] and caught.value.rule_id == "V03"


def test_the_specification_pdf_is_v06_when_it_is_missing_or_another_size_or_another_hash(tmp_path):
    project = accepted_project(tmp_path / "missing")
    project.pdf.unlink()
    with pytest.raises(SourceError, match=r"V06.*runbook R1") as caught:
        check_sources(project.cfg)
    assert caught.value.problems[0]["reason"] == "missing"
    project = accepted_project(tmp_path / "size")
    project.pdf.write_bytes(PDF_BYTES + b"x")
    with pytest.raises(SourceError, match="V06") as caught:
        check_sources(project.cfg)
    assert caught.value.problems[0]["reason"] == "size"
    project = accepted_project(tmp_path / "hash")
    project.pdf.write_bytes(PDF_BYTES.replace(b"ARCA", b"AFIP"))
    assert len(PDF_BYTES.replace(b"ARCA", b"AFIP")) == len(PDF_BYTES)
    with pytest.raises(SourceError, match="V06") as caught:
        check_sources(project.cfg)
    assert caught.value.problems[0]["reason"] == "sha256"


# --- the quick mode: the first view is registered, and only after a good read ---------------------------------------------


def test_the_first_view_of_the_quick_mode_is_registered_after_a_good_read_and_held_afterwards(tmp_path):
    project = make_project(tmp_path)
    lock = project.root / "data" / "quick" / sources.LOCK_NAME
    assert not lock.exists()
    first = fetch_source(project.cfg)
    document = json.loads(lock.read_text(encoding="utf-8"))
    assert set(document) == {"version", "cord", "arca_spec"} and set(document["cord"]) == set(FILE_NAMES.values())
    assert document["cord"][TRAIN] == {"sha256": hashlib.sha256((project.cord_dir / TRAIN).read_bytes()).hexdigest(), "size": (project.cord_dir / TRAIN).stat().st_size}
    assert document["arca_spec"]["size"] == len(PDF_BYTES) and first["lock"] == "registered"
    again = fetch_source(project.cfg)
    assert again["lock"] == "held" and lock.read_text(encoding="utf-8") == json.dumps(document, indent=2, sort_keys=True) + "\n"
    # another file afterwards is V01 against the registered hash, and the lock stays
    content = bytearray((project.cord_dir / VALIDATION).read_bytes())
    content[len(content) // 2] ^= 0xFF
    (project.cord_dir / VALIDATION).write_bytes(bytes(content))
    with pytest.raises(SourceError, match=r"V01.*registrada") as caught:
        fetch_source(project.cfg)
    assert caught.value.problems[0]["reason"] == "sha256" and json.loads(lock.read_text(encoding="utf-8")) == document


VALID_ENTRY = '{"sha256": "' + "a" * 64 + '", "size": 5}'


@pytest.mark.parametrize("text", ['{"version": 2, "cord": {"a.parquet": ' + VALID_ENTRY + '}, "arca_spec": ' + VALID_ENTRY + '}',
                                  '{"version": 1, "cord": {}, "arca_spec": ' + VALID_ENTRY + '}', "not json", "[]", '{"version": 2, "cord": {}, "arca_spec": {}}', '{"version": 1, "cord": {}, "arca_spec": {"sha256": "x", "size": 1}}',
                                  '{"version": 1, "cord": {"a": {"sha256": "z", "size": 1}}, "arca_spec": {"sha256": "z", "size": 1}}'])
def test_a_lock_that_is_not_the_exact_format_is_v01(tmp_path, text):
    project = make_project(tmp_path)
    lock = project.root / "data" / "quick" / sources.LOCK_NAME
    lock.write_text(text, encoding="utf-8")
    with pytest.raises(SourceError, match="V01") as caught:
        fetch_source(project.cfg)
    assert str(lock) in str(caught.value) and not project.cfg.paths.interim_dir.exists()


# --- RF03, AC03: the canonical index -----------------------------------------------------------------------------------------


def test_the_index_says_what_was_found_and_the_manifest_is_written_last(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    written: list[str] = []
    real = cord.write_atomic
    monkeypatch.setattr(cord, "write_atomic", lambda path, data: (written.append(Path(path).name), real(path, data))[1])
    result = fetch_source(project.cfg)
    folder = project.cfg.paths.interim_dir / "cord"
    assert written == ["index-train.jsonl", "index-validation.jsonl", "manifest.json"] and result["index"] == "written"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1 and manifest["cord_commit"] == project.cfg.source.cord_commit
    assert manifest["splits"] == {"train": 16, "validation": 4}, "the index has no test receipt"
    assert set(manifest["source"]) == set(FILE_NAMES.values())
    for name, facts in manifest["files"].items():
        content = (folder / name).read_bytes()
        assert facts == {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "rows": len(content.splitlines())}, name
    rows = [json.loads(line) for line in (folder / "index-train.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["receipt_key"] for row in rows] == [f"train-{n}" for n in range(16)]
    table = pq.read_table(project.cord_dir / TRAIN).to_pylist()
    for row, original in zip(rows, table, strict=True):
        assert row["image_sha256"] == hashlib.sha256(original["image"]["bytes"]).hexdigest() and (row["width"], row["height"]) == (96, 128)
        assert isinstance(row["target"]["menu"], list) and set(row) == {
            "receipt_key", "image_sha256", "width", "height", "target", "menu_was_object", "total_in_list", "duplicate_of"}
    assert sum(row["menu_was_object"] for row in rows) == 10 and sum(row["total_in_list"] for row in rows) == 1
    assert [row["duplicate_of"] for row in rows].count(None) == 15 and rows[15]["duplicate_of"] == "train-14"
    assert all(len(row["target"]["menu"]) >= 1 for row in rows)
    assert not [path for path in folder.parent.rglob("*") if path.is_file() and "test" in path.name], "no file of the test split"
    assert sum(len(entries) for entries in cord.read_index(project.cfg).entries.values()) == 20


def test_a_second_fetch_writes_nothing_and_a_tampered_index_or_manifest_is_rebuilt(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    fetch_source(project.cfg)
    folder = project.cfg.paths.interim_dir / "cord"
    snapshot = project.snapshot("data")
    calls: list[str] = []
    real = cord.write_atomic
    monkeypatch.setattr(cord, "write_atomic", lambda path, data: (calls.append(Path(path).name), real(path, data))[1])
    assert fetch_source(project.cfg)["index"] == "unchanged" and calls == [] and project.snapshot("data") == snapshot
    (folder / "index-validation.jsonl").write_text("tampered\n", encoding="utf-8")
    assert fetch_source(project.cfg)["index"] == "written" and calls == ["index-train.jsonl", "index-validation.jsonl", "manifest.json"]
    assert project.snapshot("data") == snapshot, "the same bytes come back"
    calls.clear()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["source"][TRAIN] = "0" * 64
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert fetch_source(project.cfg)["index"] == "written" and project.snapshot("data") == snapshot
    (folder / "manifest.json").write_text("not json", encoding="utf-8")
    assert fetch_source(project.cfg)["index"] == "written" and project.snapshot("data") == snapshot


def test_the_footers_are_checked_for_every_file_without_reading_a_row(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    rows_read: list[str] = []
    monkeypatch.setattr(cord, "_iter_rows", lambda path, *args, **kwargs: rows_read.append(Path(path).name) or iter(()))
    assert cord.check_footers(project.cfg) == {"train": 16, "validation": 4, "test": 4} and rows_read == []


def test_reading_a_split_checks_each_receipt_when_it_is_reached(tmp_path):
    project = make_project(tmp_path)
    replace_with(project, make_malformed(base_folder(tmp_path / "base"), "bad_json"), TRAIN)
    stream = cord.read_split(project.cfg, "train")
    first = next(stream)
    assert first.receipt_key == "train-0" and first.split == "train" and first.index == 0
    with pytest.raises(SourceError, match="V04") as caught:
        next(stream)
    assert caught.value.problems[0]["receipt_key"] == "train-1"


def test_a_receipt_carries_its_image_its_hash_and_its_canonical_annotation(tmp_path):
    project = make_project(tmp_path)
    receipts = list(cord.read_split(project.cfg, "validation"))
    table = pq.read_table(project.cord_dir / VALIDATION).to_pylist()
    assert [r.receipt_key for r in receipts] == [f"validation-{n}" for n in range(4)]
    for receipt, original in zip(receipts, table, strict=True):
        assert receipt.image_bytes == original["image"]["bytes"] and receipt.image_sha256 == hashlib.sha256(receipt.image_bytes).hexdigest()
        assert (receipt.width, receipt.height) == (96, 128) and isinstance(receipt.gt_parse["menu"], list)
        raw = json.loads(original["ground_truth"])["gt_parse"]
        assert receipt.menu_was_object == isinstance(raw["menu"], dict)
        assert receipt.gt_parse["menu"] == (raw["menu"] if isinstance(raw["menu"], list) else [raw["menu"]])
        assert {key: value for key, value in receipt.gt_parse.items() if key != "menu"} == {key: value for key, value in raw.items() if key != "menu"}


def test_a_total_in_a_list_is_kept_as_it_is_and_counted(tmp_path):
    project = make_project(tmp_path)
    receipts = list(cord.read_split(project.cfg, "train"))
    listed = [r for r in receipts if r.total_in_list]
    assert [r.receipt_key for r in listed] == ["train-3"] and isinstance(listed[0].gt_parse["total"]["total_price"], list)


# --- AC03 on the real files (the footers of train and validation, and their rows; the rows of test are never read) ----------


@pytest.mark.real_data
def test_the_real_sources_the_real_counts_and_the_canonical_index_without_the_test_split(cfg, tmp_path):
    hashes = check_sources(cfg)   # the size and the SHA-256 of the six files and the PDF
    assert len(hashes) == 7
    assert cord.check_footers(cfg) == {"train": 800, "validation": 100, "test": 100}   # the test file: its footer only
    index = cord.build_index(cfg, hashes)
    assert [len(index.entries[split]) for split in ("train", "validation")] == [800, 100] and set(index.entries) == {"train", "validation"}
    entries = [entry for split in index.entries.values() for entry in split]
    assert all(isinstance(entry.target["menu"], list) for entry in entries) and sum(entry.menu_was_object for entry in entries) > 300
    assert len({entry.image_sha256 for entry in entries}) + sum(entry.duplicate_of is not None for entry in entries) == 900
    assert sum(entry.total_in_list for entry in entries) >= 1


# --- every limit is held at its edge, and every kind of bad row is its own rule -----------------------------------------------


def test_each_limit_accepts_its_edge_and_refuses_one_past_it(tmp_path):
    from invoiceflow.cord import build_index

    sizes = {name: (make_project(tmp_path / "probe").cord_dir / name).stat().st_size for name in FILE_NAMES.values()}
    biggest = max(sizes.values())
    # the image's pixels (96 x 128 = 12,288), the annotation's depth (4: the document, gt_parse, menu, a line), the rows of a file (train has 16), the bytes of a file
    cases = [
        ("max_image_pixels", 12288, 12287, "V03"), ("max_gt_depth", 4, 3, "V04"), ("max_rows", 16, 15, "V03"), ("max_shard_bytes", biggest, biggest - 1, "V03"),
    ]
    for key, edge, past, rule_id in cases:
        fetch_source(make_project(tmp_path / f"{key}-edge", source_extra=f"{key} = {edge}").cfg)
        with pytest.raises(SourceError) as caught:
            fetch_source(make_project(tmp_path / f"{key}-past", source_extra=f"{key} = {past}").cfg)
        assert caught.value.rule_id == rule_id and key in str(caught.value) + str(caught.value.problems), key


def rewrite_train(project, edit) -> None:
    from tests.fixtures.make_cord_fixture import CORD_SCHEMA, write_table
    import pyarrow as pa

    rows = pq.read_table(project.cord_dir / TRAIN).to_pylist()
    edit(rows)
    write_table(pa.Table.from_pylist(rows, schema=CORD_SCHEMA), project.cord_dir / TRAIN)


def with_annotation(**changes):
    def edit(rows):
        document = json.loads(rows[1]["ground_truth"])
        for key, value in changes.items():
            if value is ...:
                document.pop(key, None)
            else:
                document[key] = value
        rows[1]["ground_truth"] = json.dumps(document)
    return edit


def with_meta(**changes):
    def edit(rows):
        document = json.loads(rows[1]["ground_truth"])
        for key, value in changes.items():
            if value is ...:
                document["meta"].pop(key, None)
            else:
                document["meta"][key] = value
        rows[1]["ground_truth"] = json.dumps(document)
    return edit


def set_row(**fields):
    def edit(rows):
        rows[1].update(fields)
    return edit


ROW_DEFECTS = {
    "image_null": (set_row(image=None), "V03"),
    "image_bytes_null": (set_row(image={"bytes": None, "path": None}), "V03"),
    "image_bytes_empty": (set_row(image={"bytes": b"", "path": None}), "V03"),
    "not_an_image": (set_row(image={"bytes": b"not an image at all", "path": None}), "V03"),
    "ground_truth_null": (set_row(ground_truth=None), "V04"),
    "json_list": (set_row(ground_truth="[]"), "V04"),
    "json_nested_past_the_parser": (set_row(ground_truth="[" * 100_000 + "]" * 100_000), "V04"),
    "gt_parse_a_list": (with_annotation(gt_parse=[]), "V04"),
    "gt_parse_missing": (with_annotation(gt_parse=...), "V04"),
    "meta_missing": (with_annotation(meta=...), "V04"),
    "meta_a_list": (with_annotation(meta=[]), "V04"),
    "menu_a_string": (with_annotation(gt_parse={"menu": "x"}), "V04"),
    "split_missing": (with_meta(split=...), "V05"),
    "split_other": (with_meta(split="dev"), "V05"),
    "split_of_validation": (with_meta(split="valid"), "V05"),
}


@pytest.mark.parametrize("name", list(ROW_DEFECTS))
def test_each_defect_of_a_row_is_its_own_rule_and_names_the_receipt(tmp_path, name):
    edit, rule_id = ROW_DEFECTS[name]
    project = make_project(tmp_path)
    rewrite_train(project, edit)
    before = project.snapshot("data")
    with pytest.raises(SourceError) as caught:
        fetch_source(project.cfg)
    assert caught.value.rule_id == rule_id and caught.value.problems[0]["receipt_key"] == "train-1" and caught.value.problems[0]["file"] == TRAIN
    assert project.snapshot("data") == before


def test_a_file_of_another_size_than_the_registered_one_is_v01_in_quick_mode(tmp_path):
    project = make_project(tmp_path)
    fetch_source(project.cfg)
    with (project.cord_dir / TRAIN).open("ab") as handle:
        handle.write(b"x")
    with pytest.raises(SourceError, match=r"V01.*tamaño registrado") as caught:
        fetch_source(project.cfg)
    assert caught.value.problems[0]["reason"] == "size"
    project.pdf.write_bytes(PDF_BYTES + b"x")
    (project.cord_dir / TRAIN).write_bytes((project.cord_dir / TRAIN).read_bytes()[:-1])
    with pytest.raises(SourceError, match=r"V06.*tamaño registrado"):
        fetch_source(project.cfg)


def test_with_every_hash_accepted_no_registry_is_written_or_needed(tmp_path):
    project = make_project(tmp_path, accepted=True)
    result = fetch_source(project.cfg)
    assert result["lock"] == "not needed" and result["index"] == "written" and result["files"] == 4
    assert not list(project.root.rglob(sources.LOCK_NAME))
    assert fetch_source(project.cfg)["index"] == "unchanged"


def test_an_index_built_for_another_commit_is_rebuilt(tmp_path):
    project = make_project(tmp_path)
    fetch_source(project.cfg)
    other = make_project(tmp_path / "other", source_extra='cord_commit = "' + "a" * 40 + '"')
    from invoiceflow.cord import index_is_current

    hashes = check_sources(project.cfg)
    assert index_is_current(project.cfg, hashes)
    assert not index_is_current(dataclasses.replace(project.cfg, source=other.cfg.source), hashes)


def test_the_index_of_another_project_folder_is_not_trusted_and_a_missing_one_is_named(tmp_path):
    project = make_project(tmp_path)
    with pytest.raises(cord.CordIndexError, match="fetch_source"):
        cord.read_index(project.cfg)
    fetch_source(project.cfg)
    (project.cfg.paths.interim_dir / "cord" / "index-train.jsonl").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(cord.CordIndexError, match="no está entero"):
        cord.read_index(project.cfg)


# --- what was left to prove: the edges of each rule, one by one -------------------------------------------------------------


def test_the_cap_on_the_bytes_of_a_file_is_the_shards_cap_and_does_not_apply_to_the_pdf(tmp_path):
    project = make_project(tmp_path, source_extra="max_shard_bytes = 60000")
    project.pdf.write_bytes(b"%PDF-1.4 " + b"0" * 100_000)
    assert project.pdf.stat().st_size > 60000 > max(path.stat().st_size for path in project.cord_dir.iterdir())
    assert fetch_source(project.cfg)["lock"] == "registered"


def test_an_accepted_hash_wins_over_a_registered_one_and_a_mixed_project_registers_what_is_not_pinned(tmp_path):
    project = make_project(tmp_path / "wins", accepted=True)
    wrong = {"version": 1, "cord": {name: {"sha256": "b" * 64, "size": 7} for name in FILE_NAMES.values()}, "arca_spec": {"sha256": "b" * 64, "size": 7}}
    sources.lock_path(project.cfg).write_text(json.dumps(wrong), encoding="utf-8")   # next to the folder of the CORD files: the one the project reads
    assert sources.lock_path(project.cfg) == project.root / "data" / "raw" / sources.LOCK_NAME
    assert fetch_source(project.cfg)["lock"] == "not needed", "the accepted size and hash are the ones that count"
    mixed = make_project(tmp_path / "mixed", accepted=True, pdf_quick=True)
    assert mixed.cfg.source.arca_spec_sha256 == "" and all(f.sha256 for f in mixed.cfg.source.cord_files)
    assert fetch_source(mixed.cfg)["lock"] == "registered" and fetch_source(mixed.cfg)["lock"] == "held"


def test_the_message_of_v02_says_what_differs_in_the_schema(tmp_path):
    from tests.fixtures.make_cord_fixture import CORD_SCHEMA, write_table
    import pyarrow as pa

    for kind, fragment in (("extra_column", "de más ['extra']"), ("missing_column", "faltan ['ground_truth']"), ("wrong_type", "ground_truth es binary")):
        project = make_project(tmp_path / kind)
        replace_with(project, make_malformed(base_folder(tmp_path / f"base-{kind}"), kind), TRAIN)
        with pytest.raises(SourceError) as caught:
            fetch_source(project.cfg)
        assert fragment in str(caught.value), kind
    project = make_project(tmp_path / "order")
    table = pq.read_table(project.cord_dir / TRAIN)
    write_table(table.select(["ground_truth", "image"]), project.cord_dir / TRAIN)
    with pytest.raises(SourceError, match="otro orden") as caught:
        fetch_source(project.cfg)
    assert caught.value.rule_id == "V02" and CORD_SCHEMA.names == ["image", "ground_truth"] and pa


def test_the_counts_of_the_test_file_are_held_from_its_footer_alone(tmp_path):
    from tests.fixtures.make_cord_fixture import CORD_SCHEMA, rows, write_table
    import pyarrow as pa

    for count in (3, 5):
        project = make_project(tmp_path / f"test-{count}")
        write_table(pa.Table.from_pylist(rows("test", count), schema=CORD_SCHEMA), project.cord_dir / TEST)
        with pytest.raises(SourceError) as caught:
            fetch_source(project.cfg)
        assert caught.value.rule_id == "V05" and caught.value.problems[0]["split"] == "test" and caught.value.problems[0]["rows"] == count


def test_a_split_that_changes_between_the_footer_and_the_read_is_still_v05(tmp_path):
    from tests.fixtures.make_cord_fixture import CORD_SCHEMA, rows, write_table
    import pyarrow as pa

    project = make_project(tmp_path)
    write_table(pa.Table.from_pylist(rows("validation", 5), schema=CORD_SCHEMA), project.cord_dir / VALIDATION)
    with pytest.raises(SourceError) as caught:   # build_index does not read the footers: the end of the read counts the receipts
        cord.build_index(project.cfg)
    assert caught.value.rule_id == "V05" and caught.value.problems[0]["rows"] == 5 and caught.value.problems[0]["split"] == "validation"


def test_too_many_row_groups_in_a_footer_are_v03(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    monkeypatch.setattr(cord, "MAX_ROW_GROUPS", 0)
    with pytest.raises(SourceError, match="grupos de filas") as caught:
        cord.check_footers(project.cfg)
    assert caught.value.rule_id == "V03" and caught.value.problems[0]["row_groups"] == 1


def test_the_depth_counts_lists_as_well_as_objects(tmp_path):
    deep = lambda rows: rows[1].update(ground_truth=json.dumps({"gt_parse": {"menu": [], "x": [[[[]]]]}, "meta": {"split": "train"}}))  # noqa: E731
    for limit, ok in ((6, True), (5, False)):   # the document, gt_parse and four lists: 6 levels
        project = make_project(tmp_path / f"limit-{limit}", source_extra=f"max_gt_depth = {limit}")
        rewrite_train(project, deep)
        if ok:
            assert len(cord.build_index(project.cfg).entries["train"]) == 16
        else:
            with pytest.raises(SourceError, match="anida 6 niveles") as caught:
                cord.build_index(project.cfg)
            assert caught.value.rule_id == "V04"


ROW_MESSAGES = {"image_null": "no trae los bytes", "image_bytes_null": "no trae los bytes", "image_bytes_empty": "no trae los bytes",
                "not_an_image": "no es una imagen legible", "ground_truth_null": "no tiene texto de anotación", "json_list": "gt_parse y un objeto meta",
                "gt_parse_a_list": "gt_parse y un objeto meta", "gt_parse_missing": "gt_parse y un objeto meta", "meta_missing": "gt_parse y un objeto meta",
                "meta_a_list": "gt_parse y un objeto meta", "menu_a_string": "no es una lista ni un objeto", "json_nested_past_the_parser": "no es un JSON legible",
                "split_missing": "dice meta.split = None", "split_other": "dice meta.split = 'dev'", "split_of_validation": "dice meta.split = 'valid'"}


@pytest.mark.parametrize("name", list(ROW_DEFECTS))
def test_each_defect_of_a_row_says_what_it_is(tmp_path, name):
    project = make_project(tmp_path)
    rewrite_train(project, ROW_DEFECTS[name][0])
    with pytest.raises(SourceError) as caught:
        fetch_source(project.cfg)
    assert ROW_MESSAGES[name] in str(caught.value)


def test_the_index_is_written_with_sorted_keys_and_a_same_size_change_or_an_old_manifest_is_rebuilt(tmp_path):
    project = make_project(tmp_path)
    fetch_source(project.cfg)
    folder = project.cfg.paths.interim_dir / "cord"
    snapshot = project.snapshot("data")
    first = (folder / "index-train.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith('{"duplicate_of": null, "height": 128, "image_sha256": "') and list(json.loads(first)) == sorted(json.loads(first))
    content = bytearray((folder / "index-validation.jsonl").read_bytes())
    content[10] = ord("x") if content[10] != ord("x") else ord("y")   # the same size, another hash
    (folder / "index-validation.jsonl").write_bytes(bytes(content))
    assert fetch_source(project.cfg)["index"] == "written" and project.snapshot("data") == snapshot
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = 2
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert fetch_source(project.cfg)["index"] == "written" and project.snapshot("data") == snapshot
