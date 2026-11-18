"""Reading a QR from a rendered page for the tests of the generator (U06): the same reader the flow will use first (pyzbar), nothing of ``qr.py`` (U07)."""

from __future__ import annotations

from PIL import Image


def decode_qr_pyzbar(image: Image.Image) -> list[str]:
    from pyzbar.pyzbar import ZBarSymbol, decode

    return [item.data.decode("utf-8") for item in decode(image, symbols=[ZBarSymbol.QRCODE])]
