"""The five invoice designs and the canvas they are drawn on (plan section 7.2; decision D10): L1 to L3 are the designs of the known suppliers, L4 is reserved for
``validation_new`` and L5 for ``test_new``.

Every design draws the same facts (the issuer, the receiver, the voucher, the order, the lines, the totals, the CAE and the QR) in its own places and with its own
columns, so a model that learns where a field is on L1 to L3 has to read, not remember, to read L5. All coordinates are in the units of an A4 page of 1,240 x 1,754 at
150 dpi and are scaled to the dpi of the configuration. Every page carries the legend of a synthetic document (D05). One font draws a whole page (the supplier's).

``Canvas`` records every text it draws with its box, so a test can ask what a page says and where, and check that the legend is drawn with ink.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import matplotlib
import qrcode
from PIL import Image, ImageDraw, ImageFont

from invoiceflow.synth import printed
from invoiceflow.synth.master import BUYER_NAME

NOTICE = "DOCUMENTO SINTÉTICO — SIN VALIDEZ FISCAL"
BASE_WIDTH, BASE_HEIGHT = 1240, 1754          # A4 at 150 dpi: the unit of every coordinate below
LINE_STEP = 36
MM_PER_INCH = 25.4
CM_PER_INCH = 2.54
DOCUMENT_TITLES = {1: ("FACTURA", "A", "COD. 01"), 3: ("NOTA DE CRÉDITO", "A", "COD. 03")}


def font_path(name: str) -> Path:
    """The font file of matplotlib's data that `name` names (``DejaVuSans`` -> ``DejaVuSans.ttf``): the same file on Windows and in the image of Linux."""
    path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / f"{name}.ttf"
    if not path.is_file():
        raise ValueError(f"la fuente {name!r} no está en los datos de matplotlib")
    return path


@lru_cache(maxsize=64)
def _font(name: str, pixels: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path(name)), pixels)


@dataclass
class Canvas:
    """A white A4 page in gray levels at `dpi` with the text drawn so far."""

    dpi: int
    font: str
    image: Image.Image = field(init=False)
    texts: list[tuple[str, tuple[int, int, int, int]]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.k = self.dpi / 150
        self.image = Image.new("L", (round(210 / MM_PER_INCH * self.dpi), round(297 / MM_PER_INCH * self.dpi)), 255)
        self._draw = ImageDraw.Draw(self.image)

    def px(self, value: float) -> int:
        return round(value * self.k)

    def text(self, x: float, y: float, text: str, size: int = 18, anchor: str = "la") -> tuple[int, int, int, int]:
        """Draw `text` with its anchor (``la`` left, ``ma`` middle, ``ra`` right, all top) at the point (x, y) of the base units; returns and records its box."""
        font = _font(self.font, max(4, self.px(size)))
        at = (self.px(x), self.px(y))
        self._draw.text(at, text, fill=0, font=font, anchor=anchor)
        box = self._draw.textbbox(at, text, font=font, anchor=anchor)
        self.texts.append((text, box))
        return box

    def line(self, x0: float, y0: float, x1: float, y1: float, width: int = 2) -> None:
        self._draw.line([self.px(x0), self.px(y0), self.px(x1), self.px(y1)], fill=0, width=max(1, self.px(width)))

    def rect(self, x0: float, y0: float, x1: float, y1: float, width: int = 2, fill: int | None = None) -> None:
        self._draw.rectangle([self.px(x0), self.px(y0), self.px(x1), self.px(y1)], outline=0 if width else None, fill=fill, width=max(1, self.px(width)))

    def paste_qr(self, text: str, x: float, y_bottom: float, centimetres: float) -> None:
        """The QR of `text` printed `centimetres` wide (its quiet zone included) with its left edge at x and its bottom edge at y_bottom."""
        side = max(21, round(centimetres / CM_PER_INCH * self.dpi))
        picture = qr_matrix(text).resize((side, side), Image.NEAREST)
        self.image.paste(picture, (self.px(x), self.px(y_bottom) - side))

    def qr_top(self, centimetres: float) -> float:
        """The top of a QR of that width whose bottom edge is at the base bottom margin, in base units."""
        return BASE_HEIGHT - 90 - centimetres / CM_PER_INCH * 150


@lru_cache(maxsize=8192)
def qr_matrix(text: str) -> Image.Image:
    """The QR of `text` with one pixel per module and its quiet zone (error correction M, the smallest version that holds it), in grey levels. A pure function of the text, so it is encoded
    once per process and kept: encoding (choosing the mask) is most of what drawing a page costs, and the suite draws the same invoices many times (D63). Callers resize a copy; the kept
    image is never changed."""
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=4)
    code.add_data(text)
    code.make(fit=True)
    return code.make_image(fill_color="black", back_color="white").convert("L")


