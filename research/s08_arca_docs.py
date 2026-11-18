"""ARCA (ex AFIP) documents for the electronic-invoice QR: download, hash, and extract text.

Downloads the QR specification PDF and the QR concepts page into data/raw/arca/ (git-ignored), prints bytes and
SHA-256, and extracts the PDF text with pdftotext (Poppler) into data/interim/research/arca_qr_spec.txt, which
s04 and s07 read. Run from any directory:  python -I research/s08_arca_docs.py
"""
import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "data" / "raw" / "arca"
OUT = PROJECT / "data" / "interim" / "research"
UA = "research-script/1.0"
DOCS = [
    ("QRespecificaciones.pdf", "https://www.afip.gob.ar/fe/qr/documentos/QRespecificaciones.pdf"),
    ("qr_concepts.html", "https://www.afip.gob.ar/fe/qr/conceptos-generales.asp"),
]


def fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    return dest.stat().st_size


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, url in DOCS:
        dest = RAW / name
        n = fetch(url, dest)
        print(f"[get] {name}: {n} bytes, sha256 {sha256_of(dest)}, source {url}")

    html = (RAW / "qr_concepts.html").read_text(encoding="latin-1")
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    print(f"[page] title: {title.group(1).strip() if title else 'n/a'}")
    print(f"[page] mentions 'Comprobantes en l' scope: {bool(re.search(r'Comprobantes en l', html))}")

    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        print("[pdf] pdftotext not found on PATH; text extraction skipped")
        return 1
    txt = OUT / "arca_qr_spec.txt"
    subprocess.run([pdftotext, "-layout", str(RAW / "QRespecificaciones.pdf"), str(txt)], check=True)
    body = txt.read_text(encoding="utf-8", errors="replace")
    pages = body.count("\f") + 1
    print(f"[pdf] text extracted: {len(body.splitlines())} lines, {pages} pages")
    field_pattern = r'"(ver|fecha|cuit|ptoVta|tipoCmp|nroCmp|importe|moneda|ctz|tipoDocRec|nroDocRec|tipoCodAut|codAut)"'
    fields = sorted(set(re.findall(field_pattern, body)))
    print(f"[pdf] field names found in the JSON example: {len(fields)} {fields}")
    url_pattern = r"\{URL\}=\S+"
    print(f"[pdf] URL definition: {re.findall(url_pattern, body)}")
    print(f"[pdf] scope sentence present: {'Comprobantes en l' in body and 'Facturador' in body}")
    print("written: data/interim/research/arca_qr_spec.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
