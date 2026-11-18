"""The validation of a delivery (U07; RF07, RF08, AC09; V10-V16, I01; plan section 6, decision D25): the delivery is the next one of the plan and is the one the manifest
says; a blocking rule stops the run and writes nothing; a bad file goes to quarantine and the delivery is still validated; a repeated hash is processed all the same."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from invoiceflow.deliveries import delivery_plan
from invoiceflow.validate import RULES, ValidationError, ValidationReport, validate_delivery
from tests.fixtures.bad_files import KINDS, jpeg_bytes, make_bad_file, pdf_bytes, png_bytes


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def delivery(quick_view, tmp_path):
    """The first daily delivery with three good PNG and a manifest that lists them and a truth file (not read, only sized and hashed)."""
    plan = delivery_plan(quick_view)
    spec = plan[1]
    root = tmp_path / "deliveries" / spec.delivery_id
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    files = {}
    for number in range(3):
        data = png_bytes((64 + number, 80), color=100 + number)
        (inbox / f"f{number}.png").write_bytes(data)
        files[f"f{number}.png"] = {"bytes": len(data), "sha256": sha(data)}
    truth = tmp_path / "truth" / f"{spec.delivery_id}.jsonl"
    truth.parent.mkdir()
    truth.write_bytes(b'{"x":1}\n')
    manifest = {"version": 1, "deliveries": {
        plan[0].delivery_id: {"status": "loaded"},
        spec.delivery_id: {"status": "published", "files": files, "truth": {"bytes": truth.stat().st_size, "sha256": sha(truth.read_bytes())}},
    }}
    return {"spec": spec, "inbox": inbox, "manifest": manifest, "truth": truth, "files": files, "root": root, "tmp": tmp_path}


def tree(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def run(quick_view, d, **kwargs) -> ValidationReport:
    return validate_delivery(d["spec"], d["inbox"], kwargs.pop("conn", None), quick_view, run_key="r1", manifest=d["manifest"], truth_path=d["truth"], **kwargs)


def test_a_good_delivery_passes_every_rule_and_writes_nothing(quick_view, delivery):
    before = tree(delivery["tmp"])
    report = run(quick_view, delivery)
    assert report.delivery_id == delivery["spec"].delivery_id and report.run_key == "r1" and report.blocking_failed == ()
    assert [r.rule_id for r in report.results] == ["V10", "V11", "V12", "V13", "V14", "V15", "V16", "I01"] and all(r.passed for r in report.results if r.rule_id != "I01")
    assert [(f.name, f.status) for f in report.files] == [("f0.png", "accepted"), ("f1.png", "accepted"), ("f2.png", "accepted")]
    assert next(r for r in report.results if r.rule_id == "I01").detail == {"png": 3}
    assert tree(delivery["tmp"]) == before and not (delivery["root"] / "quarantine").exists()


def test_the_rules_are_the_plans_table():
    assert {k: v[1:] for k, v in RULES.items()} == {
        "V10": ("bloqueante", "entrega", "per_run"), "V11": ("bloqueante", "entrega", "per_run"), "V12": ("advertencia", "archivo", "per_run"),
        "V13": ("advertencia", "archivo", "per_run"), "V14": ("advertencia", "archivo", "per_run"), "V15": ("advertencia", "archivo", "per_run"),
        "V16": ("advertencia", "archivo", "per_run"), "I01": ("informativa", "entrega", "per_run"),
    }
    assert all(v[0] for v in RULES.values())


def test_each_bad_file_is_quarantined_with_its_rule_and_the_delivery_is_not_blocked(quick_view, delivery):
    for kind in KINDS:
        data = make_bad_file(kind, delivery["tmp"] / "scratch", max_file_bytes=quick_view.intake.max_file_bytes)
        target = delivery["inbox"] / data.name
        data.replace(target)
        delivery["manifest"]["deliveries"][delivery["spec"].delivery_id]["files"][target.name] = {"bytes": target.stat().st_size, "sha256": sha(target.read_bytes())}
    report = run(quick_view, delivery)
    assert report.blocking_failed == ()
    by_name = {f.name: f for f in report.files}
    assert {n: f.rule_id for n, f in by_name.items() if f.status == "quarantined"} == {
        "bad_named_png.pdf": "V12", "bad_two_pages.pdf": "V15", "bad_text.pdf": "V15", "bad_bomb.png": "V14", "bad_oversize.png": "V13", "bad_empty.png": "V13"}
    assert sorted(p.name for p in (delivery["root"] / "quarantine").iterdir()) == sorted(n for n, f in by_name.items() if f.status == "quarantined")
    assert sorted(p.name for p in delivery["inbox"].iterdir()) == ["f0.png", "f1.png", "f2.png"], "what is left in the inbox is what was accepted"
    failed = {r.rule_id: r for r in report.results if not r.passed}
    assert set(failed) == {"V12", "V13", "V14", "V15"} and failed["V13"].files_affected == 2 and failed["V15"].files_affected == 2
    assert failed["V15"].examples == ("bad_text.pdf", "bad_two_pages.pdf"), "the names of the files, sorted, never their contents"
    assert all(r.passed for r in report.results if r.rule_id in ("V10", "V11", "V16"))


def test_a_delivery_that_is_not_the_next_one_is_v10_and_nothing_is_written(quick_view, delivery):
    plan = delivery_plan(quick_view)
    before = tree(delivery["tmp"])
    delivery["manifest"]["deliveries"][plan[0].delivery_id]["status"] = "published"
    with pytest.raises(ValidationError) as error:
        run(quick_view, delivery)
    assert error.value.report.blocking_failed == ("V10",) and ValidationError.EXIT_CODE == 3
    assert tree(delivery["tmp"]) == before
    skipping = dict(delivery, spec=plan[2])
    with pytest.raises(ValidationError) as skipped:
        run(quick_view, skipping)
    assert "V10" in skipped.value.report.blocking_failed


@pytest.mark.parametrize("change", ["extra_file", "missing_file", "changed_content", "same_size_changed", "truth_missing", "truth_changed", "no_entry"])
def test_a_delivery_that_is_not_the_manifests_is_v11_and_nothing_is_moved(quick_view, delivery, change):
    inbox = delivery["inbox"]
    if change == "extra_file":
        (inbox / "extra.png").write_bytes(png_bytes())
    elif change == "missing_file":
        (inbox / "f0.png").unlink()
    elif change == "changed_content":
        (inbox / "f0.png").write_bytes(png_bytes((9, 9)))
    elif change == "same_size_changed":
        original = (inbox / "f0.png").read_bytes()
        (inbox / "f0.png").write_bytes(original[:-5] + b"XXXXX")
    elif change == "truth_missing":
        delivery["truth"].unlink()
    elif change == "truth_changed":
        delivery["truth"].write_bytes(b'{"x":2}\n')
    else:
        del delivery["manifest"]["deliveries"][delivery["spec"].delivery_id]
    before = tree(delivery["tmp"])
    with pytest.raises(ValidationError) as error:
        run(quick_view, delivery)
    assert "V11" in error.value.report.blocking_failed and tree(delivery["tmp"]) == before and not (delivery["root"] / "quarantine").exists()


def test_the_truth_is_checked_by_size_and_hash_and_never_read(quick_view, delivery, monkeypatch):
    opened = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (opened.append(self.name), real(self, *a, **k))[1])
    import json

    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("parsed something"))
    run(quick_view, delivery)
    assert delivery["truth"].name not in opened


def test_a_repeated_hash_is_flagged_and_processed_all_the_same(quick_view, delivery):
    same = (delivery["inbox"] / "f0.png").read_bytes()
    (delivery["inbox"] / "copy.png").write_bytes(same)
    delivery["manifest"]["deliveries"][delivery["spec"].delivery_id]["files"]["copy.png"] = {"bytes": len(same), "sha256": sha(same)}
    report = run(quick_view, delivery)
    v16 = next(r for r in report.results if r.rule_id == "V16")
    assert not v16.passed and v16.files_affected == 2 and v16.examples == ("copy.png", "f0.png")
    assert [f.status for f in report.files] == ["accepted"] * 4 and report.blocking_failed == () and not (delivery["root"] / "quarantine").exists()


def test_a_hash_that_is_already_loaded_is_v16_too(quick_view, delivery):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE invoices (invoice_key TEXT PRIMARY KEY, file_sha256 TEXT)")
    conn.execute("INSERT INTO invoices VALUES ('D2026-09-30-1', ?)", (delivery["files"]["f1.png"]["sha256"],))
    report = run(quick_view, delivery, conn=conn)
    v16 = next(r for r in report.results if r.rule_id == "V16")
    assert (v16.passed, v16.files_affected, v16.examples) == (False, 1, ("f1.png",))
    empty = sqlite3.connect(":memory:")
    assert next(r for r in run(quick_view, delivery, conn=empty).results if r.rule_id == "V16").passed, "a warehouse with no invoices table has loaded nothing"


def test_the_report_is_plain_data_and_a_markdown_table(quick_view, delivery):
    report = run(quick_view, delivery)
    data = report.to_jsonable()
    assert data["delivery_id"] == delivery["spec"].delivery_id and data["run_key"] == "r1" and [r["rule_id"] for r in data["results"]][:2] == ["V10", "V11"]
    assert data["files"][0] == {"name": "f0.png", "status": "accepted", "rule_id": None, "reason": None, "format": "png", "bytes": report.files[0].bytes}
    text = report.to_markdown()
    assert text.startswith(f"# Validación de la entrega {delivery['spec'].delivery_id}") and "| V10 |" in text and "bloqueante" in text and "## Archivos" in text
    assert "f0.png" not in text.split("## Archivos")[0] and "f0.png" in text
    groups, current = [], []
    for line in text.splitlines():
        if line.startswith("|"):
            current.append(line.replace(r"\|", ""))
        elif current:
            groups.append(current)
            current = []
    groups.append(current)
    assert len(groups) == 2 and all(len({row.count("|") for row in group}) == 1 for group in groups), "every table row has the header's width"


def test_a_pipe_in_a_file_name_cannot_break_the_table():
    """A file name is untrusted text (Windows will not hold a pipe in one, Linux will): the table escapes it."""
    from invoiceflow.intake import FileCheck

    odd = FileCheck("a|b.png", Path("a|b.png"), "quarantined", "V12", "unknown_type", None, 3, None, None)
    text = ValidationReport("D2026-10-01", "r|1", (), (odd,)).to_markdown()
    assert r"a\|b.png" in text and "| a|b.png" not in text and r"r\|1" in text


def test_the_files_are_counted_by_format_with_the_unknown_ones_apart(quick_view, delivery):
    entry = delivery["manifest"]["deliveries"][delivery["spec"].delivery_id]["files"]
    for name, data in (("a.jpg", jpeg_bytes()), ("b.jpg", jpeg_bytes((70, 80))), ("c.pdf", pdf_bytes()), ("d.txt", b"hello")):
        (delivery["inbox"] / name).write_bytes(data)
        entry[name] = {"bytes": len(data), "sha256": sha(data)}
    counts = next(r for r in run(quick_view, delivery).results if r.rule_id == "I01").detail
    assert counts == {"jpeg": 2, "pdf": 1, "png": 3, "unknown": 1}
