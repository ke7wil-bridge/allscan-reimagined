#!/usr/bin/env bash
set -Eeuo pipefail

# Idempotent ASL3 Broadcastify setup for Johnny2.
# Run as root on Johnny2. The feed password is read without echo and is never
# accepted as a command-line argument, so it does not land in shell history.

main_node=623123
stream_id=BCSTFY
icecast_host=audio3.broadcastify.com
icecast_port=80
icecast_mount=/3crmvb5kqyfj
icecast_user=source
stream_name="Johnny2 Broadcastify Feed"
stream_description="Listen-only amateur radio feed via AllStarLink"
stream_url="https://www.broadcastify.com/"
rpt_conf=/etc/asterisk/rpt.conf
config_dir=/etc/asterisk/broadcastify
config_file=
backup_dir=

usage() {
  cat <<'EOF'
Usage: sudo ./johnny2-install-broadcastify.sh [options]

Options:
  --node NODE          ASL node to stream (default: 623123)
  --stream-id ID       Service/FIFO name (default: BCSTFY)
  --host HOST          Broadcastify server
  --port PORT          Broadcastify port
  --mount /MOUNT       Broadcastify mount
  --name TEXT          Stream name shown in metadata
  --description TEXT   Stream description
  --url URL            Stream metadata URL
  --help                Show this help

The Broadcastify source password is requested securely at runtime. It can also
be supplied through BROADCASTIFY_PASSWORD for non-interactive automation; avoid
putting that assignment in shell history.
EOF
}

while (($#)); do
  case "$1" in
    --node) main_node=${2:?missing value for --node}; shift 2 ;;
    --stream-id) stream_id=${2:?missing value for --stream-id}; shift 2 ;;
    --host) icecast_host=${2:?missing value for --host}; shift 2 ;;
    --port) icecast_port=${2:?missing value for --port}; shift 2 ;;
    --mount) icecast_mount=${2:?missing value for --mount}; shift 2 ;;
    --name) stream_name=${2:?missing value for --name}; shift 2 ;;
    --description) stream_description=${2:?missing value for --description}; shift 2 ;;
    --url) stream_url=${2:?missing value for --url}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Run this installer as root." >&2; exit 1; }
