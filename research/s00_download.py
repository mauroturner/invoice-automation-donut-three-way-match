"""Download CORD v2 (Clova AI, CC BY 4.0) into data/raw/, verified by size and SHA-256.

Source: the Hugging Face dataset naver-clova-ix/cord-v2 at a pinned commit, plus the GitHub repository
clovaai/cord (README and the licence file). Standard library only, no account, no token.
The expected SHA-256 values are the LFS object ids that the Hugging Face tree API reports for each shard
at the pinned commit; the GitHub files are recorded by their own SHA-256 (no published checksum).
Run from any directory:  python -I research/s00_download.py
"""
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "data" / "raw" / "cord"
REPO = "naver-clova-ix/cord-v2"
COMMIT = "7f0115a4b758a71d6473b8d085751692da2fef98"  # main as of the access date 2026-11-18
BASE = f"https://huggingface.co/datasets/{REPO}/resolve/{COMMIT}/"
TREE_API = f"https://huggingface.co/api/datasets/{REPO}/tree/{COMMIT}/data?expand=true"
GH_RAW = "https://raw.githubusercontent.com/clovaai/cord/master/"
GH_FILES = ["README.md", "LICENSE-CC-BY"]
# (repository path, expected bytes, expected sha256 from the LFS object id)
FILES = [
    ("data/train-00000-of-00004-b4aaeceff1d90ecb.parquet", 490224630, "da3994eee1bf9bd3c57f0d53a72c3a6812c8696c5ba26245987949ddf73483cc"),
    ("data/train-00001-of-00004-7dbbe248962764c5.parquet", 441418432, "cce4def16a0d6a6c75f80be712f7494c56c318a8829b712f5c62650155c9e58e"),
    ("data/train-00002-of-00004-688fe1305a55e5cc.parquet", 443802181, "591e2db8fe8b1d364b054f46e8c375b7f00e72578914ba46a573b6858162cab2"),
    ("data/train-00003-of-00004-2d0cd200555ed7fd.parquet", 455555434, "1ffd9de8d6fbcee7630fd4cdfedff05b9b7fabc0fae4fc17557c5fe7cf178748"),
    ("data/validation-00000-of-00001-cc3c5779fe22e8ca.parquet", 242080800, "0d0f6dac11fdcc549de2746aa9f53136a3bc22a2a1aff2b0b847f7622ad60c15"),
    ("data/test-00000-of-00001-9c204eb3f4e11791.parquet", 234202795, "51c65f1788faff392abe2a0b55b023eb23e9be551c509138eaa3a832514224e7"),
]
CARD = "README.md"
UA = "research-script/1.0"


def fetch(url, dest):
    """Stream url into dest through a temporary name, then rename. Returns the byte count."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    total = 0
    with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as out:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            out.write(block)
            total += len(block)
    tmp.replace(dest)
    return total


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = {"repo": REPO, "commit": COMMIT, "license": "CC-BY-4.0", "files": [], "github": []}
    ok = True
    req = urllib.request.Request(TREE_API, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        tree = {f["path"]: f for f in json.loads(resp.read().decode("utf-8"))}
    for rel, size, sha in FILES:
        meta = tree.get(rel)
        if meta is None or meta["lfs"]["size"] != size or meta["lfs"]["oid"] != sha:
            print(f"[tree] {rel}: listing differs from the pinned values; stop")
            return 1
    print("[tree] pinned sizes and oids match the Hugging Face listing at the pinned commit")

    for rel, size, sha in FILES:
        dest = RAW / Path(rel).name
        if dest.exists() and dest.stat().st_size == size and sha256_of(dest) == sha:
            print(f"[skip] {dest.name}: already present and verified")
        else:
            n = fetch(BASE + rel, dest)
            print(f"[get ] {dest.name}: {n} bytes")
        got_size = dest.stat().st_size
        got_sha = sha256_of(dest)
        good = got_size == size and got_sha == sha
        ok = ok and good
        print(f"[check] {dest.name}: bytes {got_size} (expected {size}), sha256 {'match' if got_sha == sha else 'MISMATCH'}")
        manifest["files"].append({"path": f"data/raw/cord/{dest.name}", "repo_path": rel,
                                  "bytes": got_size, "sha256": got_sha, "verified": good})

    card = RAW / "dataset_card_README.md"
    fetch(BASE + CARD, card)
    manifest["dataset_card"] = {"path": "data/raw/cord/dataset_card_README.md",
                                "bytes": card.stat().st_size, "sha256": sha256_of(card)}
    for name in GH_FILES:
        dest = RAW / f"github_{name.replace('.', '_')}"
        fetch(GH_RAW + name, dest)
        manifest["github"].append({"path": f"data/raw/cord/{dest.name}", "source": GH_RAW + name,
                                   "bytes": dest.stat().st_size, "sha256": sha256_of(dest)})
        print(f"[gh  ] {name}: {dest.stat().st_size} bytes, sha256 {sha256_of(dest)}")
    manifest["downloaded_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (RAW / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    total = sum(f["bytes"] for f in manifest["files"])
    print(f"[total] parquet shards: {len(manifest['files'])} files, {total} bytes")
    print("all files verified" if ok else "VERIFICATION FAILED")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
