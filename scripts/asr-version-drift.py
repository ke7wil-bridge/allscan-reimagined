#!/usr/bin/env python3
"""Read-only ASR version and managed web asset drift audit (#132)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[a-z]+\.[\d.]+)?$")
API_RE = re.compile(r"const\s+ASR_VERSION\s*=\s*['\"]([^'\"]+)['\"]")
ASSET_RE = re.compile(r"""(?:src|href)=["'](?:\./)?(/?assets/[^"']+)["']""")


def audit(master: Path, web: Path) -> dict:
    checks = []
    def add(name: str, ok: bool, detail: str, action: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "action": action if not ok else ""})

    try:
        manifest = json.loads((master / "package.json").read_text(encoding="utf-8"))
        version = manifest["version"]
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise ValueError("invalid version")
        add("installed-manifest", True, version, "")
    except (OSError, ValueError, KeyError, TypeError):
        version = None
        add("installed-manifest", False, "missing or invalid package manifest", "Review the installed release; do not overwrite custom files.")

    for label, path in (("master-api", master / "server/asr-api.php"), ("web-api", web / "asr-api.php")):
        try:
            match = API_RE.search(path.read_text(encoding="utf-8"))
            actual = match.group(1) if match else None
        except OSError:
            actual = None
        add(label, version is not None and actual == version,
            "version matches" if version is not None and actual == version else "missing or mismatched version",
            "Review deployment version and installation logs before any repair.")

    try:
        html = (web / "index.html").read_text(encoding="utf-8")
        assets = sorted(set(ASSET_RE.findall(html)))
        add("web-index", True, "index present", "")
        add("asset-references", bool(assets), f"{len(assets)} referenced assets",
            "Inspect index.html asset references.")
    except OSError:
        assets = []
        add("web-index", False, "index missing", "Check whether ASR is installed.")
    for asset in assets:
        rel = asset.lstrip("/")
        if ".." in Path(rel).parts or not rel.startswith("assets/"):
            add("asset-path", False, "unsafe asset reference", "Review index.html manually.")
            continue
        expected = master / "web" / rel
        installed = web / rel
        try:
            ok = expected.is_file() and installed.is_file() and hashlib.sha256(expected.read_bytes()).digest() == hashlib.sha256(installed.read_bytes()).digest()
        except OSError:
            ok = False
        add("asset-content", ok, rel if ok else f"missing or mismatched: {rel}",
            "Compare release assets and installed files; back up before any repair.")
    return {"schema": 1, "checkedUtc": datetime.now(timezone.utc).isoformat(),
            "ok": bool(checks) and all(c["ok"] for c in checks),
            "scope": "read-only-version-and-web-assets", "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", type=Path, default=Path("/opt/allscan-reimagined/current"))
    parser.add_argument("--web", type=Path, default=Path("/var/www/html/asr"))
    args = parser.parse_args()
    result = audit(args.master, args.web)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
