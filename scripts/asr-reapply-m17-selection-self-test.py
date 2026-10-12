#!/usr/bin/env python3
"""Verify native reapply only manages M17 cards with managed backends."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    reapply = (ROOT / "scripts/asr-reapply.sh").read_text(encoding="utf-8")
    marker = "allscan-reimagined-m17-bridge@.service"
    selector_start = reapply.index("import json\nimport re\nimport sys\n", reapply.index(marker))
    selector_end = reapply.index("\nPY\n  )", selector_start)
    selector = reapply[selector_start:selector_end]
    config = {
        "bridges": [
            {"id": "display_m17", "mode": "m17", "cardType": "standard",
             "backendMode": "display_only"},
            {"id": "managed_m17", "mode": "m17", "cardType": "standard",
             "backendMode": "managed", "bridgePermission": "self_owned"},
            {"id": "legacy_managed", "mode": "m17", "cardType": "standard",
             "bridgePermission": "approved"},
            {"id": "m17_net", "mode": "m17", "cardType": "m17_net",
             "bridgePermission": "approved"},
            {"id": "other_mode", "mode": "p25", "cardType": "standard",
             "backendMode": "managed"},
        ]
    }
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        selected = subprocess.run(
            [sys.executable, "-c", selector, str(config_path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    assert selected == ["managed_m17", "legacy_managed", "m17_net"], selected
    print("M17 reapply selection self-test: PASS")


if __name__ == "__main__":
    main()
