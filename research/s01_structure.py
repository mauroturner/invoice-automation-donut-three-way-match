"""CORD v2 structure: counts per split, annotation schema, field presence, line items, totals, images, duplicates.

Streams the six Parquet shards in data/raw/cord/ (checked by s00 against the pinned hashes) one batch at a time,
so the 2.3 GB of images are never held in memory. Only the ground_truth JSON is parsed; images are hashed and
their size read from the annotation's meta block. Writes data/interim/research/cord_structure.json for later scripts.
Run from any directory:  python -I research/s01_structure.py
"""
import hashlib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "data" / "raw" / "cord"
OUT = PROJECT / "data" / "interim" / "research"
OUT.mkdir(parents=True, exist_ok=True)
SHARDS = {
    "train": sorted(RAW.glob("train-*.parquet")),
    "validation": sorted(RAW.glob("validation-*.parquet")),
    "test": sorted(RAW.glob("test-*.parquet")),
}
NUM = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+([.,]\d+)?$")
INDONESIAN_HINTS = ("TUNAI", "KEMBALI", "PAJAK", "TOTAL", "JUMLAH", "KASIR", "SUBTOTAL")


def flatten(obj, prefix=""):
    """Yield (path, leaf_type) for every key path of a nested JSON object. A list is reported as its own path
    (type list) and each dict element is walked under 'path[]', so keys inside repeated menu entries count too."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        yield prefix, "list"
        for item in obj:
            if isinstance(item, dict):
                yield from flatten(item, f"{prefix}[]")
            else:
                yield f"{prefix}[]", type(item).__name__
    else:
        yield prefix, type(obj).__name__


def parse_money(text):
    """Parse an annotated amount. The annotation mixes conventions: '60.000' and 'Rp 16.500' (dot thousands,
    Indonesian style), '1,591,600' and '226,500.00' (comma thousands), '2.00' (plain decimal). Returns float or None.
    A mixed string with dots and commas is read by its last separator: digits after it of length 2 or less are decimals."""
    if not isinstance(text, str):
        return None
    t = text.replace("Rp", "").replace("RP", "").strip().replace(" ", "")
    sign = -1.0 if t.startswith("-") else 1.0
    t = t.lstrip("-")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):          # comma thousands, optional dot decimals
        value = t.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", t):         # dot thousands, optional comma decimals
        value = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+(\.\d{1,2})?", t):                   # plain integer or dot decimal
        value = t
    elif re.fullmatch(r"\d+(,\d{1,2})", t):                     # plain integer with comma decimal
        value = t.replace(",", ".")
    else:
        return None
    try:
        return sign * float(value)
    except ValueError:
        return None


def main():
    summary = {"splits": {}, "shards": {}}
    counts = Counter()
    presence = defaultdict(Counter)  # field path -> Counter of splits present
    leaf_types = defaultdict(Counter)
    categories = Counter()
    top_keys = Counter()
    meta_keys = Counter()
    sizes = []
    img_hashes = defaultdict(set)  # sha256 -> split names
    per_receipt_lines = []
    menu_lines_present = 0
    total_checks = Counter()
    indonesian_receipts = 0
    fields_per_receipt = []
    image_ids = Counter()
    empty_valid_line = 0
    total_strings = []
    gt_meta_split_mismatch = 0
    unparsed_examples = []
    menu_form_counts = Counter()
    meta_split_values = Counter()
    dup_split_pairs = Counter()

    for split, files in SHARDS.items():
        n = 0
        for path in files:
            pf = pq.ParquetFile(path)
            summary["shards"][path.name] = {"rows": pf.metadata.num_rows, "row_groups": pf.metadata.num_row_groups}
            for batch in pf.iter_batches(batch_size=50, columns=["image", "ground_truth"]):
                imgs = batch.column("image").to_pylist()
                gts = batch.column("ground_truth").to_pylist()
                for img, raw in zip(imgs, gts):
                    gt = json.loads(raw)
                    n += 1
                    img_hashes[hashlib.sha256(img["bytes"]).hexdigest()].add(split)
                    for k in gt:
                        top_keys[k] += 1
                    meta = gt.get("meta", {})
                    for k in meta:
                        meta_keys[k] += 1
                    meta_split_values[(split, meta.get("split"))] += 1
                    if meta.get("split") != split:
                        gt_meta_split_mismatch += 1
                    image_ids[meta.get("image_id")] += 1
                    size = meta.get("image_size", {})
                    sizes.append((size.get("width"), size.get("height")))
                    gp = gt.get("gt_parse", {})
                    seen = set()
                    for path_, typ in flatten(gp):
                        if path_ not in seen:
                            presence[path_][split] += 1
                        leaf_types[path_][typ] += 1
                        seen.add(path_)
                    menu_form = type(gp.get("menu")).__name__ if "menu" in gp else "absent"
                    menu_form_counts[menu_form] += 1
                    fields_per_receipt.append(len(seen))
                    vl = gt.get("valid_line", [])
                    if not vl:
                        empty_valid_line += 1
                    lines_here = 0
                    texts = []
                    for line in vl:
                        categories[line.get("category")] += 1
                        for w in line.get("words", []):
                            texts.append(w.get("text", ""))
                        if str(line.get("category", "")).startswith("menu.nm"):
                            lines_here += 1
                    menu_items = gp.get("menu", [])
                    if isinstance(menu_items, dict):
                        menu_items = [menu_items]
                    if menu_items:
                        menu_lines_present += 1
                    per_receipt_lines.append(len(menu_items))
                    joined = " ".join(texts).upper()
                    if sum(1 for h in INDONESIAN_HINTS if h in joined) >= 2:
                        indonesian_receipts += 1
                    tot = gp.get("total", {})
                    tp = parse_money(tot.get("total_price"))
                    sub = gp.get("sub_total", {})
                    if isinstance(sub, list):
                        sub = sub[0] if sub else {}
                    sp = parse_money(sub.get("subtotal_price"))
                    if sp is not None and tp is not None:
                        extra = sum(x or 0.0 for x in (
                            parse_money(sub.get("tax_price")), parse_money(sub.get("service_price")),
                            parse_money(sub.get("othersvc_price"))))
                        disc = sum(abs(x or 0.0) for x in (parse_money(sub.get("discount_price")),))
                        if abs(sp + extra - disc - tp) < 1:
                            total_checks["subtotal + tax + service - discount equals total"] += 1
                        else:
                            total_checks["subtotal + tax + service - discount differs from total"] += 1
                    if tot.get("total_price") is None:
                        total_checks["total missing"] += 1
                    elif tp is None:
                        total_checks["total not parseable"] += 1
                        if len(unparsed_examples) < 8:
                            unparsed_examples.append(tot.get("total_price"))
                    else:
                        total_checks["total parseable"] += 1
                        total_strings.append(tot.get("total_price"))
                        items = menu_items if isinstance(menu_items, list) else []
                        prices = [parse_money(m.get("price")) for m in items if isinstance(m, dict)]
                        if prices and all(p is not None for p in prices):
                            s = sum(prices)
                            if abs(s - tp) < 1:
                                total_checks["sum of menu prices equals total"] += 1
                            else:
                                total_checks["sum of menu prices differs from total"] += 1
                        else:
                            total_checks["no menu prices to compare"] += 1
            print(f"[read] {path.name}: running count {n}")
        counts[split] = n
        summary["splits"][split] = {"receipts": n}

    dupes = {h: sorted(s) for h, s in img_hashes.items() if len(img_hashes[h]) > 1}
    dup_within = sum(1 for h, s in img_hashes.items())  # unique image hashes
    total_receipts = sum(counts.values())
    print("\n== Counts")
    for split in SHARDS:
        print(f"{split}: {counts[split]} receipts")
    print(f"total: {total_receipts} receipts; unique image hashes: {dup_within}")
    print(f"image_id values: unique {len(image_ids)}, max count {max(image_ids.values())}")
    print(f"meta.split disagrees with shard split: {gt_meta_split_mismatch}")
    for (shard_split, meta_split), c in sorted(meta_split_values.items()):
        print(f"  shard {shard_split}: meta.split '{meta_split}' x {c}")
    print(f"image hashes shared across splits: {len(dupes)}")
    print(f"image hashes occurring more than once within the dataset: {total_receipts - dup_within}")

    print("\n== Top-level annotation keys (receipts with key)")
    for k, v in top_keys.items():
        print(f"{k}: {v}")
    print("meta keys:", dict(meta_keys))

    print("\n== gt_parse fields: receipts with the field, by split (train, validation, test) and type")
    for path_ in sorted(presence):
        p = presence[path_]
        total_present = sum(p.values())
        types = "/".join(sorted(leaf_types[path_]))
        print(f"{path_}: {total_present} ({total_present / total_receipts * 100:.1f}%) "
              f"[train {p.get('train', 0)}, validation {p.get('validation', 0)}, test {p.get('test', 0)}] type {types}")

    print("\n== valid_line categories (line count)")
    for cat, c in sorted(categories.items(), key=lambda x: (-x[1], str(x[0]))):
        print(f"{cat}: {c}")
    print(f"valid_line categories: {len(categories)} distinct; receipts with empty valid_line: {empty_valid_line}")

    print("\n== Line items (gt_parse.menu entries per receipt)")
    print(f"receipts with at least one menu entry: {menu_lines_present} of {total_receipts}")
    if per_receipt_lines:
        print(f"menu entries per receipt: min {min(per_receipt_lines)}, median {statistics.median(per_receipt_lines)}, "
              f"mean {statistics.mean(per_receipt_lines):.2f}, max {max(per_receipt_lines)}")

    print("\n== Totals and money (gt_parse.total.total_price, against the sum of menu prices)")
    for k in sorted(total_checks):
        print(f"{k}: {total_checks[k]}")
    print(f"examples of unparseable total_price strings: {unparsed_examples}")
    print(f"gt_parse.menu form: {dict(menu_form_counts)}")

    print("\n== Image sizes (meta.image_size, pixels)")
    ws = [w for w, _ in sizes if w]
    hs = [h for _, h in sizes if h]
    print(f"width: min {min(ws)}, median {statistics.median(ws)}, max {max(ws)}")
    print(f"height: min {min(hs)}, median {statistics.median(hs)}, max {max(hs)}")
    print(f"distinct sizes: {len(set(sizes))}")

    print("\n== Language heuristic (receipts with two or more Indonesian keywords in their words)")
    print(f"receipts: {indonesian_receipts} of {total_receipts} ({indonesian_receipts / total_receipts * 100:.1f}%)")

    summary.update({
        "counts": dict(counts), "total": total_receipts, "unique_images": dup_within,
        "shared_image_hashes": len(dupes), "categories": dict(categories),
        "fields": {p: sum(presence[p].values()) for p in presence},
        "menu_entries_median": statistics.median(per_receipt_lines) if per_receipt_lines else None,
        "total_checks": dict(total_checks), "indonesian_receipts": indonesian_receipts,
        "image_size_median": [statistics.median(ws), statistics.median(hs)],
    })
    (OUT / "cord_structure.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nwritten: data/interim/research/cord_structure.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