[[ $main_node =~ ^[0-9]+$ ]] || { echo "--node must be numeric." >&2; exit 2; }
[[ $stream_id =~ ^[A-Za-z0-9_-]+$ ]] || { echo "Invalid --stream-id." >&2; exit 2; }
[[ $icecast_port =~ ^[0-9]+$ ]] || { echo "Invalid --port." >&2; exit 2; }
[[ $icecast_mount == /* ]] || { echo "--mount must start with /." >&2; exit 2; }

if [[ -z ${BROADCASTIFY_PASSWORD:-} ]]; then
  [[ -r /dev/tty ]] || {
    echo "Set BROADCASTIFY_PASSWORD when running non-interactively." >&2
    exit 2
  }
  read -r -s -p "Broadcastify source password: " BROADCASTIFY_PASSWORD </dev/tty
  printf '\n' >/dev/tty
fi
[[ -n $BROADCASTIFY_PASSWORD ]] || { echo "The source password is empty." >&2; exit 2; }
[[ $BROADCASTIFY_PASSWORD != *$'\n'* && $BROADCASTIFY_PASSWORD != *$'\r'* ]] || {
  echo "The source password contains an invalid newline." >&2
  exit 2
}

for path in "$rpt_conf" /usr/bin/asl-broadcastify /usr/libexec/asl3/rpt_audio_writer; do
  [[ -e $path ]] || {
    echo "Missing $path. Update ASL3 to 3.18.2-2 or newer before installing." >&2
    exit 1
  }
done
command -v ffmpeg >/dev/null || {
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg
}

config_file="$config_dir/$stream_id.conf"
backup_dir="/var/backups/johnny2-broadcastify/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$backup_dir"
cp -a "$rpt_conf" "$backup_dir/rpt.conf"
[[ ! -e $config_file ]] || cp -a "$config_file" "$backup_dir/$stream_id.conf"

rollback() {
  local rc=$?
  trap - EXIT
  if ((rc != 0)); then
    echo "Install failed; restoring Asterisk configuration from $backup_dir" >&2
    cp -a "$backup_dir/rpt.conf" "$rpt_conf"
    if [[ -e $backup_dir/$stream_id.conf ]]; then
      cp -a "$backup_dir/$stream_id.conf" "$config_file"
    else
      rm -f "$config_file"
    fi
    systemctl restart asterisk.service >/dev/null 2>&1 || true
  fi
  unset BROADCASTIFY_PASSWORD
  exit "$rc"
}
trap rollback EXIT

fifo="/var/lib/asterisk/$stream_id.fifo"
python3 - "$rpt_conf" "$main_node" "$fifo" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
node = sys.argv[2]
fifo = sys.argv[3]
text = path.read_text()
lines = text.splitlines(keepends=True)
header = re.compile(rf"^\s*\[{re.escape(node)}\](?:\([^]]+\))?\s*(?:;.*)?$")
section = re.compile(r"^\s*\[[^]]+\]")
start = next((i for i, line in enumerate(lines) if header.match(line.rstrip("\r\n"))), None)
if start is None:
    raise SystemExit(f"Node [{node}] was not found in {path}")
end = next((i for i in range(start + 1, len(lines)) if section.match(lines[i])), len(lines))
directive = f"outstreamcmd = /usr/libexec/asl3/rpt_audio_writer,{fifo}\n"
matches = [i for i in range(start + 1, end) if re.match(r"^\s*outstreamcmd\s*=", lines[i], re.I)]
if matches:
    lines[matches[0]] = directive
    for i in reversed(matches[1:]):
        del lines[i]
else:
    lines.insert(end, directive)
path.write_text("".join(lines))
PY

install -d -o asterisk -g asterisk -m 750 "$config_dir"
{
  printf '# Managed by johnny2-install-broadcastify.sh\n'
  printf 'FIFO=%q\n' "$fifo"
  printf 'ICECAST_HOST=%q\n' "$icecast_host"
  printf 'ICECAST_PORT=%q\n' "$icecast_port"
  printf 'ICECAST_MOUNT=%q\n' "$icecast_mount"
  printf 'ICECAST_USER=%q\n' "$icecast_user"
  printf 'ICECAST_PASSWORD=%q\n' "$BROADCASTIFY_PASSWORD"
  printf 'STREAM_NAME=%q\n' "$stream_name"
  printf 'STREAM_DESCRIPTION=%q\n' "$stream_description"
  printf 'STREAM_URL=%q\n' "$stream_url"
  printf 'STREAM_PUBLIC=1\nSTREAM_GENRE=%q\n' 'Amateur Radio'
  printf 'INPUT_SAMPLERATE=8000\nINPUT_CHANNELS=1\nOUTPUT_BITRATE=16k\n'
} >"$config_file"
chown asterisk:asterisk "$config_file"
chmod 640 "$config_file"
unset BROADCASTIFY_PASSWORD

systemctl restart asterisk.service
systemctl enable --now "asl-broadcastify@$stream_id.service"

for _ in {1..20}; do
  systemctl is-active --quiet "asl-broadcastify@$stream_id.service" && [[ -p $fifo ]] && break
  sleep 1
done
systemctl is-active --quiet "asl-broadcastify@$stream_id.service"
[[ -p $fifo ]] || { echo "Expected FIFO was not created: $fifo" >&2; exit 1; }

trap - EXIT
echo "Broadcastify stream $stream_id is installed for node $main_node."
echo "Service: asl-broadcastify@$stream_id.service (enabled, restart=always)"
echo "Backup: $backup_dir"
