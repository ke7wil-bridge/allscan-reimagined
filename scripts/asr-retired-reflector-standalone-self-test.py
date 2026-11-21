#!/usr/bin/env python3
"""Reapply must never remove a working bridge card for leftover grouping fields (#168).

Johnny2 (623123) lost its five working DMR/YSF/P25/NXDN/M17 bridges on the
Beta 8.6 upgrade because each card still carried old grouping fields
(urfReflector/urfGroupId/urfName). Those fields must decide nothing: they are
dropped and the card stays. This runs the real reapply block against fixtures.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
reapply = (ROOT / "scripts/asr-reapply.sh").read_text(encoding="utf-8")
opening = "<<'PY_RETIRE_URF'"
start = reapply.index("\n", reapply.index(opening)) + 1
block = reapply[start:reapply.index("\nPY_RETIRE_URF\n", start) + 1]
GROUPING = {"urfReflector", "urfGroupId", "urfName"}


def card(card_id: str, mode: str, node: str, **extra) -> dict:
    return {"id": card_id, "mode": mode, "node": node, "cardType": "standard",
            "urfReflector": True, "urfGroupId": "g", "urfName": "OLD", **extra}


def run(directory: Path, bridges: list, retired: dict | None = None) -> tuple[dict, dict | None, str]:
    config = directory / "config.json"
    config.write_text(json.dumps({"node": "623123", "bridges": bridges}), encoding="utf-8")
    retired_path = directory / "retired-urf-bridges.json"
    if retired is not None:
        retired_path.write_text(json.dumps(retired), encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-", str(config)], input=block,
                            capture_output=True, text=True, check=True)
    after = json.loads(config.read_text(encoding="utf-8"))
    archive = json.loads(retired_path.read_text(encoding="utf-8")) if retired_path.exists() else None
    return after, archive, result.stdout


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


bridges = [
    card("dmr_johnny2", "dmr", "1001", dmrMasterHost="tgif.network"),
    card("ysf_johnny2", "ysf", "1003", bridgeType="direct-ysf"),
    card("p25_johnny2", "p25", "1004", bridgeType="direct-p25"),
    card("nxdn_johnny2", "nxdn", "1005", bridgeType="direct-nxdn"),
    card("m17_johnny2", "m17", "1006", m17Reflector="M17-073"),
    card("zello", "zello", "1007"),
]
ids = [b["id"] for b in bridges]

with tempfile.TemporaryDirectory() as tmp:
    # 1. Every bridge card stays, minus the leftover fields; settings are untouched.
    d = Path(tmp) / "keep"; d.mkdir()
    after, archive, out = run(d, json.loads(json.dumps(bridges)))
    check([b["id"] for b in after["bridges"]] == ids, f"bridge cards were dropped: {after['bridges']}")
    check(all(not (GROUPING & set(b)) for b in after["bridges"]), "leftover fields were not removed")
    check(after["bridges"][0]["dmrMasterHost"] == "tgif.network", "bridge settings were not preserved")
    check(archive is None, "a working bridge was written to the retired archive")
    check(any(p.name.startswith("config.json.pre-urf-retirement-") for p in d.iterdir()), "no backup taken")
    _, _, again = run(d, after["bridges"])
    check(again == "", f"second pass was not a no-op: {again!r}")

    # 2. Even with an old aggregate card present, only the aggregate leaves.
    d = Path(tmp) / "aggregate"; d.mkdir()
    after, archive, out = run(d, [{"id": "old", "mode": "urf", "cardType": "standard"}, *json.loads(json.dumps(bridges))])
    check([b["id"] for b in after["bridges"]] == ids, f"working bridges left with the aggregate: {after['bridges']}")
    check([b["id"] for b in archive["retired"][-1]["bridges"]] == ["old"], "archive holds more than the aggregate")
    check(os.stat(d / "retired-urf-bridges.json").st_mode & 0o777 == 0o600, "retired archive is not 0600")

    # 3. Bridges an earlier release moved out come back once, without the fields.
    d = Path(tmp) / "recover"; d.mkdir()
    archive_in = {"schema": 1, "retired": [
        {"retiredAt": "20261120-160813", "bridges": json.loads(json.dumps(bridges[:5]))},
        {"retiredAt": "20261101-000000", "bridges": [{"id": "old", "mode": "urf", "cardType": "standard"}]},
    ]}
    after, archive, out = run(d, [bridges[5], {k: v for k, v in bridges[0].items() if k not in GROUPING}], archive_in)
    got = [b["id"] for b in after["bridges"]]
    check(got == ["zello", "dmr_johnny2", "ysf_johnny2", "p25_johnny2", "nxdn_johnny2", "m17_johnny2"],
          f"recovery result wrong: {got}")
    check(all(not (GROUPING & set(b)) for b in after["bridges"]), "restored cards kept leftover fields")
    check("old" not in got, "the aggregate card was restored")
    first = archive["retired"][0]
    check(first.get("restoredAt") and first["restoredIds"] == ["ysf_johnny2", "p25_johnny2", "nxdn_johnny2", "m17_johnny2"],
          f"recovered batch not marked: {first}")
    check("Restored 4 bridge" in out, out)
    # A card an administrator deletes afterwards does not come back.
    after2, _, again = run(d, [bridges[5] | {}])
    check([b["id"] for b in after2["bridges"]] == ["zello"], "recovery ran twice")

# The web rules must not hide or drop a card for the leftover fields.
for path in ["asr-api.php", "server/asr-api.php",
             *[f"compat/allscan-v{v}/asr-settings/settings-controller.php" for v in ("1.02", "1.03", "1.04", "1.05", "1.06")]]:
    text = (ROOT / path).read_text(encoding="utf-8")
    name = "function asr_is_retired_reflector_bridge" if "asr-api" in path else "function asrSettingsIsRetiredReflectorBridge"
    body = text[text.index(name):text.index("\n}\n", text.index(name))]
    check("urfReflector" not in body and "(#168)" in body, f"{path}: card filter still reads the leftover fields")
print("Bridge card retirement self-test: ok")
