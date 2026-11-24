#!/usr/bin/env python3
"""Offline gates for required-mode config parity and service sandboxing."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops/asrplus"

central = json.loads((OPS / "command-integrity.example.json").read_text())["hubs"]
for hub, node in (("phoenix", "641894"), ("ky2", "674980"), ("texas", "674984")):
    agent = json.loads((OPS / f"{hub}-agent.json").read_text())
    assert agent["hub"] == hub and agent["node"] == node
    assert agent["commandIntegrity"] == central[hub]
    assert agent["commandIntegrity"]["keyFile"] == f"/etc/asrplus/command-keys/{hub}.key"

# Existing hubs remain optional in source until an approved production cutover;
# Texas must fail closed from its first installation.
assert central["phoenix"]["mode"] == central["ky2"]["mode"] == "optional"
assert central["texas"]["mode"] == "required"

# Prove the planned config-only cutover and rollback are exact and do not alter
# key IDs or paths. This never writes a production or repository config.
with tempfile.TemporaryDirectory() as directory:
    staged = Path(directory) / "command-integrity.json"
    original = {"hubs": central}
    staged.write_text(json.dumps(original, sort_keys=True))
    required = json.loads(staged.read_text())
    for hub in ("phoenix", "ky2"):
        required["hubs"][hub]["mode"] = "required"
    staged.write_text(json.dumps(required, sort_keys=True))
    assert all(required["hubs"][hub]["mode"] == "required" for hub in required["hubs"])
    for hub in required["hubs"]:
        assert required["hubs"][hub]["keyId"] == original["hubs"][hub]["keyId"]
        assert required["hubs"][hub]["keyFile"] == original["hubs"][hub]["keyFile"]
    staged.write_text(json.dumps(original, sort_keys=True))
    assert json.loads(staged.read_text()) == original

dropin = (OPS / "staging/ky2/asrplus-ky-control.service.d/security-hardening.conf").read_text()
for directive in (
    "NoNewPrivileges=true", "PrivateTmp=true", "ProtectSystem=strict",
    "ProtectHome=read-only", "UMask=0077", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    "ReadWritePaths=/run/asrplus-ky /var/lib/asrplus-agent",
):
    assert directive in dropin
assert "ExecStart=" not in dropin and "CapabilityBoundingSet=" not in dropin

print("ASR Plus required-mode readiness and KY2 sandbox contract: PASS")
