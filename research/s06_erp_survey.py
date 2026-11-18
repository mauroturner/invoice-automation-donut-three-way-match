"""Open-source ERP candidates for a local demo: licence, activity, and the size of the official container image.

Reads public metadata only (GitHub REST API, Docker Hub API). Nothing is pulled or run. Writes
data/interim/research/erp_survey.json. Run from any directory:  python -I research/s06_erp_survey.py
Licences that GitHub reports as NOASSERTION are read from the repository's LICENSE file instead.
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

try:  # use the Windows certificate store; the bundled OpenSSL store lacks the chain on this machine
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "data" / "interim" / "research"
UA = "research-script/1.0"
GITHUB = [("ERPNext", "frappe/erpnext"), ("Odoo", "odoo/odoo"), ("frappe_docker", "frappe/frappe_docker"),
          ("n8n", "n8n-io/n8n")]
DOCKER = [("ERPNext image", "frappe/erpnext", "v15"), ("Odoo image", "library/odoo", "latest")]


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main():
    out = {"github": {}, "docker": {}}
    for label, repo in GITHUB:
        meta = get_json(f"https://api.github.com/repos/{repo}")
        lic = (meta.get("license") or {}).get("spdx_id")
        entry = {"repo": repo, "license_spdx": lic, "pushed_at": meta.get("pushed_at"),
                 "stars": meta.get("stargazers_count")}
        if lic in (None, "NOASSERTION"):
            branch = meta.get("default_branch", "master")
            entry["license_file_head"] = "unavailable"
            for name in ("LICENSE", "LICENSE.md"):
                try:
                    text = get_text(f"https://raw.githubusercontent.com/{repo}/{branch}/{name}")
                except Exception as exc:  # network or missing file: recorded, not hidden
                    entry["license_file_head"] = f"unavailable: {name} {type(exc).__name__}"
                    continue
                m = re.search(r"(GNU [A-Z ]+LICENSE|Sustainable Use License|Apache License|MIT License)[^\n]*", text)
                entry["license_file_head"] = m.group(0).strip() if m else " ".join(text.split()[:12])
                entry["license_file_name"] = name
                break
        out["github"][label] = entry
        print(f"[github] {label}: {repo} license {lic} pushed {meta.get('pushed_at')} stars {meta.get('stargazers_count')}")
        if "license_file_head" in entry:
            print(f"         LICENSE head: {entry['license_file_head']}")

    for label, image, tag in DOCKER:
        data = get_json(f"https://hub.docker.com/v2/repositories/{image}/tags/{tag}")
        size = data.get("full_size")
        out["docker"][label] = {"image": image, "tag": tag, "full_size_bytes": size,
                                "last_updated": data.get("last_updated")}
        print(f"[docker] {label}: {image}:{tag} compressed size {size} bytes ({round(size / 1e6)} MB), "
              f"last updated {data.get('last_updated')}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "erp_survey.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("written: data/interim/research/erp_survey.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
