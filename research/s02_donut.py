"""Donut (naver-clova-ix/donut-base-finetuned-cord-v2, MIT) on a seeded sample of the CORD v2 test split.

Measures, on the GPU (fp32) and on the CPU (fp32, fewer receipts):
  - TED-based accuracy per receipt: 1 - TED(pred, gt) / TED(empty, gt), clipped at 0, averaged over receipts.
    This is our reimplementation of the metric the Donut paper reports as tree-edit-distance accuracy, with the
    zss library (Zhang-Shasha). It is not the authors' evaluation script.
  - Leaf recall: share of ground-truth (field path, value) leaves reproduced exactly after normalisation.
  - Time per receipt (preprocessing, generation and decoding), after warm-up.
The checkpoint was fine-tuned on CORD; its card does not name the split, so claims about the test split
assume it was not in training (see docs/research.md). Writes data/interim/research/donut_eval.json and the
per-receipt predictions to data/interim/research/donut_predictions.jsonl (both git-ignored).

Run from any directory:  python -I research/s02_donut.py --gpu 100 --cpu 10
"""
import argparse
import io
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq
import torch
from PIL import Image
from transformers import DonutProcessor, VisionEncoderDecoderModel
from zss import Node, simple_distance

sys.path.insert(0, str(Path(__file__).resolve().parent))
from s01_structure import parse_money  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "data" / "raw" / "cord"
OUT = PROJECT / "data" / "interim" / "research"
OUT.mkdir(parents=True, exist_ok=True)
MODEL = "naver-clova-ix/donut-base-finetuned-cord-v2"
REVISION = "8003d433113256b4ce3a0f5bf604b29ff78a7451"  # main as of the access date 2026-11-18
TASK_PROMPT = "<s_cord-v2>"
SEED = 20261118


def load_test_rows():
    """All 100 test receipts (the shard is 234 MB): image bytes and the parsed ground truth."""
    table = pq.read_table(RAW / "test-00000-of-00001-9c204eb3f4e11791.parquet")
    images = table.column("image").to_pylist()
    gts = [json.loads(g) for g in table.column("ground_truth").to_pylist()]
    return [(i, images[i]["bytes"], gts[i]) for i in range(len(gts))]


def to_tree(obj, label="root"):
    """Ordered tree: dict keys are nodes, list items are nodes labelled 'item', scalar values are leaves."""
    node = Node(label)
    if isinstance(obj, dict):
        for k, v in obj.items():
            node.addkid(to_tree(v, str(k)))
    elif isinstance(obj, list):
        for v in obj:
            node.addkid(to_tree(v, "item"))
    else:
        node.addkid(Node(normalise(obj)))
    return node


def normalise(value):
    """Lower-case, whitespace-collapsed string; numbers in the annotation's money format become canonical numbers."""
    if value is None:
        return ""
    s = str(value).strip()
    num = parse_money(s)
    if num is not None:
        return f"{num:.2f}"
    return re.sub(r"\s+", " ", s).lower()


def ted_accuracy(pred, gt):
    t_gt = to_tree(gt)
    t_pred = to_tree(pred)
    t_empty = Node("root")
    denom = simple_distance(t_empty, t_gt)
    if denom == 0:
        return 1.0
    return max(0.0, 1.0 - simple_distance(t_pred, t_gt) / denom)


def leaves(obj, prefix="", out=None):
    """(path, normalised value) for every scalar leaf. Repeated list items share a path; occurrence counts
    keep them distinct: the k-th occurrence of a path is keyed (path, k)."""
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


def leaf_match(pred, gt):
    """Return (matched, total, per_path) comparing the ground-truth leaves against the prediction."""
    pool = {}
    for path, val in leaves(pred):
        pool.setdefault((path, val), 0)
        pool[(path, val)] += 1
    matched = 0
    per_path = {}
    for path, val in leaves(gt):
        hit = pool.get((path, val), 0) > 0
        if hit:
            pool[(path, val)] -= 1
            matched += 1
        tot, ok = per_path.get(path, (0, 0))
        per_path[path] = (tot + 1, ok + int(hit))
    return matched, len(leaves(gt)), per_path


def load_model(device):
    processor = DonutProcessor.from_pretrained(MODEL, revision=REVISION)
    model = VisionEncoderDecoderModel.from_pretrained(MODEL, revision=REVISION).to(device)
    model.eval()
    return processor, model


def predict(processor, model, device, image_bytes):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    pixel_values = processor(img, return_tensors="pt").pixel_values.to(device)
    dec_ids = processor.tokenizer(TASK_PROMPT, add_special_tokens=False, return_tensors="pt").input_ids.to(device)
    with torch.inference_mode():
        out = model.generate(
            pixel_values,
            decoder_input_ids=dec_ids,
            max_length=model.decoder.config.max_position_embeddings,
            early_stopping=True,
            pad_token_id=processor.tokenizer.pad_token_id,
            eos_token_id=processor.tokenizer.eos_token_id,
            use_cache=True,
            num_beams=1,
            bad_words_ids=[[processor.tokenizer.unk_token_id]],
            return_dict_in_generate=True,
        )
    seq = processor.batch_decode(out.sequences)[0]
    seq = seq.replace(processor.tokenizer.eos_token, "").replace(processor.tokenizer.pad_token, "")
    seq = re.sub(r"<.*?>", "", seq, count=1).strip()
    return processor.token2json(seq)


