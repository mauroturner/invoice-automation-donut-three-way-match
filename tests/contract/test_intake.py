"""The untrusted inbox (U07; AC09, RF07, RF08; V12-V16; plan section 6, decisions D08, D25): the type is the file's own bytes, the size and the pixels are held before
anything is decoded, a PDF is one page with one image and nothing that runs, each bad file goes to quarantine with its rule and the day goes on."""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path

import pytest
from PIL import Image

from invoiceflow.intake import FileCheck, inspect_file, load_page
from invoiceflow.synth.invoices import invoices
from invoiceflow.synth.render import render
from tests.fixtures.bad_files import KINDS, jpeg_bytes, make_bad_file, pdf_bytes, png_bytes

EXPECTED = {"pdf_named_png": ("V12", "extension_mismatch"), "two_page_pdf": ("V15", "pages"), "text_pdf": ("V15", "text"), "pixel_bomb_png": ("V14", "too_many_pixels"),
            "oversize": ("V13", "too_big"), "empty": ("V13", "empty")}


@pytest.fixture(scope="module")
def good(quick_view, quick_master, tmp_path_factory) -> dict[str, Path]:
    """One good file of each format, made by the generator."""
    folder = tmp_path_factory.mktemp("good")
    truths = invoices(quick_view, quick_master, "train", None, seal_path=folder / "none.json")
    found: dict[str, Path] = {}
    for truth in truths:
        if truth.file_format not in found:
            extension = {"png": ".png", "jpeg": ".jpg", "pdf": ".pdf"}[truth.file_format]
            found[truth.file_format] = folder / f"{truth.invoice_key}{extension}"
            found[truth.file_format].write_bytes(render(truth, quick_view))
    assert set(found) == {"png", "jpeg", "pdf"}
    return found


PAGE = (1240, 1754)      # A4 at the 150 dpi of the quick mode (D56; it was 413 x 585 at 50)


def small(view, **changes):
    return dataclasses.replace(view, intake=dataclasses.replace(view.intake, **changes))


def test_each_good_format_is_accepted_and_loads_as_one_rgb_page(quick_view, good):
    for file_format, path in good.items():
        check = inspect_file(path, quick_view)
        assert (check.status, check.rule_id, check.file_format, check.bytes) == ("accepted", None, file_format, path.stat().st_size), file_format
        assert check.pixels == PAGE[0] * PAGE[1] and len(check.sha256) == 64 and check.name == path.name
        page = load_page(path, quick_view)
        assert page.mode == "RGB" and page.size == PAGE, file_format


def test_each_bad_file_goes_to_quarantine_and_the_day_goes_on(quick_view, good, tmp_path):
    view = small(quick_view, max_file_bytes=200_000)
    checks = {}
    for kind in KINDS:
        path = make_bad_file(kind, tmp_path / "inbox", max_file_bytes=view.intake.max_file_bytes)
        checks[kind] = inspect_file(path, view)
        rule, reason = EXPECTED[kind]
        assert (checks[kind].status, checks[kind].rule_id, checks[kind].reason) == ("quarantined", rule, reason), kind
        assert checks[kind].sha256 is None, "a file that failed is never hashed"
    assert all(inspect_file(path, view).status == "accepted" for path in good.values()), "the good ones are not affected by the bad ones around them"


def test_a_bad_file_never_raises_and_never_stops_the_others(quick_view, good, tmp_path):
    names = [make_bad_file(kind, tmp_path, max_file_bytes=quick_view.intake.max_file_bytes).name for kind in KINDS if kind != "oversize"]
    results = [inspect_file(path, quick_view) for path in sorted(tmp_path.iterdir()) + list(good.values())]
    assert len(results) == len(names) + 3 and [r.status for r in results].count("accepted") == 3


