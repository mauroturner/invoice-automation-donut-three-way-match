"""Files that must not get into the flow (U07; plan section 24, AC09): the kinds of ``make_bad_file`` and a builder of small PDFs of one page with the parts a test wants
(an image, text, a script, a second page). Every file is written under the folder the test gives, which is outside the working tree."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

KINDS = ("pdf_named_png", "two_page_pdf", "text_pdf", "pixel_bomb_png", "oversize", "empty")
PIXEL_BOMB_SIDE = 7000   # 49 million pixels in a 1-bit PNG of a few kilobytes: over max_pixels (40 million) without any memory cost until it is decoded


def png_bytes(size: tuple[int, int] = (64, 80), mode: str = "L", color: int = 255) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG")
    return buffer.getvalue()


def jpeg_bytes(size: tuple[int, int] = (64, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", size, 200).save(buffer, "JPEG")
    return buffer.getvalue()


def pdf_bytes(*, pages: int = 1, image: bool = True, text: bool = False, catalog_extra: str = "", page_extra: str = "", image_size: tuple[int, int] = (64, 80),
              extra_objects: int = 0, form: bool = False) -> bytes:
    """A PDF whose pages each show the same gray JPEG (when `image`) and, when `text`, a line of text in Helvetica; `catalog_extra` and `page_extra` are raw dictionary
    entries (``/OpenAction ...``); `extra_objects` adds that many unused objects (to pass the limit of objects)."""
    jpeg = jpeg_bytes(image_size)
    kids = " ".join(f"{3 + number} 0 R" for number in range(pages))
    first_free = 3 + pages
    objects: list[bytes] = [f"<< /Type /Catalog /Pages 2 0 R {catalog_extra} >>".encode(), f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode()]
    content_id = first_free + (1 if image else 0)
    form_ref = f" /Fm0 {content_id + 1} 0 R" if form else ""
    xobject = f"/XObject << /Im0 {first_free} 0 R{form_ref} >>" if image else (f"/XObject <<{form_ref} >>" if form else "")
    font = f"/Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >>" if text else ""
    for _ in range(pages):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << {xobject} {font} >> /Contents {content_id} 0 R {page_extra} >>".encode())
    if image:
        objects.append((f"<< /Type /XObject /Subtype /Image /Width {image_size[0]} /Height {image_size[1]} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode "
                        f"/Length {len(jpeg)} >>\nstream\n").encode() + jpeg + b"\nendstream")
    drawing = (b"q 595 0 0 842 0 0 cm /Im0 Do Q " if image else b"") + (b"BT /F1 12 Tf 20 20 Td (hola) Tj ET" if text else b"")
    objects.append(f"<< /Length {len(drawing)} >>\nstream\n".encode() + drawing + b"\nendstream")
    if form:
        objects.append(b"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Length 0 >>\nstream\n\nendstream")
    objects += [b"<< >>"] * extra_objects
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_bad_file(kind: str, folder: Path, *, max_file_bytes: int = 10_485_760) -> Path:
    """One bad file under `folder`: ``pdf_named_png`` (a PNG called .pdf), ``two_page_pdf``, ``text_pdf`` (a page with text and no scan), ``pixel_bomb_png`` (a PNG whose
    header says 49 million pixels), ``oversize`` (a PNG one byte over `max_file_bytes`) or ``empty``."""
    folder.mkdir(parents=True, exist_ok=True)
    if kind == "pdf_named_png":
        path, data = folder / "bad_named_png.pdf", png_bytes()
    elif kind == "two_page_pdf":
        path, data = folder / "bad_two_pages.pdf", pdf_bytes(pages=2)
    elif kind == "text_pdf":
        path, data = folder / "bad_text.pdf", pdf_bytes(image=False, text=True)
    elif kind == "pixel_bomb_png":
        path = folder / "bad_bomb.png"
        buffer = io.BytesIO()
        Image.new("1", (PIXEL_BOMB_SIDE, PIXEL_BOMB_SIDE)).save(buffer, "PNG")
        data = buffer.getvalue()
    elif kind == "oversize":
        path, data = folder / "bad_oversize.png", png_bytes() + b"\0" * max_file_bytes
    elif kind == "empty":
        path, data = folder / "bad_empty.png", b""
    else:
        raise ValueError(kind)
    path.write_bytes(data)
    return path
