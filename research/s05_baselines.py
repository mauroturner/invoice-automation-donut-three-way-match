"""Cheap baselines for CORD field extraction, scored with the same leaf recall as s02.

  - always-empty: predicts no field (leaf recall 0 by construction; the TED-based accuracy is 0 as well).
  - majority-value: for each field path, predicts the most frequent normalised value seen in the TRAIN split
    (fitted on train only, scored on the 100 test receipts).
  - regex over annotated words: an oracle-OCR upper bound. The annotated word texts are joined per receipt
    in reading order and a regex looks for the total after the word TOTAL. This is NOT a real OCR baseline: no OCR
    engine is installed (Tesseract is absent and docTR is not installed in this environment).

Writes data/interim/research/baselines.json. Run from any directory:  python -I research/s05_baselines.py
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from s01_structure import parse_money  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "data" / "raw" / "cord"
OUT = PROJECT / "data" / "interim" / "research"


def normalise(value):
    if value is None:
        return ""
    s = str(value).strip()
    num = parse_money(s)
    if num is not None:
        return f"{num:.2f}"
    return re.sub(r"\s+", " ", s).lower()


def leaves(obj, prefix="", out=None):
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            leaves(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(obj, list):
        for v in obj:
            leaves(v, f"{prefix}[]", out)
    else:
        out.append((prefix, normalise(obj)))
    return out


def read_split(name):
    files = sorted(RAW.glob(f"{name}-*.parquet"))
    rows = []
    for path in files:
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=50, columns=["ground_truth"]):
            for raw in batch.column("ground_truth").to_pylist():
                rows.append(json.loads(raw))
    return rows


def total_from_words(gt):
    """Read the annotated words of one receipt in line order and take the number after the word TOTAL."""
    lines = []
    for line in gt.get("valid_line", []):
        words = [w.get("text", "") for w in line.get("words", [])]
        lines.append(" ".join(words))
    text = "\n".join(lines)
    m = re.search(r"TOTAL[^\n0-9]*([0-9][0-9.,]*)", text.upper())
    return normalise(m.group(1)) if m else ""


def main():
    train = read_split("train")
    test = read_split("test")
    mode = defaultdict(Counter)
    for gt in train:
        for path, val in leaves(gt["gt_parse"]):
            mode[path][val] += 1
    majority = {p: c.most_common(1)[0][0] for p, c in mode.items()}
    print(f"[fit] train receipts {len(train)}; field paths with a majority value: {len(majority)}")

    gt_leaves_total = 0
    majority_hits = 0
    per_path_total, per_path_hit = Counter(), Counter()
    regex_total_hits = 0
    regex_total_present = 0
    for gt in test:
        g = leaves(gt["gt_parse"])
        gt_leaves_total += len(g)
        for path, val in g:
            per_path_total[path] += 1
            if majority.get(path) == val:
                majority_hits += 1
                per_path_hit[path] += 1
        tp = normalise(gt["gt_parse"].get("total", {}).get("total_price")) if isinstance(gt["gt_parse"].get("total"), dict) else ""
        if tp:
            regex_total_present += 1
            if total_from_words(gt) == tp:
                regex_total_hits += 1

    always_empty = 0.0
    result = {
        "test_receipts": len(test),
        "test_gt_leaves": gt_leaves_total,
        "always_empty_leaf_recall_pct": always_empty,
        "majority_leaf_recall_pct": round(majority_hits / gt_leaves_total * 100, 2),
        "majority_hits": majority_hits,
        "regex_oracle_total_exact_pct": round(regex_total_hits / max(1, regex_total_present) * 100, 2),
        "regex_oracle_total_hits": regex_total_hits,
        "regex_oracle_total_present": regex_total_present,
        "majority_top_fields": {p: round(per_path_hit[p] / per_path_total[p] * 100, 1)
                                for p, _ in per_path_total.most_common(8)},
    }
    print(f"[always-empty] leaf recall {always_empty} % (TED accuracy 0 by construction)")
    print(f"[majority] test leaf recall {result['majority_leaf_recall_pct']} % ({majority_hits}/{gt_leaves_total})")
    for p, v in result["majority_top_fields"].items():
        print(f"  {p}: {v} %")
    print(f"[regex, oracle words] total exact match {result['regex_oracle_total_exact_pct']} % "
          f"({regex_total_hits}/{regex_total_present} test receipts with a parseable total)")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "baselines.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("written: data/interim/research/baselines.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
