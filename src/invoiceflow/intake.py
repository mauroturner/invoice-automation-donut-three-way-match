"""The inbox is untrusted (RF07, RF08; V12 to V15; plan section 6; decisions D08, D25): a file is looked at as bytes before it is believed to be a picture.

``inspect_file`` applies the rules in this order and stops at the first that fails; a file that fails is quarantined with its rule and a short reason, never raises, and is never
hashed. The order spares work: nothing is read of a file that is too big.

* **V13** the size is 1 byte to ``max_file_bytes`` (from the directory entry: the file is not opened); a link or a folder is not a file of the inbox (V12).
* **V12** the type is the file's own first bytes (``%PDF-``, the PNG signature, the JPEG marker), is one of ``formats`` and agrees with the extension.
* **V14** the pixels are the header's (``Image.open`` is lazy), at most ``max_pixels``; ``Image.MAX_IMAGE_PIXELS`` is set to that limit for the call and restored. Then the picture is
  decoded once: a header that lies about its data is unreadable, here and not in the middle of the flow. A PDF's scan is capped from its dictionary, before it is decoded.
* **V15** a PDF has at most ``pdf_max_objects`` objects, one page, no script, action, form, annotation or attachment, no text and exactly one image.

``load_page`` is the picture of a good file as an RGB image: the file itself or the one image of the PDF.
"""

from __future__ import annotations

import io
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from invoiceflow.config import SealedConfig
from invoiceflow.files import sha256_file

SIGNATURES = ((b"%PDF-", "pdf"), (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"))
EXTENSIONS = {"png": (".png",), "jpeg": (".jpg", ".jpeg"), "pdf": (".pdf",)}
CATALOG_FORBIDDEN = ("/OpenAction", "/AA", "/Names", "/AF", "/AcroForm", "/JavaScript", "/EmbeddedFiles")
PAGE_FORBIDDEN = ("/AA", "/Annots", "/AF")


class IntakeError(RuntimeError):
    """A file that ``inspect_file`` would quarantine was asked to be loaded as a page."""


@dataclass(frozen=True)
class FileCheck:
    name: str
    path: Path
    status: Literal["accepted", "quarantined"]
    rule_id: str | None
    reason: str | None
    file_format: str | None
    bytes: int
    pixels: int | None
    sha256: str | None


def _failed(path: Path, rule_id: str, reason: str, *, size: int = 0, file_format: str | None = None, pixels: int | None = None) -> FileCheck:
    return FileCheck(path.name, path, "quarantined", rule_id, reason, file_format, size, pixels, None)


@contextmanager
def _pixel_limit(limit: int) -> Iterator[None]:
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = limit
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)   # the limit is held by hand, from the header, so that the pixels are known
            yield
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def _sniff(path: Path) -> str | None:
    with path.open("rb") as handle:
        head = handle.read(8)
    return next((name for signature, name in SIGNATURES if head.startswith(signature)), None)


def _picture(path: Path, limit: int) -> tuple[Image.Image | None, int | None, str | None]:
    """(the decoded picture, its pixels, the reason it is refused). The pixels come from the header and are given even when they are too many."""
    try:
        with _pixel_limit(limit):
            image = Image.open(path)
            pixels = image.width * image.height
            if pixels > limit:
                image.close()
                return None, pixels, "too_many_pixels"
            image.load()
            return image, pixels, None
    except Image.DecompressionBombError:   # more than twice the limit: Pillow refuses to say how many
        return None, None, "too_many_pixels"
    except (OSError, SyntaxError, ValueError, EOFError):
        return None, None, "unreadable_image"


def _pdf_scan(data: bytes, view: SealedConfig) -> tuple[Image.Image | None, int | None, str | None, str | None]:
    """(the scan, its pixels, the rule that refuses the file, the reason) for a PDF held in memory."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            return None, None, "V15", "encrypted"
        if int(reader.trailer["/Size"]) > view.intake.pdf_max_objects:
            return None, None, "V15", "too_many_objects"
        if len(reader.pages) != 1:
            return None, None, "V15", "pages"
        catalog = reader.trailer["/Root"]
        if any(key in catalog for key in CATALOG_FORBIDDEN):
            return None, None, "V15", "javascript_or_attachment"
        page = reader.pages[0]
        if any(key in page for key in PAGE_FORBIDDEN):
            return None, None, "V15", "annotations_or_actions"
        resources = page.get("/Resources", {})
        if "/Font" in resources or page.extract_text().strip():
            return None, None, "V15", "text"
        objects = [value.get_object() for value in resources.get("/XObject", {}).values()]
        images = [item for item in objects if item.get("/Subtype") == "/Image"]
        if len(images) != 1 or len(objects) != 1:
            return None, None, "V15", "no_image" if not images else "images"
        pixels = int(images[0]["/Width"]) * int(images[0]["/Height"])
        if pixels > view.intake.max_pixels:
            return None, pixels, "V14", "too_many_pixels"
        with _pixel_limit(view.intake.max_pixels):
            return page.images[0].image.convert("RGB"), pixels, None, None
    except (PyPdfError, ValueError, KeyError, TypeError, OSError, RecursionError, AttributeError, IndexError):
        return None, None, "V15", "unreadable_pdf"


def inspect_file(path: Path, view: SealedConfig) -> FileCheck:
    """V12 to V15 for one file of the inbox. A file that fails is quarantined with its rule and reason (and never hashed); one that passes carries its size, its pixels and its
    SHA-256. Never raises for what a file holds."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return _failed(path, "V12", "not_a_regular_file")
    size = path.stat().st_size
    if size < 1:
        return _failed(path, "V13", "empty", size=size)
    if size > view.intake.max_file_bytes:
        return _failed(path, "V13", "too_big", size=size)
    kind = _sniff(path)
    if kind is None:
        return _failed(path, "V12", "unknown_type", size=size)
    if kind not in view.intake.formats:
        return _failed(path, "V12", "format_not_accepted", size=size, file_format=kind)
    if path.suffix.lower() not in EXTENSIONS[kind]:
        return _failed(path, "V12", "extension_mismatch", size=size, file_format=kind)
    if kind == "pdf":
        _scan, pixels, rule_id, reason = _pdf_scan(path.read_bytes(), view)
        if rule_id is not None:
            return _failed(path, rule_id, reason, size=size, file_format=kind, pixels=pixels)
    else:
        picture, pixels, reason = _picture(path, view.intake.max_pixels)
        if picture is None:
            return _failed(path, "V14", reason, size=size, file_format=kind, pixels=pixels)
        picture.close()
    return FileCheck(path.name, path, "accepted", None, None, kind, size, pixels, sha256_file(path))


def load_page(path: Path, view: SealedConfig) -> Image.Image:
    """The page of a file that ``inspect_file`` accepts, as an RGB image (the image of a PDF, or the picture itself). A file it would quarantine is an ``IntakeError``."""
    path = Path(path)
    check = inspect_file(path, view)
    if check.status != "accepted":
        raise IntakeError(f"{check.rule_id} ({check.reason}): el archivo {path.name} no es una página que se pueda leer")
    if check.file_format == "pdf":
        scan, _pixels, _rule, _reason = _pdf_scan(path.read_bytes(), view)
        return scan
    with _pixel_limit(view.intake.max_pixels):
        with Image.open(path) as image:
            return image.convert("RGB")