@pytest.mark.parametrize(("name", "data", "rule", "reason"), [
    ("a.png", jpeg_bytes(), "V12", "extension_mismatch"), ("a.jpg", png_bytes(), "V12", "extension_mismatch"), ("a.jpeg", jpeg_bytes(), None, None),
    ("a.JPG", jpeg_bytes(), None, None), ("a.PNG", png_bytes(), None, None), ("a.txt", b"hello world", "V12", "unknown_type"), ("a.png", b"GIF89a....", "V12", "unknown_type"),
    ("a", png_bytes(), "V12", "extension_mismatch"), ("a.png", b"%PDF-1.4 but not", "V12", "extension_mismatch"), ("a.pdf", pdf_bytes(), None, None),
    ("a.png", b"\x89PNG\r\n\x1a\n", "V14", "unreadable_image"),
])
def test_the_type_is_the_files_own_first_bytes_and_must_match_its_extension(quick_view, tmp_path, name, data, rule, reason):
    path = tmp_path / name
    path.write_bytes(data)
    check = inspect_file(path, quick_view)
    assert (check.rule_id, check.reason) == (rule, reason)


def test_only_the_configured_formats_are_accepted(quick_view, good):
    only_png = small(quick_view, formats=("png",))
    assert inspect_file(good["jpeg"], only_png).rule_id == "V12" and inspect_file(good["jpeg"], only_png).reason == "format_not_accepted"
    assert inspect_file(good["pdf"], only_png).reason == "format_not_accepted" and inspect_file(good["png"], only_png).status == "accepted"


def test_the_size_limits_are_one_byte_to_the_maximum_and_the_size_is_held_before_the_file_is_read(quick_view, tmp_path, monkeypatch):
    data = png_bytes()
    exact = tmp_path / "exact.png"
    exact.write_bytes(data)
    assert inspect_file(exact, small(quick_view, max_file_bytes=len(data))).status == "accepted"
    check = inspect_file(exact, small(quick_view, max_file_bytes=len(data) - 1))
    assert (check.rule_id, check.reason) == ("V13", "too_big")
    opened = []
    real = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (opened.append(self.name), real(self, *a, **k))[1])
    monkeypatch.setattr(Image, "open", lambda *a, **k: pytest.fail("opened an oversize image"))
    inspect_file(exact, small(quick_view, max_file_bytes=len(data) - 1))
    assert opened == [], "an oversize file is not opened at all"


def test_the_pixels_are_the_headers_and_a_bomb_is_never_decoded(quick_view, tmp_path, monkeypatch):
    bomb = make_bad_file("pixel_bomb_png", tmp_path)
    decoded = []
    real_load = Image.Image.load
    monkeypatch.setattr(Image.Image, "load", lambda self, *a, **k: (decoded.append(self.size), real_load(self, *a, **k))[1])
    check = inspect_file(bomb, quick_view)
    assert (check.rule_id, check.reason, check.pixels) == ("V14", "too_many_pixels", 7000 * 7000) and decoded == []
    path = tmp_path / "edge.png"
    path.write_bytes(png_bytes((200, 100)))
    assert inspect_file(path, small(quick_view, max_pixels=20_000)).status == "accepted"
    refused = inspect_file(path, small(quick_view, max_pixels=19_999))
    assert (refused.rule_id, refused.pixels) == ("V14", 20_000)


def test_the_global_pixel_limit_of_pillow_is_restored_after_every_call(quick_view, tmp_path):
    before = Image.MAX_IMAGE_PIXELS
    path = tmp_path / "x.png"
    path.write_bytes(png_bytes())
    inspect_file(path, small(quick_view, max_pixels=100))
    inspect_file(make_bad_file("pixel_bomb_png", tmp_path), quick_view)
    assert Image.MAX_IMAGE_PIXELS == before


def test_a_picture_whose_data_is_broken_after_a_good_header_is_unreadable(quick_view, good, tmp_path):
    cut = tmp_path / "cut.png"
    cut.write_bytes(good["png"].read_bytes()[:200])
    check = inspect_file(cut, quick_view)
    assert (check.rule_id, check.reason, check.sha256) == ("V14", "unreadable_image", None)