def run(processor, model, device, rows, label, out_lines):
    ted, leaf_m, leaf_t, times, exact = [], 0, 0, [], 0
    field_tot, field_ok = {}, {}
    for idx, image_bytes, gt in rows:
        t0 = time.perf_counter()
        pred = predict(processor, model, device, image_bytes)
        if device.type == "cuda":
            torch.cuda.synchronize()
        times.append(time.perf_counter() - t0)
        gt_parse = gt["gt_parse"]
        ted.append(ted_accuracy(pred, gt_parse))
        m, t, per_path = leaf_match(pred, gt_parse)
        leaf_m += m
        leaf_t += t
        exact += int(normalise(json.dumps(pred, sort_keys=True)) == normalise(json.dumps(gt_parse, sort_keys=True)))
        for p, (tt, oo) in per_path.items():
            field_tot[p] = field_tot.get(p, 0) + tt
            field_ok[p] = field_ok.get(p, 0) + oo
        out_lines.append(json.dumps({"device": label, "index": idx, "pred": pred, "gt_parse": gt_parse,
                                     "ted_accuracy": round(ted[-1], 4), "leaf_matched": m, "leaf_total": t}, ensure_ascii=False))
    n = len(rows)
    return {
        "receipts": n,
        "ted_accuracy_mean": round(statistics.mean(ted) * 100, 2),
        "ted_accuracy_median": round(statistics.median(ted) * 100, 2),
        "leaf_recall": round(leaf_m / leaf_t * 100, 2),
        "leaves_matched": leaf_m, "leaves_total": leaf_t,
        "exact_match_receipts": exact,
        "seconds_per_receipt_mean": round(statistics.mean(times), 3),
        "seconds_per_receipt_median": round(statistics.median(times), 3),
        "field_recall": {p: round(field_ok[p] / field_tot[p] * 100, 1) for p in sorted(field_tot)},
        "field_leaves": {p: field_tot[p] for p in sorted(field_tot)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", type=int, default=100, help="receipts on the GPU (max 100)")
    ap.add_argument("--cpu", type=int, default=10, help="receipts on the CPU")
    args = ap.parse_args()

    rows = load_test_rows()
    rng = random.Random(SEED)
    chosen = sorted(rng.sample(range(len(rows)), min(args.gpu, len(rows))))
    gpu_rows = [rows[i] for i in chosen]
    cpu_rows = [rows[i] for i in chosen[: args.cpu]]
    print(f"[sample] {len(gpu_rows)} test receipts (seed {SEED}); first indices {chosen[:8]}")

    result = {"model": MODEL, "revision": REVISION, "seed": SEED, "metric": "1 - TED(pred,gt)/TED(empty,gt), zss"}
    lines = []
    if torch.cuda.is_available():
        dev = torch.device("cuda")
        t0 = time.perf_counter()
        processor, model = load_model(dev)
        result["load_seconds_gpu"] = round(time.perf_counter() - t0, 1)
        for _, b, _ in gpu_rows[:3]:  # warm-up, not counted
            predict(processor, model, dev, b)
        torch.cuda.synchronize()
        print(f"[gpu] loaded in {result['load_seconds_gpu']} s on {torch.cuda.get_device_name(0)}")
        result["gpu"] = run(processor, model, dev, gpu_rows, "cuda", lines)
        result["gpu_name"] = torch.cuda.get_device_name(0)
        result["gpu_peak_mem_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
        del model
        torch.cuda.empty_cache()

    torch.set_num_threads(os.cpu_count() or 4)
    cpu = torch.device("cpu")
    t0 = time.perf_counter()
    processor, model = load_model(cpu)
    result["load_seconds_cpu"] = round(time.perf_counter() - t0, 1)
    result["cpu_threads"] = torch.get_num_threads()
    result["cpu"] = run(processor, model, cpu, cpu_rows, "cpu", lines)

    (OUT / "donut_predictions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "donut_eval.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    for dev_key in ("gpu", "cpu"):
        if dev_key in result:
            r = result[dev_key]
            print(f"[{dev_key}] receipts {r['receipts']}: TED accuracy mean {r['ted_accuracy_mean']} % "
                  f"(median {r['ted_accuracy_median']}), leaf recall {r['leaf_recall']} % "
                  f"({r['leaves_matched']}/{r['leaves_total']}), exact receipts {r['exact_match_receipts']}, "
                  f"s/receipt mean {r['seconds_per_receipt_mean']} (median {r['seconds_per_receipt_median']})")
    print("written: data/interim/research/donut_eval.json and donut_predictions.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
