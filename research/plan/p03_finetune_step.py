"""Planner check 3: what one fine-tuning step of Donut costs on this machine (time and GPU memory).

Loads the published CORD checkpoint (the same architecture the project fine-tunes), takes the first 24 receipts of
the first CORD **train** shard (never the test shard), builds Donut's targets with json2token, and times training
steps (forward, backward and AdamW) at the checkpoint's input size (1280 x 960), with and without bf16 autocast,
for batch sizes 1, 2 and 4. Three warm-up steps, then ten timed steps. Nothing is saved; the weights are discarded.

Writes data/interim/plan/p03.json (git-ignored); the summary is versioned as research/plan/p03.out.
Run from the project folder:  .venv\\Scripts\\python -I research/plan/p03_finetune_step.py
"""
import io
import json
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq
import torch
from PIL import Image

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "research"))
sys.path.insert(0, str(PROJECT / "research" / "plan"))
from p01_cord_validation import json2token  # noqa: E402
from s02_donut import TASK_PROMPT, load_model  # noqa: E402

RAW = PROJECT / "data" / "raw" / "cord"
OUT = PROJECT / "data" / "interim" / "plan"
OUT.mkdir(parents=True, exist_ok=True)
SHARD = sorted(RAW.glob("train-*.parquet"))[0]


def main():
    assert "train" in SHARD.name
    if not torch.cuda.is_available():
        print("[p03] no CUDA device: nothing measured")
        return 1
    dev = torch.device("cuda")
    pf = pq.ParquetFile(SHARD)
    batch = next(pf.iter_batches(batch_size=24, columns=["image", "ground_truth"]))
    imgs = [Image.open(io.BytesIO(x["bytes"])).convert("RGB") for x in batch.column("image").to_pylist()]
    gts = [json.loads(g)["gt_parse"] for g in batch.column("ground_truth").to_pylist()]
    processor, model = load_model(dev)
    tok = processor.tokenizer
    model.config.decoder_start_token_id = tok.convert_tokens_to_ids(TASK_PROMPT)
    model.config.pad_token_id = tok.pad_token_id
    pixel = processor(imgs, return_tensors="pt").pixel_values
    texts = [json2token(g) + tok.eos_token for g in gts]
    enc = tok(texts, add_special_tokens=False, padding="max_length", max_length=384, truncation=True,
              return_tensors="pt").input_ids
    labels = enc.clone()
    labels[labels == tok.pad_token_id] = -100
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=3e-5)
    result = {"shard": SHARD.name, "input_size": list(pixel.shape[-2:]), "max_target_tokens": 384,
              "gpu": torch.cuda.get_device_name(0), "runs": {}}
    for amp in (False, True):
        for bs in (1, 2, 4):
            torch.cuda.reset_peak_memory_stats()
            times = []
            for step in range(13):
                i0 = (step * bs) % (24 - bs)
                pv = pixel[i0:i0 + bs].to(dev)
                lb = labels[i0:i0 + bs].to(dev)
                torch.cuda.synchronize(); t0 = time.perf_counter()
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp):
                    loss = model(pixel_values=pv, labels=lb).loss
                loss.backward()
                opt.step(); opt.zero_grad(set_to_none=True)
                torch.cuda.synchronize()
                if step >= 3:
                    times.append(time.perf_counter() - t0)
            key = f"{'bf16' if amp else 'fp32'}_batch{bs}"
            s = sum(times) / len(times)
            result["runs"][key] = {"seconds_per_step": round(s, 3), "seconds_per_image": round(s / bs, 3),
                                   "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
                                   "last_loss": round(float(loss), 3)}
    (OUT / "p03.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[p03] Donut training steps on {result['gpu']}, input {result['input_size']}, targets padded to 384 tokens, "
          f"24 CORD train receipts")
    for key, r in result["runs"].items():
        print(f"[p03] {key}: {r['seconds_per_step']} s per step, {r['seconds_per_image']} s per image, "
              f"peak {r['peak_gpu_gb']} GB allocated")
    print("[p03] the test shard was not opened; no weights were saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