def test_a_pdf_is_one_page_with_one_scan_and_nothing_that_runs(quick_view, tmp_path):
    def check(name: str, data: bytes, view=quick_view) -> FileCheck:
        path = tmp_path / name
        path.write_bytes(data)
        return inspect_file(path, view)

    assert check("ok.pdf", pdf_bytes()).status == "accepted"
    cases = [
        ("pages", pdf_bytes(pages=2)), ("text", pdf_bytes(image=False, text=True)), ("text", pdf_bytes(image=True, text=True)), ("no_image", pdf_bytes(image=False)),
        ("javascript_or_attachment", pdf_bytes(catalog_extra="/OpenAction << /S /JavaScript /JS (app.alert(1)) >>")),
        ("annotations_or_actions", pdf_bytes(page_extra="/Annots [] /AA << >>")), ("unreadable_pdf", b"%PDF-1.4 not a pdf at all"),
    ]
    for number, (reason, data) in enumerate(cases):
        result = check(f"case{number}.pdf", data)
        assert (result.status, result.rule_id, result.reason) == ("quarantined", "V15", reason), (number, reason, result.reason)
    assert check("form.pdf", pdf_bytes(form=True)).reason == "images", "a form XObject next to the scan is more than a scan"
    assert check("names.pdf", pdf_bytes(catalog_extra="/Names << /EmbeddedFiles << >> >>")).reason == "javascript_or_attachment"
    assert check("acro.pdf", pdf_bytes(catalog_extra="/AcroForm << >>")).reason == "javascript_or_attachment"


def test_the_pdfs_object_count_and_the_scans_pixels_are_limited(quick_view, tmp_path):
    path = tmp_path / "many.pdf"
    path.write_bytes(pdf_bytes(extra_objects=20))
    assert inspect_file(path, small(quick_view, pdf_max_objects=40)).status == "accepted"
    assert inspect_file(path, small(quick_view, pdf_max_objects=26)).status == "accepted", "25 objects and the free entry 0: a /Size of 26 is at the limit"
    refused = inspect_file(path, small(quick_view, pdf_max_objects=25))
    assert (refused.rule_id, refused.reason) == ("V15", "too_many_objects")
    big = tmp_path / "big.pdf"
    big.write_bytes(pdf_bytes(image_size=(300, 300)))
    result = inspect_file(big, small(quick_view, max_pixels=89_999))
    assert (result.rule_id, result.reason, result.pixels) == ("V14", "too_many_pixels", 90_000), "the scan inside a PDF is capped from its dictionary"


def test_a_symbolic_link_and_a_folder_are_not_files_of_the_inbox(quick_view, good, tmp_path):
    folder = tmp_path / "folder.png"
    folder.mkdir()
    assert (inspect_file(folder, quick_view).rule_id, inspect_file(folder, quick_view).reason) == ("V12", "not_a_regular_file")
    link = tmp_path / "link.png"
    try:
        link.symlink_to(good["png"])
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links need a privilege on this machine")
    assert inspect_file(link, quick_view).reason == "not_a_regular_file"


def test_loading_a_page_reads_the_image_of_a_pdf_and_refuses_what_inspect_would(quick_view, good, tmp_path):
    from invoiceflow.intake import IntakeError

    assert load_page(good["pdf"], quick_view).size == PAGE
    bad = make_bad_file("pdf_named_png", tmp_path)
    with pytest.raises(IntakeError, match="V12"):
        load_page(bad, quick_view)
    jpeg = io.BytesIO()
    Image.new("RGB", (8, 8)).save(jpeg, "JPEG")
    (tmp_path / "p.jpg").write_bytes(jpeg.getvalue())
    assert load_page(tmp_path / "p.jpg", quick_view).mode == "RGB"
