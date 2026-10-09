#!/bin/bash
set -euo pipefail
TGIF_RUNTIME_DIR="${HEALTH_DIR:-/run/tgif-dmr}"
TGIF_ASSET_DIR="${TGIF_ASSET_DIR:-/opt/tgif}"
MMDVM_BINARY="${MMDVM_BINARY:-/opt/MMDVM_Bridge}"
install -d -m 750 "$TGIF_RUNTIME_DIR"
python3 - <<'RENDER'
import os
from pathlib import Path

secret = Path(os.environ.get("TGIF_PASSWORD_FILE", "/run/secrets/tgif-password"))
password = secret.read_text(encoding="utf-8").rstrip("\n")
if not password or len(password) > 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
    raise SystemExit("TGIF password is empty, too long, or contains control characters.")
asset_dir = os.environ.get("TGIF_ASSET_DIR", "/opt/tgif")
runtime_dir = os.environ.get("HEALTH_DIR", "/run/tgif-dmr")
templates = (
    (asset_dir + "/MMDVM_Bridge.ini.template", runtime_dir + "/MMDVM_Bridge.ini",
     {"TGIF_PASSWORD": password, "DMR_ID": os.environ["DMR_ID"],
      "DMR_NETWORK_ID": os.environ.get("DMR_NETWORK_ID", os.environ["DMR_ID"]),
      "CALLSIGN": os.environ["CALLSIGN"], "LOCAL_PORT": os.environ["LOCAL_PORT"],
      "MMDVM_PORT": os.environ["MMDVM_PORT"]}),
    (asset_dir + "/DVSwitch.ini", runtime_dir + "/DVSwitch.ini",
     {"DVSWITCH_TX": os.environ["DVSWITCH_TX"], "DVSWITCH_RX": os.environ["DVSWITCH_RX"]}),
)
for source, destination, values in templates:
    content = Path(source).read_text(encoding="utf-8")
    for key, value in values.items():
        content = content.replace("__" + key + "__", value)
    content = content.replace("/run/tgif-dmr", runtime_dir)
    Path(destination).write_text(content, encoding="utf-8")
RENDER
chmod 600 "$TGIF_RUNTIME_DIR/MMDVM_Bridge.ini" "$TGIF_RUNTIME_DIR/DVSwitch.ini"
python3 "$TGIF_ASSET_DIR/asr-tgif-urf-dmr-bridge.py" &
adapter=$!
DVSWITCH="$TGIF_RUNTIME_DIR/DVSwitch.ini" "$MMDVM_BINARY" "$TGIF_RUNTIME_DIR/MMDVM_Bridge.ini" &
mmdvm=$!
trap 'kill "$adapter" "$mmdvm" 2>/dev/null || true; wait 2>/dev/null || true' EXIT INT TERM
printf '%s\n' "$adapter" > "$TGIF_RUNTIME_DIR/adapter.pid"
printf '%s\n' "$mmdvm" > "$TGIF_RUNTIME_DIR/mmdvm.pid"
wait -n "$adapter" "$mmdvm"
