#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

node=623123
label=BCSTFY
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/var/backups/johnny2-broadcastify-visible/$stamp
install -d -m 700 "$backup"
cp -a /etc/asterisk/rpt.conf "$backup/rpt.conf"
cp -a /etc/asterisk/modules.conf "$backup/modules.conf"
cp -a /etc/asterisk/custom/extensions.conf "$backup/extensions.conf"
for web_node_info in /var/www/html/allscan/astapi/nodeInfo.php /var/www/html/asr/astapi/nodeInfo.php; do
  [[ ! -f $web_node_info ]] || cp -a "$web_node_info" "$backup/$(basename "$(dirname "$(dirname "$web_node_info")")")-nodeInfo.php"
done

for module in res_audiosocket.so app_audiosocket.so pbx_spool.so; do
  if ! grep -Eq "^[[:space:]]*load[[:space:]]*=[[:space:]]*$module([[:space:]]|$)" /etc/asterisk/modules.conf; then
    printf 'load = %s ; Broadcastify visible connection tap\n' "$module" >>/etc/asterisk/modules.conf
  fi
done

cat >/etc/asterisk/custom/broadcastify-tap.conf <<EOF
; Managed by johnny2-enable-visible-bcstfy.sh
[broadcastify-tap]
exten => $node,1,Answer()
 same => n,Rpt($node,P,$label)
 same => n,Hangup()
EOF
if ! grep -Fqx '#include "custom/broadcastify-tap.conf"' /etc/asterisk/custom/extensions.conf; then
  printf '\n#include "custom/broadcastify-tap.conf"\n' >>/etc/asterisk/custom/extensions.conf
fi

python3 - /etc/asterisk/rpt.conf "$label" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
label = sys.argv[2]
lines = path.read_text().splitlines(keepends=True)
lines = [
    line for line in lines
    if not (re.match(r"^\s*outstreamcmd\s*=", line, re.I) and label in line)
]
path.write_text("".join(lines))
PY

python3 - <<'PY'
from pathlib import Path

for path in (
    Path("/var/www/html/allscan/astapi/nodeInfo.php"),
    Path("/var/www/html/asr/astapi/nodeInfo.php"),
):
    if not path.is_file():
        continue
    text = path.read_text()
    if "$node === 'BCSTFY'" in text:
        continue
    old = "\t// Build info string\n\tif(isset($astdb[$node])) {"
    new = (
        "\t// Build info string\n"
        "\tif($node === 'BCSTFY') {\n"
        "\t\t$info = 'Broadcastify Feed';\n"
        "\t} elseif(isset($astdb[$node])) {"
    )
    if old not in text:
        raise SystemExit(f"Expected nodeInfo.php block not found in {path}")
    path.write_text(text.replace(old, new, 1))
PY
php -l /var/www/html/allscan/astapi/nodeInfo.php >/dev/null
php -l /var/www/html/asr/astapi/nodeInfo.php >/dev/null

cat >/usr/local/sbin/johnny2-broadcastify-visible.py <<'PY'
#!/usr/bin/env python3
import logging
import os
import pwd
import shlex
import socket
import struct
import subprocess
import time

CONFIG = "/etc/asterisk/broadcastify/BCSTFY.conf"
NODE = "623123"
LABEL = "BCSTFY"
HOST = "127.0.0.1"
PORT = 19094
UUID = "00000000-0000-0000-0000-000000623123"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("broadcastify-visible")


