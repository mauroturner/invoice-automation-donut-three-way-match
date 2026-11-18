"""Planner check 1: the published Donut CORD checkpoint on the CORD **validation** split, and target lengths.

Reads only the train and validation shards of CORD v2 (never the test shard, which stays sealed for the final
evaluation; docs/research.md already ran this checkpoint on it, see docs/decisions.md D11).

1. The checkpoint naver-clova-ix/donut-base-finetuned-cord-v2 (revision pinned as in research/s02) on all 100
   validation receipts, GPU, fp32, one at a time: TED-based accuracy, leaf recall, per-field recall, exact
   receipts and seconds per receipt, with the same metric code as research/s02 (imported, not copied).
2. The length in tokens of every train and validation target sequence (Donut's json2token of gt_parse, with the
   checkpoint's tokenizer, plus the task prompt and the end token), to size the decoder for fine-tuning.

Writes data/interim/plan/p01.json (git-ignored); the printed summary is versioned as research/plan/p01.out.
Run from the project folder:  .venv\\Scripts\\python -I research/plan/p01_cord_validation.py
"""
import json
import statistics
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq
import torch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "research"))
from s02_donut import TASK_PROMPT, leaf_match, load_model, normalise, predict, ted_accuracy  # noqa: E402

RAW = PROJECT / "data" / "raw" / "cord"
OUT = PROJECT / "data" / "interim" / "plan"
OUT.mkdir(parents=True, exist_ok=True)
VALIDATION = RAW / "validation-00000-of-00001-cc3c5779fe22e8ca.parquet"
TRAIN = sorted(RAW.glob("train-*.parquet"))


def json2token(obj):
    """Donut's target serialisation: dict keys as <s_k>...</s_k> in reverse-sorted order, lists joined by <sep/>."""
    if isinstance(obj, dict):
        return "".join(f"<s_{k}>{json2token(obj[k])}</s_{k}>" for k in sorted(obj, reverse=True))
    if isinstance(obj, list):
        return "<sep/>".join(json2token(v) for v in obj)
    return str(obj)


def rows(path, with_images=True):
    cols = ["image", "ground_truth"] if with_images else ["ground_truth"]
    table = pq.read_table(path, columns=cols)
    gts = [json.loads(g) for g in table.column("ground_truth").to_pylist()]
    imgs = table.column("image").to_pylist() if with_images else [None] * len(gts)
    return [(i, (imgs[i] or {}).get("bytes"), gts[i]) for i in range(len(gts))]


def main():
    assert "test" not in VALIDATION.name
    result = {"split": "validation", "test_shard_read": False}
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    processor, model = load_model(dev)
    val = rows(VALIDATION)
    for _, b, _ in val[:3]:
        predict(processor, model, dev, b)  # warm-up, not counted
    ted, times, exact, m_all, t_all = [], [], 0, 0, 0
    f_tot, f_ok = {}, {}
    for _, b, gt in val:
        t0 = time.perf_counter()
        pred = predict(processor, model, dev, b)
        if dev.type == "cuda":
            torch.cuda.synchronize()
        times.append(time.perf_counter() - t0)
        g = gt["gt_parse"]
        ted.append(ted_accuracy(pred, g))
        m, t, per = leaf_match(pred, g)
        m_all += m
        t_all += t
        exact += int(normalise(json.dumps(pred, sort_keys=True)) == normalise(json.dumps(g, sort_keys=True)))
        for p, (tt, oo) in per.items():
            f_tot[p] = f_tot.get(p, 0) + tt
            f_ok[p] = f_ok.get(p, 0) + oo
    result["donut_checkpoint_validation"] = {
        "device": dev.type,
        "receipts": len(val),
        "ted_accuracy_mean": round(statistics.mean(ted) * 100, 2),
        "ted_accuracy_median": round(statistics.median(ted) * 100, 2),
        "leaf_recall": round(m_all / t_all * 100, 2),
        "leaves_matched": m_all,
        "leaves_total": t_all,
        "exact_match_receipts": exact,
        "seconds_per_receipt_mean": round(statistics.mean(times), 3),
        "field_recall": {p: round(f_ok[p] / f_tot[p] * 100, 1) for p in sorted(f_tot) if f_tot[p] >= 20},
    }

    tok = processor.tokenizer
    lengths = {}
    for split, paths in (("train", TRAIN), ("validation", [VALIDATION])):
        ls = []
        for path in paths:
            for _, _, gt in rows(path, with_images=False):
                ids = tok(TASK_PROMPT + json2token(gt["gt_parse"]) + tok.eos_token, add_special_tokens=False).input_ids
                ls.append(len(ids))
        ls.sort()
        lengths[split] = {"n": len(ls), "median": statistics.median(ls), "p99": ls[int(0.99 * (len(ls) - 1))],
                          "max": ls[-1], "over_768": sum(x > 768 for x in ls)}
    result["target_tokens"] = lengths
    (OUT / "p01.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    d = result["donut_checkpoint_validation"]
    print(f"[p01] CORD validation, checkpoint unchanged, {d['receipts']} receipts on {d['device']}:")
    print(f"  TED accuracy mean {d['ted_accuracy_mean']} % (median {d['ted_accuracy_median']}), leaf recall "
          f"{d['leaf_recall']} % ({d['leaves_matched']}/{d['leaves_total']}), exact receipts {d['exact_match_receipts']}, "
          f"{d['seconds_per_receipt_mean']} s per receipt")
    print("  field recall (fields with >= 20 leaves): " + ", ".join(f"{k} {v}" for k, v in d["field_recall"].items()))
    for split, s in lengths.items():
        print(f"[p01] target tokens {split}: n {s['n']}, median {s['median']}, p99 {s['p99']}, max {s['max']}, "
              f"over 768: {s['over_768']}")
    print("[p01] the test shard was not opened")
    return 0


if __name__ == "__main__":
    sys.exit(main())
