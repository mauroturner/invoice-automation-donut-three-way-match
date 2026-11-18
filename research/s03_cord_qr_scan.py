"""Does CORD contain any QR code? Scans all 1,000 receipt images with OpenCV's QRCodeDetector.

Images are downscaled so the longest side is at most 1280 px before detection (a receipt QR code is large
at that size; the scan is a presence check, not a benchmark). Also searches the annotations for the words
QR, ARCA and AFIP. Writes data/interim/research/cord_qr_scan.json.
Run from any directory:  python -I research/s03_cord_qr_scan.py
"""
import io
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pyarrow.parquet as pq
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "data" / "raw" / "cord"
OUT = PROJECT / "data" / "interim" / "research"
OUT.mkdir(parents=True, exist_ok=True)
MAX_SIDE = 1280


def to_gray_array(img_bytes):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    scale = min(1.0, MAX_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def main():
    detector = cv2.QRCodeDetector()
    shards = sorted(RAW.glob("*.parquet"))
    total, qr_found, decoded, keyword_hits = 0, 0, 0, {"QR": 0, "ARCA": 0, "AFIP": 0}
    examples = []
    detected_at, decoded_at = [], []
    index = 0
    t0 = time.perf_counter()
    for path in shards:
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=25, columns=["image", "ground_truth"]):
            for img, gt in zip(batch.column("image").to_pylist(), batch.column("ground_truth").to_pylist()):
                total += 1
                index = total - 1
                text_blob = gt.upper()
                for k in keyword_hits:
                    if k in text_blob:
                        keyword_hits[k] += 1
                frame = to_gray_array(img["bytes"])
                data, pts, _ = detector.detectAndDecode(frame)
                if pts is not None:
                    qr_found += 1
                    detected_at.append(index)
                if data:
                    decoded += 1
                    decoded_at.append({"index": index, "payload_chars": len(data), "payload_prefix": data[:6]})
                    if len(examples) < 3:
                        examples.append(data[:80])
    elapsed = round(time.perf_counter() - t0, 1)
    result = {
        "receipts_scanned": total,
        "qr_patterns_detected": qr_found,
        "qr_payloads_decoded": decoded,
        "keyword_receipts_in_annotations": keyword_hits,
        "max_side_px": MAX_SIDE,
        "seconds": elapsed,
        "decoded_examples": examples,
        "detected_indices": detected_at,
        "decoded_at": decoded_at,
    }
    (OUT / "cord_qr_scan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"receipts scanned: {total}")
    print(f"QR patterns detected: {qr_found}; payloads decoded: {decoded}")
    print(f"annotation keyword hits (receipts): {keyword_hits}")
    print(f"seconds: {elapsed}")
    print("written: data/interim/research/cord_qr_scan.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
