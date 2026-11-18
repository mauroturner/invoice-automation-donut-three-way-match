"""ARCA electronic-invoice QR: spec check, synthetic CUITs, encode with qrcode, decode with OpenCV and pyzbar.

1. Reproduces the worked example of the ARCA QR specification (QRespecificaciones.pdf, version 1 of the data):
   the JSON in the PDF, its Base64 and the URL must match byte for byte.
2. Generates synthetic issuer CUITs with a valid check digit (AFIP modulus-11 rule). They are fake: the
   Espacio de diálogo assigns homologation CUITs, and no public list of them exists (ARCA FAQ). The prefix 30 and
   the low serial numbers are a convention for synthetic data, not a guarantee that no real taxpayer has them.
3. Encodes N synthetic payloads with the qrcode library (error correction M) and decodes them with
   OpenCV QRCodeDetector and, if its native zbar library loads, pyzbar, under clean and degraded conditions.
   Success = the decoded text equals the encoded URL exactly.

Writes data/interim/research/arca_qr_eval.json and sample images under data/interim/research/qr_samples/.
Run from any directory:  python -I research/s04_arca_qr.py
"""
import base64
import io
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import qrcode
from importlib.metadata import version as pkg_version
from PIL import Image, ImageEnhance, ImageFilter

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "data" / "interim" / "research"
SAMPLES = OUT / "qr_samples"
SAMPLES.mkdir(parents=True, exist_ok=True)
SPEC_URL = "https://www.arca.gob.ar/fe/qr/"
SPEC_EXAMPLE_JSON = ('{"ver":1,"fecha":"2020-10-13","cuit":30000000007,"ptoVta":10,"tipoCmp":1,"nroCmp":94,'
                     '"importe":12100,"moneda":"DOL","ctz":65,"tipoDocRec":80,"nroDocRec":20000000001,'
                     '"tipoCodAut":"E","codAut":70417054367476}')
SPEC_EXAMPLE_B64 = ("eyJ2ZXIiOjEsImZlY2hhIjoiMjAyMC0xMC0xMyIsImN1aXQiOjMwMDAwMDAwMDA3LCJwdG9WdGEiOjEwLCJ0aXBvQ21wIjox"
                    "LCJucm9DbXAiOjk0LCJpbXBvcnRlIjoxMjEwMCwibW9uZWRhIjoiRE9MIiwiY3R6Ijo2NSwidGlwb0RvY1JlYyI6ODAsIm5yb0Rv"
                    "Y1JlYyI6MjAwMDAwMDAwMDEsInRpcG9Db2RBdXQiOiJFIiwiY29kQXV0Ijo3MDQxNzA1NDM2NzQ3Nn0=")
SPEC_EXAMPLE_URL = "https://www.afip.gob.ar/fe/qr/?p=" + SPEC_EXAMPLE_B64  # host as printed in the spec example
SEED = 20261118
N_PER_CONDITION = 40


def cuit_check_digit(first10):
    """AFIP modulus-11 rule: weights 5,4,3,2,7,6,5,4,3,2; 11 - (sum mod 11), with 11 mapped to 0.
    Returns 10 when no valid check digit exists for that prefix."""
    weights = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    s = sum(int(d) * w for d, w in zip(first10, weights))
    r = 11 - (s % 11)
    return 0 if r == 11 else r


def synthetic_cuit(serial):
    """Prefix 30 (legal entity) + 8-digit serial + check digit. Returns None when the rule gives 10 (no CUIT)."""
    base = f"30{serial:08d}"
    cd = cuit_check_digit(base)
    if cd == 10:
        return None
    return base + str(cd)


def valid_cuit(cuit):
    return len(cuit) == 11 and cuit.isdigit() and cuit_check_digit(cuit[:10]) == int(cuit[10])


def build_payload(rng, cuit, serial):
    data = {
        "ver": 1,
        "fecha": f"2026-{rng.randint(1, 11):02d}-{rng.randint(1, 28):02d}",
        "cuit": int(cuit),
        "ptoVta": rng.randint(1, 9999),
        "tipoCmp": rng.choice([1, 6, 11]),
        "nroCmp": serial,
        "importe": round(rng.uniform(1000, 900000), 2),
        "moneda": "PES",
        "ctz": 1,
        "tipoCodAut": "E",
        "codAut": rng.randint(10**13, 10**14 - 1),
    }
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return SPEC_URL + "?p=" + base64.b64encode(raw.encode("utf-8")).decode("ascii"), data


def make_qr(text, box_size=10):
    """Error correction M. Returns (image, QR version, modules per side)."""
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    return img, qr.version, qr.modules_count


def degrade(img, kind, rng):
    if kind in ("clean", "clean_box20"):
        return img
    if kind == "jpeg_q30":
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=30)
        return Image.open(io.BytesIO(buf.getvalue())).convert("L")
    if kind == "blur_r2":
        return img.filter(ImageFilter.GaussianBlur(radius=2))
    if kind == "rotate_25":
        return img.rotate(25, expand=True, fillcolor=255)
    if kind == "small_160px":
        return img.resize((160, 160), Image.LANCZOS)
    if kind == "dim_low_contrast":
        return ImageEnhance.Contrast(ImageEnhance.Brightness(img).enhance(0.55)).enhance(0.45)
    if kind == "salt_noise":
        arr = np.asarray(img).copy()
        mask = np.random.default_rng(rng.randint(0, 2**31)).random(arr.shape) < 0.02
        arr[mask] = 0
        return Image.fromarray(arr)
    raise ValueError(kind)


CONDITIONS = ["clean", "clean_box20", "jpeg_q30", "blur_r2", "rotate_25", "small_160px", "dim_low_contrast", "salt_noise"]


