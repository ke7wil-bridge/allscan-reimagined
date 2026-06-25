#!/bin/bash
set -Eeuo pipefail

ENV_FILE="/root/tgif-login.env"
REFRESHER="/usr/local/sbin/tgif-refresh-token.py"
SERVICE="/etc/systemd/system/tgif-refresh-token.service"
TIMER="/etc/systemd/system/tgif-refresh-token.timer"
CLIENT_DAEMON="connected-clients-daemon.service"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
prompt_secret() {
  local label="$1" value
  read -r -s -p "$label: " value
  printf '\n' >&2
  printf '%s' "$value"
}
prompt_text() {
  local label="$1" default="$2" value
  read -r -p "$label [$default]: " value
  printf '%s' "${value:-$default}"
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Run this setup as root."
[ -t 0 ] || fail "TGIF setup needs an interactive terminal."
command -v python3 >/dev/null 2>&1 || fail "python3 is required."
command -v base64 >/dev/null 2>&1 || fail "base64 is required."

echo
echo "=== DMR/TGIF Connected-Client Tracking ==="
echo "TGIF credentials are stored only in $ENV_FILE with root-only permissions."
callsign=$(prompt_text "TGIF callsign" "")
[ -n "$callsign" ] || fail "TGIF callsign is required."
talkgroup=$(prompt_text "TGIF talkgroup" "")
[ -n "$talkgroup" ] || fail "TGIF talkgroup is required."
password=$(prompt_secret "TGIF password")
[ -n "$password" ] || fail "TGIF password is required."

install -d -o root -g root -m 700 "$(dirname "$ENV_FILE")"
tmp_env=$(mktemp)
chmod 600 "$tmp_env"
{
  printf 'TGIF_CALLSIGN_B64=%s\n' "$(printf '%s' "$callsign" | base64 | tr -d '\n')"
  printf 'TGIF_PASSWORD_B64=%s\n' "$(printf '%s' "$password" | base64 | tr -d '\n')"
  printf 'TGIF_TALKGROUP_B64=%s\n' "$(printf '%s' "$talkgroup" | base64 | tr -d '\n')"
  printf 'TGIF_TOKEN_FILE=/var/lib/allscan-reimagined/tgif-token.json\n'
} > "$tmp_env"
install -o root -g root -m 600 "$tmp_env" "$ENV_FILE"
rm -f "$tmp_env"
unset password

cat > "$REFRESHER" <<'PY'
#!/usr/bin/env python3
import base64
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

def load_env(path):
    data = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key] = value.strip().strip("'\"")
    return data

env = load_env("/root/tgif-login.env")
def b64(name):
    return base64.b64decode(env.get(name, "").encode("ascii")).decode("utf-8")

callsign = b64("TGIF_CALLSIGN_B64")
password = b64("TGIF_PASSWORD_B64")
talkgroup = b64("TGIF_TALKGROUP_B64")
token_file = pathlib.Path(env.get("TGIF_TOKEN_FILE", "/var/lib/allscan-reimagined/tgif-token.json"))
if not callsign or not password or not talkgroup:
    print("TGIF login is incomplete.", file=sys.stderr)
    sys.exit(2)

payload = urllib.parse.urlencode({
    "callsign": callsign,
    "password": password,
    "talkgroup": talkgroup,
}).encode("utf-8")

urls = [
    "https://tgif.network/api/login",
    "https://tgif.network/api/auth/login",
]
last_error = ""
for url in urls:
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "AllScan-Reimagined/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
        data = json.loads(body)
    except Exception as exc:
        last_error = str(exc)
        continue
    token = data.get("token") or data.get("access_token") or data.get("jwt") or data.get("api_token")
    if token:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(json.dumps({
            "callsign": callsign,
            "talkgroup": talkgroup,
            "token": token,
        }, indent=2) + "\n", encoding="utf-8")
        os.chmod(token_file, 0o600)
        print("DMR TGIF token refreshed.")
        sys.exit(0)
    last_error = data.get("error") or data.get("message") or "TGIF login did not return a token."

print("TGIF login failed, may require CAPTCHA, updated endpoint, or corrected credentials.", file=sys.stderr)
if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
chmod 755 "$REFRESHER"

cat > "$SERVICE" <<EOF
[Unit]
Description=Refresh TGIF token for AllScan Reimagined connected-client tracking
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
ExecStart=$REFRESHER
ExecStartPost=-/bin/systemctl restart $CLIENT_DAEMON
EOF

cat > "$TIMER" <<'EOF'
[Unit]
Description=Refresh TGIF token for AllScan Reimagined

[Timer]
OnBootSec=2min
OnUnitActiveSec=4h
Unit=tgif-refresh-token.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now tgif-refresh-token.timer
if systemctl start tgif-refresh-token.service; then
  systemctl reset-failed tgif-refresh-token.service >/dev/null 2>&1 || true
  echo "DMR/TGIF tracking is configured. If no clients appear, no TGIF clients may be connected right now."
else
  echo "TGIF login failed or needs attention. Credentials were stored root-only; no password was printed." >&2
  exit 1
fi
