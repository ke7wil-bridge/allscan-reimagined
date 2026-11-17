#!/usr/bin/env python3
"""Prove URF reflector support is absent from shipped ASR code (#177).

Only deliberate retirement handling may mention the retired reflector: the
legacy-card filters and migration, the Global Ban list-name migration and its
read-only compatibility aliases, legacy roster-key readers, and the refusal
message. M17 reflectors that happen to be named URF... are M17, not URF.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORD = re.compile(r"urf", re.I)
BENIGN = re.compile(r"surface", re.I)

# path -> regexes that a URF-mentioning line must match to be allowed
ALLOWED = {
    "asr-api.php": [r"urfReflector'\]\)", r"=== 'urf'", r"URF reflector bridges were retired",
                    r"Retired URF reflector cards", r"URF grouping flag"],
    "server/asr-api.php": [r"urfReflector'\]\)", r"=== 'urf'", r"URF reflector bridges were retired",
                           r"Retired URF reflector cards", r"URF grouping flag"],
    "scripts/asr-bridge-setup-helper.py": [r"URF reflector bridges (were retired|are no longer supported)"],
    "scripts/asr-reapply.sh": [r"urf", r"URF"],  # list-name migration, aliases, URF card retirement
    "docker/layered-reapply.sh": [r"urf-admin", r"URF name", r"urfd\.blacklist"],
    "scripts/asr-dmr-standalone-admin-adapter.py": [r'"urf_dmr"'],
    "scripts/bridge-relays/asr-tgif-standalone-relay.py": [r'"urf_dmr"', r"No URF dependency"],
    "scripts/asr-tgif-user-session.py": [r"asr tgif to urf bri", r"ASR TGIF to URF bri"],
    "scripts/asr-m17-usrp-connector.py": [r"URF921", r"urf_outbound"],
    "scripts/asr-m17-hosts-update.py": [r"URF records"],
}
SETTINGS = [r"URF reflector bridges are no longer supported", r"Retired URF reflector cards",
            r"urfReflector", r"=== 'urf'"]
SKIP_PREFIXES = ("release-notes/", "docs/", "qa/", "CODEX_HANDOFF_147.md", "public/img/", "public/favicon")


def shipped_files() -> list[str]:
    listing = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [path for path in listing.splitlines()
            if not path.startswith(SKIP_PREFIXES) and "self-test" not in path
            and not path.endswith((".png", ".gif", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".md"))]


failures = []
for path in shipped_files():
    if WORD.search(Path(path).name):
        failures.append(f"URF-named file is still shipped: {path}")
        continue
    try:
        text = (ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        continue
    allowed = ALLOWED.get(path, SETTINGS if path.endswith("asr-settings/settings-controller.php") else [])
    for number, line in enumerate(text.splitlines(), 1):
        if not WORD.search(BENIGN.sub("", line)):
            continue
        if any(re.search(pattern, line) for pattern in allowed):
            continue
        failures.append(f"{path}:{number}: {line.strip()[:120]}")

for directory in ("runtime/urf",):
    if (ROOT / directory).exists():
        failures.append(f"retired runtime directory still present: {directory}")
build = (ROOT / "build-release.sh").read_text(encoding="utf-8")
if "runtime/dmr" not in build or "asr-global-ban.py" not in build:
    failures.append("release does not package the direct DMR runtime and Global Ban helper")

if failures:
    raise SystemExit("URF absence check failed:\n  " + "\n  ".join(failures[:60]))
print("URF reflector absence self-test: ok")
