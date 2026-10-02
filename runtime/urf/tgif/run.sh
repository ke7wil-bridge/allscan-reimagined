#!/bin/bash
set -euo pipefail
install -d -m 755 /run/tgif-dmr
python3 - <<'RENDER'
import os
from pathlib import Path

secret = Path(os.environ.get("TGIF_PASSWORD_FILE", "/run/secrets/tgif-password"))
password = secret.read_text(encoding="utf-8").rstrip("\n")
if not password or len(password) > 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
    raise SystemExit("TGIF password is empty, too long, or contains control characters.")
templates = (
    ("/opt/tgif/MMDVM_Bridge.ini.template", "/run/tgif-dmr/MMDVM_Bridge.ini",
     {"TGIF_PASSWORD": password, "DMR_ID": os.environ["DMR_ID"],
      "DMR_NETWORK_ID": os.environ.get("DMR_NETWORK_ID", os.environ["DMR_ID"]),
      "CALLSIGN": os.environ["CALLSIGN"], "LOCAL_PORT": os.environ["LOCAL_PORT"],
      "MMDVM_PORT": os.environ["MMDVM_PORT"]}),
    ("/opt/tgif/DVSwitch.ini", "/run/tgif-dmr/DVSwitch.ini",
     {"DVSWITCH_TX": os.environ["DVSWITCH_TX"], "DVSWITCH_RX": os.environ["DVSWITCH_RX"]}),
)
for source, destination, values in templates:
    content = Path(source).read_text(encoding="utf-8")
    for key, value in values.items():
        content = content.replace("__" + key + "__", value)
    Path(destination).write_text(content, encoding="utf-8")
RENDER
chmod 600 /run/tgif-dmr/MMDVM_Bridge.ini /run/tgif-dmr/DVSwitch.ini
python3 /opt/tgif/asr-tgif-urf-dmr-bridge.py &
adapter=$!
DVSWITCH=/run/tgif-dmr/DVSwitch.ini /opt/MMDVM_Bridge /run/tgif-dmr/MMDVM_Bridge.ini &
mmdvm=$!
trap 'kill "$adapter" "$mmdvm" 2>/dev/null || true; wait 2>/dev/null || true' EXIT INT TERM
printf '%s\n' "$adapter" > /run/tgif-dmr/adapter.pid
printf '%s\n' "$mmdvm" > /run/tgif-dmr/mmdvm.pid
wait -n "$adapter" "$mmdvm"