def decode_opencv(img, detector):
    arr = np.asarray(img.convert("L"))
    data, pts, _ = detector.detectAndDecode(arr)
    return data if data else None


def decode_opencv_aruco(img, detector):
    arr = np.asarray(img.convert("L"))
    data, pts, _ = detector.detectAndDecode(arr)
    return data if data else None


def decode_pyzbar(img, pz):
    res = pz.decode(img.convert("L"))
    return res[0].data.decode("utf-8") if res else None


def main():
    # 1. Spec example
    json_ok = json.loads(SPEC_EXAMPLE_JSON)["ver"] == 1
    b64_ok = base64.b64encode(SPEC_EXAMPLE_JSON.encode("utf-8")).decode("ascii") == SPEC_EXAMPLE_B64
    url_ok = SPEC_EXAMPLE_URL == "https://www.afip.gob.ar/fe/qr/?p=" + SPEC_EXAMPLE_B64
    spec = {"json_parses": json_ok, "base64_matches_pdf_example": b64_ok, "example_fields": len(json.loads(SPEC_EXAMPLE_JSON)),
            "url_host_in_example": "www.afip.gob.ar", "url_host_in_spec_definition": "www.arca.gob.ar",
            "url_example_consistent": url_ok}
    print("[spec]", spec)
    print(f"[spec] check digit of 30000000007 valid: {valid_cuit('30000000007')}")

    # 2. Synthetic CUITs
    rng = random.Random(SEED)
    cuits = []
    serial = 1
    while len(cuits) < 40:
        c = synthetic_cuit(serial)
        if c is not None:
            cuits.append(c)
        serial += 1
    all_valid = all(valid_cuit(c) for c in cuits)
    print(f"[cuit] {len(cuits)} synthetic CUITs generated, all with valid check digit: {all_valid}; first {cuits[:3]}")

    # 3. Encode and decode
    detector = cv2.QRCodeDetector()
    aruco = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else None
    try:
        from pyzbar import pyzbar as pz
        pz.decode(Image.new("L", (8, 8), 255))
        pyzbar_ok, pyzbar_note = True, "loaded"
    except Exception as exc:  # zbar native library missing on Windows is the usual failure
        pz, pyzbar_ok, pyzbar_note = None, False, f"{type(exc).__name__}: {str(exc)[:160]}"
    print(f"[pyzbar] {pyzbar_note}")

    results = {c: {"opencv": 0, "opencv_aruco": 0, "pyzbar": 0, "n": 0} for c in CONDITIONS}
    versions = []
    payload_check = {"payloads": 0, "base64_roundtrip": 0, "json_fields_ok": 0}
    t0 = time.perf_counter()
    for i in range(N_PER_CONDITION):
        url, data = build_payload(rng, cuits[i % len(cuits)], serial=i + 1)
        payload_check["payloads"] += 1
        decoded_json = json.loads(base64.b64decode(url.split("?p=")[1]).decode("utf-8"))
        payload_check["base64_roundtrip"] += int(decoded_json == data)
        payload_check["json_fields_ok"] += int(set(decoded_json) == {"ver", "fecha", "cuit", "ptoVta", "tipoCmp",
                                                                     "nroCmp", "importe", "moneda", "ctz",
                                                                     "tipoCodAut", "codAut"})
        base, ver, modules = make_qr(url, 10)
        big, _, _ = make_qr(url, 20)
        versions.append((ver, modules))
        for cond in CONDITIONS:
            src = big if cond == "clean_box20" else base
            img = degrade(src, cond, rng)
            if i == 0:
                img.save(SAMPLES / f"{cond}.png")
            results[cond]["n"] += 1
            results[cond]["opencv"] += int(decode_opencv(img, detector) == url)
            if aruco is not None:
                results[cond]["opencv_aruco"] += int(decode_opencv_aruco(img, aruco) == url)
            if pyzbar_ok:
                results[cond]["pyzbar"] += int(decode_pyzbar(img, pz) == url)
    elapsed = round(time.perf_counter() - t0, 1)

    print(f"[payload] {payload_check}")
    print(f"{'condition':<18}{'n':>4}{'OpenCV':>10}{'OpenCV Aruco':>14}{'pyzbar':>10}")
    for cond in CONDITIONS:
        r = results[cond]
        pz_pct = f"{r['pyzbar'] / r['n'] * 100:.1f}%" if pyzbar_ok else "n/a"
        ar = f"{r['opencv_aruco'] / r['n'] * 100:.1f}%" if aruco is not None else "n/a"
        print(f"{cond:<18}{r['n']:>4}{r['opencv'] / r['n'] * 100:>9.1f}%{ar:>14}{pz_pct:>10}")
    print(f"QR version range {min(v for v, _ in versions)}-{max(v for v, _ in versions)}; modules {min(m for _, m in versions)}-{max(m for _, m in versions)}")

    sizes = []
    for u in [SPEC_EXAMPLE_URL]:
        sizes.append(len(u))
    out = {"spec": spec, "cuits": cuits[:5], "cuits_all_valid": all_valid, "pyzbar": pyzbar_note,
           "payload_check": payload_check, "results": results, "n_per_condition": N_PER_CONDITION,
           "seconds": elapsed, "example_url_length": sizes[0],
           "opencv_version": cv2.__version__, "qrcode_version": pkg_version("qrcode"), "aruco_detector": aruco is not None,
           "qr_versions": [min(v for v, _ in versions), max(v for v, _ in versions)]}
    (OUT / "arca_qr_eval.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("written: data/interim/research/arca_qr_eval.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
