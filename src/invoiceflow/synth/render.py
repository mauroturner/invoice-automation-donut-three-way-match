"""The page of a synthetic invoice as a file (plan section 7.2; decisions D04, D05, D08): the layout's drawing, a scan profile that spoils it the way a scanner or a
photograph would, and a PNG, a JPEG or a PDF of one page with the image inside.

A page is a pure function of its invoice and the seed of its split: the scan's random numbers come from ``random.Random`` seeded with the seed and the invoice's key, so
the same configuration gives the same bytes on the same machine and a page never depends on the pages drawn before it. The PDF is written by hand (one page, one JPEG
image, no text, no script, no attachment and no date): it is the file a scanner makes, and the intake (V15) accepts exactly that.
"""

from __future__ import annotations

import io
import random
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageFilter

from invoiceflow.config import SealedConfig
from invoiceflow.synth import layouts
from invoiceflow.synth.invoices import InvoiceTruth, split_seed
from invoiceflow.synth.layouts import NOTICE

CLEAN_JPEG_QUALITY = 85
MILD = {"quality": (60, 85), "rotation": 1.0, "noise_sigma": 3.0}
POOR = {"quality": (35, 50), "rotation": 2.5, "blur": (0.6, 1.0), "salt": 0.005}
PDF_A4_POINTS = (595.28, 841.89)

__all__ = ["NOTICE", "Page", "draw_page", "render", "scan"]


class Page(NamedTuple):
    image: Image.Image                                   # the clean page, in gray levels
    texts: tuple[tuple[str, tuple[int, int, int, int]], ...]   # every text drawn and its box (pixels)


def draw_page(truth: InvoiceTruth, view: SealedConfig) -> Page:
    """The clean page of `truth` at the dpi of the configuration, before any scan effect."""
    canvas = layouts.draw(truth, view.synth.dpi)
    return Page(canvas.image, tuple(canvas.texts))


def scan(page: Image.Image, profile: str, rng: random.Random) -> tuple[Image.Image, int | None]:
    """Spoil `page` as the profile says: ``clean`` leaves it as it is; ``mild`` rotates up to 1 degree, adds light Gaussian noise and asks a JPEG quality of 60 to 85;
    ``poor`` rotates up to 2.5 degrees, blurs with a radius of 0.6 to 1.0, flips 0.5 % of the pixels and asks a JPEG quality of 35 to 50. Returns the page and the JPEG
    quality the file must be saved with (``None``: no compression to imitate)."""
    if profile == "clean":
        return page, None
    if profile not in ("mild", "poor"):
        raise ValueError(f"el perfil de escaneo {profile!r} no existe (clean, mild, poor)")
    spec = MILD if profile == "mild" else POOR
    low, high = spec["quality"]
    quality = rng.randint(low, high)
    angle = rng.uniform(-spec["rotation"], spec["rotation"])
    image = page.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    if profile == "poor":
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(*spec["blur"])))
    generator = np.random.default_rng(rng.getrandbits(32))
    pixels = np.asarray(image, dtype=np.float32)
    if profile == "mild":
        pixels = np.clip(pixels + generator.normal(0.0, spec["noise_sigma"], pixels.shape), 0, 255)
    else:
        flipped = generator.random(pixels.shape) < spec["salt"]
        pixels = np.where(flipped, 255 - pixels, pixels)
    return Image.fromarray(pixels.astype(np.uint8), "L"), quality


def _jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def _pdf(jpeg: bytes, size: tuple[int, int]) -> bytes:
    """A one-page PDF of A4 size whose only content is the JPEG (DCT) gray image: five objects and a cross-reference table, no dates and no metadata."""
    width, height = size
    points = f"{PDF_A4_POINTS[0]} {PDF_A4_POINTS[1]}"
    content = f"q {PDF_A4_POINTS[0]} 0 0 {PDF_A4_POINTS[1]} 0 0 cm /Im0 Do Q".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {points}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>".encode("ascii"),
        (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode "
         f"/Length {len(jpeg)} >>\nstream\n").encode("ascii") + jpeg + b"\nendstream",
        f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    out += b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    return bytes(out)


def render(truth: InvoiceTruth, view: SealedConfig) -> bytes:
    """The file of `truth`: its page, scanned with its profile, as a PNG, a JPEG or a one-page PDF."""
    clean = draw_page(truth, view).image
    rng = random.Random(f"invoiceflow.scan:{split_seed(view, truth.split)}:{truth.invoice_key}")
    image, quality = scan(clean, truth.scan_profile, rng)
    if truth.file_format == "jpeg":
        return _jpeg(image, quality or CLEAN_JPEG_QUALITY)
    if truth.file_format == "pdf":
        return _pdf(_jpeg(image, quality or CLEAN_JPEG_QUALITY), image.size)
    if truth.file_format != "png":
        raise ValueError(f"el formato de archivo {truth.file_format!r} no existe (png, jpeg, pdf)")
    if quality is not None:
        image = Image.open(io.BytesIO(_jpeg(image, quality))).convert("L")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()
