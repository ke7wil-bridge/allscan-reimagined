#!/usr/bin/env python3
"""Validate all agent examples against central signed-command configuration."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
central = json.loads((ROOT / "ops/asrplus/command-integrity.example.json").read_text())
agents = {
    hub: json.loads((ROOT / "ops/asrplus" / f"{hub}-agent.json").read_text())
    for hub in ("phoenix", "ky2", "texas")
}
for hub, agent in agents.items():
    signing = central["hubs"][hub]
    assert agent["hub"] == hub
    assert agent["commandIntegrity"] == signing
    assert signing["keyFile"] == f"/etc/asrplus/command-keys/{hub}.key"

agent = agents["texas"]
texas = central["hubs"]["texas"]
assert agent["hub"] == "texas" and agent["node"] == "674984"
assert agent["server"] == "root@44.30.157.21"  # Kentucky MasterHub forced-command endpoint
assert agent["key"] == "/etc/asrplus/ssh/texas-to-kentucky"
assert agent["knownHosts"] == "/etc/asrplus/ssh/known_hosts_kentucky"
assert agent["commandIntegrity"]["mode"] == texas["mode"] == "required"
assert agent["commandIntegrity"]["keyId"] == texas["keyId"] == "texas-2026-01"
assert agent["commandIntegrity"]["keyFile"] == texas["keyFile"] == "/etc/asrplus/command-keys/texas.key"
assert agent["controlPolling"] is False
assert agent["controlHelper"] == "/usr/local/sbin/asrplus-texas-control"
assert agent["commandLedger"] == "/var/lib/asrplus-agent/executed-commands.json"
print("PASS Texas agent endpoint, separate SSH/HMAC credentials, and central key-path parity")