def config():
    values = {}
    with open(CONFIG, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            parsed = shlex.split(value, posix=True)
            values[key] = parsed[0] if parsed else ""
    for key in ("ICECAST_HOST", "ICECAST_PORT", "ICECAST_MOUNT", "ICECAST_USER", "ICECAST_PASSWORD"):
        if not values.get(key):
            raise RuntimeError(f"{key} is missing from {CONFIG}")
    return values


def receive_exact(conn, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = conn.recv(size - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def originate():
    spool = "/var/spool/asterisk"
    outgoing = os.path.join(spool, "outgoing")
    temporary = os.path.join(spool, f".broadcastify-{os.getpid()}.call.tmp")
    destination = os.path.join(outgoing, "broadcastify-visible.call")
    call = (
        f"Channel: Local/{NODE}@broadcastify-tap\n"
        "MaxRetries: 0\n"
        "RetryTime: 5\n"
        "WaitTime: 20\n"
        "Application: AudioSocket\n"
        f"Data: {UUID},{HOST}:{PORT}\n"
    )
    account = pwd.getpwnam("asterisk")
    with open(temporary, "w", encoding="ascii") as handle:
        handle.write(call)
    os.chown(temporary, account.pw_uid, account.pw_gid)
    os.chmod(temporary, 0o640)
    os.replace(temporary, destination)


def ffmpeg_process(cfg):
    endpoint = (
        f"icecast://{cfg['ICECAST_USER']}:{cfg['ICECAST_PASSWORD']}@"
        f"{cfg['ICECAST_HOST']}:{cfg['ICECAST_PORT']}{cfg['ICECAST_MOUNT']}"
    )
    return subprocess.Popen(
        [
            "/usr/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
            "-f", "s16le", "-ar", "8000", "-ac", "1", "-i", "pipe:0",
            "-c:a", "libmp3lame", "-b:a", cfg.get("OUTPUT_BITRATE", "16k"),
            "-content_type", "audio/mpeg", "-legacy_icecast", "1",
            "-ice_name", cfg.get("STREAM_NAME", "Johnny2 Broadcastify Feed"),
            "-ice_description", cfg.get("STREAM_DESCRIPTION", ""),
            "-ice_genre", cfg.get("STREAM_GENRE", "Amateur Radio"),
            "-ice_url", cfg.get("STREAM_URL", ""),
            "-ice_public", cfg.get("STREAM_PUBLIC", "1"),
            "-f", "mp3", endpoint,
        ],
        stdin=subprocess.PIPE,
    )


def run_session(server, cfg):
    originate()
    conn, address = server.accept()
    log.info("AudioSocket connected from %s:%s as %s", *address, LABEL)
    encoder = ffmpeg_process(cfg)
    audio_bytes = 0
    try:
        while True:
            header = receive_exact(conn, 3)
            if header is None:
                raise ConnectionError("AudioSocket disconnected")
            frame_type = header[0]
            length = struct.unpack(">H", header[1:])[0]
            payload = receive_exact(conn, length) if length else b""
            if payload is None:
                raise ConnectionError("AudioSocket payload ended")
            if frame_type == 0x00:
                raise ConnectionError("AudioSocket hangup")
            if frame_type == 0x10 and payload:
                if encoder.poll() is not None or encoder.stdin is None:
                    raise ConnectionError("ffmpeg exited")
                encoder.stdin.write(payload)
                audio_bytes += len(payload)
    finally:
        log.info("Session ended after %d audio bytes; reconnecting", audio_bytes)
        conn.close()
        if encoder.stdin:
            try:
                encoder.stdin.close()
            except BrokenPipeError:
                pass
        try:
            encoder.terminate()
            encoder.wait(timeout=3)
        except subprocess.TimeoutExpired:
            encoder.kill()


def main():
    cfg = config()
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(1)
        server.settimeout(30)
        log.info("Listening on %s:%d; app_rpt label is %s", HOST, PORT, LABEL)
        while True:
            try:
                run_session(server, cfg)
            except Exception as error:
                log.warning("Bridge loop restarting: %s", error)
                time.sleep(3)


if __name__ == "__main__":
    main()
PY
chmod 700 /usr/local/sbin/johnny2-broadcastify-visible.py
chown root:root /usr/local/sbin/johnny2-broadcastify-visible.py

cat >/etc/systemd/system/johnny2-broadcastify-visible.service <<'EOF'
[Unit]
Description=Johnny2 visible BCSTFY AudioSocket feed
After=asterisk.service network-online.target
Requires=asterisk.service
Wants=network-online.target
PartOf=asterisk.service

[Service]
Type=simple
ExecStart=/usr/local/sbin/johnny2-broadcastify-visible.py
Restart=always
RestartSec=5
User=root
Group=root
NoNewPrivileges=true
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl disable --now asl-broadcastify@BCSTFY.service
systemctl daemon-reload
systemctl restart asterisk.service
systemctl enable --now johnny2-broadcastify-visible.service

for _ in {1..20}; do
  if systemctl is-active --quiet johnny2-broadcastify-visible.service \
    && asterisk -rx "rpt lstats $node" 2>/dev/null | grep -q "$label"; then
    echo "Visible $label connection installed. Backup: $backup"
    exit 0
  fi
  sleep 1
done

systemctl --no-pager --full status johnny2-broadcastify-visible.service >&2 || true
asterisk -rx "rpt lstats $node" >&2 || true
exit 1