@dataclass(frozen=True)
class Column:
    key: str            # code, desc, qty, unit, price, amount
    label: str
    x: float            # the left edge, or the right edge when align is "r"
    align: str = "l"


def _facts(truth) -> dict[str, str]:
    h, t = truth.header, truth.totals
    title, letter, code = DOCUMENT_TITLES[h.tipo]
    dollars = h.moneda == "DOL"
    return {
        "title": title, "letter": letter, "code": code, "supplier": h.razon_social, "cuit": printed.cuit(h.cuit_emisor), "iva_condition": "Responsable Inscripto",
        "pto": printed.pto_vta(h.pto_vta), "nro": printed.nro(h.nro), "fecha": printed.date_es(h.fecha), "buyer": BUYER_NAME, "buyer_cuit": printed.cuit(h.cuit_receptor),
        "po": h.orden_compra or "", "currency": h.moneda, "symbol": "US$" if dollars else "$", "ctz": printed.amount(h.cotizacion) if dollars else "",
        "neto": printed.amount(t.neto), "iva": printed.amount(t.iva), "total": printed.amount(t.total), "rate": "10,5" if t.iva_rate == Decimal("10.5") else "21",
        "cae": h.cae, "cae_vto": printed.date_es(h.cae_vto),
    }


def _row(line) -> dict[str, str]:
    return {"code": line.code, "desc": line.description, "qty": printed.quantity(line.qty), "unit": line.unit, "price": printed.amount(line.unit_price),
            "amount": printed.amount(line.amount)}


def _order_line(f: dict[str, str]) -> str:
    po = f"Orden de compra: {f['po']}" if f["po"] else "Orden de compra: -"
    money = f"Moneda: {f['currency']}" + (f"  Cotización: {f['ctz']}" if f["ctz"] else "")
    return f"{po}   {money}"


def _table(c: Canvas, truth, columns: list[Column], y0: float, *, grid: bool = False, zebra: bool = False, size: int = 17) -> float:
    """Header row at y0 and one row per line; returns the y after the last row."""
    for column in columns:
        c.text(column.x, y0, column.label, 18, "ra" if column.align == "r" else "la")
    if grid:
        c.line(60, y0 + 30, 1180, y0 + 30)
    y = y0 + 45
    for number, line in enumerate(truth.lines):
        cells = _row(line)
        if zebra and number % 2 == 0:
            c.rect(60, y - 6, 1180, y + LINE_STEP - 8, width=0, fill=232)
        for column in columns:
            c.text(column.x, y, cells[column.key], size, "ra" if column.align == "r" else "la")
        if grid:
            c.line(60, y + LINE_STEP - 8, 1180, y + LINE_STEP - 8, 1)
        y += LINE_STEP
    return y


def _cae_block(c: Canvas, f: dict[str, str], qr_cm: float, qr_text: str | None, *, right: bool = False) -> None:
    side_units = qr_cm / CM_PER_INCH * 150
    left = 1180 - side_units if right else 70
    bottom = BASE_HEIGHT - 90
    if qr_text is not None:
        c.paste_qr(qr_text, left, bottom, qr_cm)
    text_x = (left - 30) if right else left + side_units + 30
    anchor = "ra" if right else "la"
    top = bottom - side_units + 10
    c.text(text_x, top, f"CAE N°: {f['cae']}", 18, anchor)
    c.text(text_x, top + 30, f"Vencimiento CAE: {f['cae_vto']}", 16, anchor)
    c.text(text_x, top + 60, "Comprobante Autorizado (sintético)", 16, anchor)


