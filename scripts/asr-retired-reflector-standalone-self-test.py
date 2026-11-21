#!/usr/bin/env python3
"""Reapply must only retire per-mode reflector cards alongside their aggregate (#168).

Johnny2 (623123) lost its five working DMR/YSF/P25/NXDN/M17 bridges on the
Beta 8.6 upgrade: each standalone card still carried the old grouping fields
(urfReflector/urfGroupId/urfName), and the #177 retirement step removed every
flagged card even though no aggregate reflector card existed. This runs the
real reapply block against fixture configs.
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
            "urfReflector": True, "urfGroupId": "urf", "urfName": "URFWIL", **extra}


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


standalone = [
    card("dmr_johnny2", "dmr", "1001", dmrMasterHost="tgif.network"),
    card("ysf_johnny2", "ysf", "1003", bridgeType="direct-ysf"),
    card("p25_johnny2", "p25", "1004", bridgeType="direct-p25"),
    card("nxdn_johnny2", "nxdn", "1005", bridgeType="direct-nxdn"),
    card("m17_johnny2", "m17", "1006", m17Reflector="M17-073"),
]

with tempfile.TemporaryDirectory() as tmp:
    # 1. Standalone bridges with leftover grouping fields stay, minus those fields.
    d = Path(tmp) / "standalone"; d.mkdir()
    after, archive, out = run(d, json.loads(json.dumps(standalone)))
    ids = [b["id"] for b in after["bridges"]]
    check(ids == [b["id"] for b in standalone], f"standalone bridges were dropped: {ids}")
    check(all(not (GROUPING & set(b)) for b in after["bridges"]), "grouping fields were not removed")
    check(after["bridges"][0]["dmrMasterHost"] == "tgif.network", "bridge settings were not preserved")
    check(archive is None, "standalone bridges were written to the retired archive")
    check(any(p.name.startswith("config.json.pre-urf-retirement-") for p in d.iterdir()), "no backup taken")
    check("Kept 5 standalone" in out, out)
    _, _, again = run(d, after["bridges"])
    check(again == "", f"second pass was not a no-op: {again!r}")

    # 2. A real aggregate reflector still retires itself and its per-mode cards;
    #    a Zello card with the old flag stays (#177).
    d = Path(tmp) / "aggregate"; d.mkdir()
    bridges = [{"id": "urfnet", "mode": "urf", "cardType": "standard", "urfReflector": True, "modes": ["ysf", "dmr"]},
               card("urf_ysf", "ysf", "1101"), card("urf_dmr", "dmr", "1102"),
               card("zello", "zello", "1103")]
    after, archive, out = run(d, bridges)
    check([b["id"] for b in after["bridges"]] == ["zello"], f"aggregate group not retired: {after['bridges']}")
    check(sorted(b["id"] for b in archive["retired"][-1]["bridges"]) == ["urf_dmr", "urf_ysf", "urfnet"],
          "retired archive is wrong")

    # 3. Nodes already upgraded get their wrongly retired standalone cards back once.
    d = Path(tmp) / "recover"; d.mkdir()
    keep = {"id": "zello", "mode": "zello", "node": "1007", "cardType": "standard"}
    archive_in = {"schema": 1, "retired": [
        {"retiredAt": "20261120-160813", "bridges": json.loads(json.dumps(standalone))},
        {"retiredAt": "20261101-000000", "bridges": [
            {"id": "urfnet", "mode": "urf", "cardType": "standard", "urfReflector": True},
            card("urf_p25", "p25", "1104")]},
    ]}
    after, archive, out = run(d, [keep, dict(standalone[0], urfReflector=False)], archive_in)
    ids = [b["id"] for b in after["bridges"]]
    check(ids == ["zello", "dmr_johnny2", "ysf_johnny2", "p25_johnny2", "nxdn_johnny2", "m17_johnny2"],
          f"recovery result wrong: {ids}")
    check(all(not (GROUPING & set(b)) for b in after["bridges"][2:]), "restored cards kept grouping fields")
    check("urf_p25" not in ids, "a card retired with its aggregate was restored")
    first = archive["retired"][0]
    check(first.get("restoredAt") and first["restoredIds"] == ["ysf_johnny2", "p25_johnny2", "nxdn_johnny2", "m17_johnny2"],
          f"recovered batch not marked: {first}")
    check(not archive["retired"][1].get("restoredAt"), "aggregate batch was marked restored")
    check("Restored 4 standalone" in out, out)
    # An administrator who later deletes a restored card does not see it come back.
    after2, _, again = run(d, [keep])
    check([b["id"] for b in after2["bridges"]] == ["zello"] and again == "", "recovery ran twice")

    # 4. The archive file keeps its owner-only mode.
    check(os.stat(Path(tmp) / "aggregate" / "retired-urf-bridges.json").st_mode & 0o777 == 0o600,
          "retired archive is not 0600")

# The web rules must agree with reapply.
for path in ["asr-api.php", "server/asr-api.php",
             *[f"compat/allscan-v{v}/asr-settings/settings-controller.php" for v in ("1.02", "1.03", "1.04", "1.05")]]:
    text = (ROOT / path).read_text(encoding="utf-8")
    check("(#168)" in text and ("foreach ($bridges as $other)" in text or "foreach($bridges as $other)" in text),
          f"{path}: retirement rule does not require an aggregate card")
    check("IsRetiredReflectorBridge($bridge)))" not in text and "asr_is_retired_reflector_bridge($bridge)))" not in text,
          f"{path}: a caller does not pass the bridge list")
print("Standalone bridge retirement self-test: ok")