def _totals_right(c: Canvas, f: dict[str, str], y: float, labels: tuple[str, str, str]) -> None:
    c.text(1160, y, f"{labels[0]}: {f['symbol']} {f['neto']}", 20, "ra")
    c.text(1160, y + 36, f"{labels[1]} {f['rate']}%: {f['symbol']} {f['iva']}", 20, "ra")
    c.text(1160, y + 80, f"{labels[2]}: {f['symbol']} {f['total']}", 24, "ra")


def draw_l1(c: Canvas, truth) -> None:
    """A boxed header with the letter in the middle, a table with a grid and the totals at the right."""
    f = _facts(truth)
    c.rect(60, 60, 1180, 300)
    c.rect(580, 60, 660, 140)
    c.text(620, 70, f["letter"], 48, "ma")
    c.text(620, 122, f["code"], 12, "ma")
    c.text(80, 80, f["supplier"], 26)
    c.text(80, 130, f"CUIT: {f['cuit']}", 18)
    c.text(80, 160, f"IVA {f['iva_condition']}", 16)
    c.text(700, 80, f["title"], 30)
    c.text(700, 130, f"Punto de Venta: {f['pto']}   Comp. Nro: {f['nro']}", 18)
    c.text(700, 165, f"Fecha de Emisión: {f['fecha']}", 18)
    c.text(80, 230, f"Cliente: {f['buyer']}   CUIT: {f['buyer_cuit']}", 18)
    c.text(80, 262, _order_line(f), 18)
    columns = [Column("code", "Código", 70), Column("desc", "Descripción", 190), Column("qty", "Cant.", 700, "r"), Column("unit", "U.", 720),
               Column("price", "P. Unit.", 960, "r"), Column("amount", "Subtotal", 1170, "r")]
    y = _table(c, truth, columns, 340, grid=True)
    _totals_right(c, f, max(y + 40, 900), ("Importe Neto Gravado", "IVA", "Importe Total"))
    _cae_block(c, f, truth.qr_cm, truth.qr_text)


def draw_l2(c: Canvas, truth) -> None:
    """No grid, the quantity first, the issuer at the right and the voucher at the left."""
    f = _facts(truth)
    c.text(80, 70, f"{f['title']} {f['letter']}  ({f['code'].title().replace('Cod.', 'Cód.')})", 30)
    c.text(80, 120, f"Nro {f['pto']}-{f['nro']}", 22)
    c.text(80, 160, f"Fecha {f['fecha']}", 20)
    c.text(1160, 70, f["supplier"], 22, "ra")
    c.text(1160, 105, f"C.U.I.T. {f['cuit']}", 18, "ra")
    c.text(1160, 135, "Resp. Inscripto", 16, "ra")
    c.text(80, 220, f"Señores: {f['buyer']}", 18)
    c.text(80, 250, f"CUIT {f['buyer_cuit']}  -  {_order_line(f)}", 18)
    columns = [Column("qty", "Cant.", 110, "r"), Column("desc", "Detalle", 140), Column("code", "Cód.", 640), Column("unit", "Unid.", 760),
               Column("price", "Precio", 980, "r"), Column("amount", "Importe", 1170, "r")]
    y = _table(c, truth, columns, 320)
    _totals_right(c, f, max(y + 40, 900), ("Subtotal neto", "I.V.A.", "TOTAL"))
    _cae_block(c, f, truth.qr_cm, truth.qr_text)


def draw_l3(c: Canvas, truth) -> None:
    """Two blocks of header side by side and the totals in a box."""
    f = _facts(truth)
    c.rect(60, 70, 600, 280, 1)
    c.rect(640, 70, 1180, 280, 1)
    c.text(80, 90, f["supplier"], 24)
    c.text(80, 135, f"CUIT {f['cuit']}", 18)
    c.text(80, 165, f"IVA: {f['iva_condition']}", 16)
    c.text(80, 195, f"Receptor: {f['buyer']}", 16)
    c.text(80, 225, f"CUIT receptor {f['buyer_cuit']}", 16)
    c.text(660, 90, f"{f['title']} {f['letter']}", 28)
    c.text(660, 135, f"Pto. Vta. {f['pto']}   N° {f['nro']}", 18)
    c.text(660, 165, f"Emitida el {f['fecha']}", 18)
    c.text(660, 195, f["code"], 16)
    c.text(660, 225, _order_line(f), 15)
    columns = [Column("code", "Cód.", 70), Column("desc", "Detalle", 200), Column("unit", "Unidad", 690), Column("qty", "Cantidad", 880, "r"),
               Column("price", "Precio unit.", 1020, "r"), Column("amount", "Importe", 1170, "r")]
    y = _table(c, truth, columns, 330)
    top = max(y + 40, 900)
    c.rect(700, top - 12, 1180, top + 130, 2)
    _totals_right(c, f, top, ("Neto gravado", "IVA", "Total"))
    _cae_block(c, f, truth.qr_cm, truth.qr_text)


def draw_l4(c: Canvas, truth) -> None:
    """The header at the right, zebra rows and the code in the last column; the QR bottom right (the design reserved for ``validation_new``)."""
    f = _facts(truth)
    c.text(1160, 60, f["supplier"], 26, "ra")
    c.text(1160, 100, f"CUIT {f['cuit']}   {f['iva_condition']}", 17, "ra")
    c.text(1160, 140, f"{f['title']} {f['letter']}   {f['code']}", 26, "ra")
    c.text(1160, 180, f"N° {f['pto']}-{f['nro']}", 20, "ra")
    c.text(1160, 212, f"Fecha: {f['fecha']}", 18, "ra")
    c.text(80, 120, "CLIENTE", 16)
    c.text(80, 150, f["buyer"], 18)
    c.text(80, 180, f"CUIT {f['buyer_cuit']}", 18)
    c.text(80, 250, _order_line(f), 17)
    columns = [Column("desc", "Artículo", 70), Column("qty", "Cant.", 700, "r"), Column("unit", "Un.", 720), Column("price", "Precio", 930, "r"),
               Column("amount", "Importe", 1100, "r"), Column("code", "Cód.", 1115)]
    y = _table(c, truth, columns, 330, zebra=True)
    _totals_right(c, f, max(y + 40, 900), ("Importe neto", "IVA", "Importe total"))
    _cae_block(c, f, truth.qr_cm, truth.qr_text, right=True)


def draw_l5(c: Canvas, truth) -> None:
    """A stacked header, the description first and the code second, and the totals in one row: the design reserved for ``test_new``."""
    f = _facts(truth)
    c.text(620, 70, f["supplier"], 28, "ma")
    c.text(620, 112, f"C.U.I.T. {f['cuit']}  -  {f['iva_condition']}", 17, "ma")
    c.text(620, 150, f"{f['title']} {f['letter']}", 30, "ma")
    c.text(620, 192, f"{f['code']}   Pto. de Venta {f['pto']}   Nº {f['nro']}   {f['fecha']}", 18, "ma")
    c.line(60, 230, 1180, 230, 1)
    c.text(70, 250, f"Cliente: {f['buyer']} (CUIT {f['buyer_cuit']})", 17)
    c.text(70, 282, _order_line(f), 17)
    columns = [Column("desc", "Descripción", 70), Column("code", "Código", 520), Column("price", "Precio unitario", 840, "r"), Column("qty", "Cantidad", 930, "r"),
               Column("unit", "Un.", 950), Column("amount", "Importe", 1170, "r")]
    y = _table(c, truth, columns, 350, grid=True)
    top = max(y + 40, 900)
    c.line(60, top - 10, 1180, top - 10, 1)
    c.text(70, top, f"Neto {f['symbol']} {f['neto']}", 19)
    c.text(520, top, f"IVA {f['rate']}% {f['symbol']} {f['iva']}", 19)
    c.text(1170, top, f"TOTAL {f['symbol']} {f['total']}", 22, "ra")
    _cae_block(c, f, truth.qr_cm, truth.qr_text)


LAYOUTS: dict[str, Callable[[Canvas, object], None]] = {"L1": draw_l1, "L2": draw_l2, "L3": draw_l3, "L4": draw_l4, "L5": draw_l5}


def draw(truth, dpi: int) -> Canvas:
    """The page of `truth` in its layout and font, with the legend of a synthetic document at the top."""
    if truth.layout not in LAYOUTS:
        raise ValueError(f"el diseño {truth.layout!r} no existe ({', '.join(LAYOUTS)})")
    canvas = Canvas(dpi, truth.font)
    canvas.text(BASE_WIDTH / 2, 28, NOTICE, 14, "ma")
    LAYOUTS[truth.layout](canvas, truth)
    return canvas
