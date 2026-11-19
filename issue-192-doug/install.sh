#!/usr/bin/env bash
# Issue 192 / Doug node67098 / ASR Beta 8.4.3 dashboard-only recovery.
set -Eeuo pipefail
BASE='https://raw.githubusercontent.com/ke7wil-bridge/allscan-reimagined/fix/issue-192-beta843/issue-192-doug'
EXPECTED='fde1af4b7e5234850ffc312b2ac028a9bf75af6ea353e2044f97e266836fdb0c'
[[ $EUID -eq 0 ]] || { echo 'Run: sudo bash install.sh' >&2; exit 1; }
[[ $(hostname) == node67098 ]] || { echo 'STOP: installer is approved only for node67098' >&2; exit 1; }
ROOT='/opt/allscan-reimagined/current'
[[ $(readlink -f "$ROOT") == /opt/allscan-reimagined/releases/1.0.0-beta.8.4.3 ]] || { echo 'STOP: active release is not Beta 8.4.3' >&2; exit 1; }
LIVE='/var/www/html/asr'; PIN="$ROOT/web"
[[ -f "$LIVE/index.html" && -f "$PIN/index.html" ]] || { echo 'STOP: missing dashboard index' >&2; exit 1; }
[[ $(grep -Eo '/asr/assets/index-[A-Za-z0-9_-]+\.css' "$LIVE/index.html" | head -1) == /asr/assets/index-AkBgFY7U.css ]] || { echo 'STOP: CSS does not match' >&2; exit 1; }
[[ $(grep -Eo '/asr/assets/index-[A-Za-z0-9_-]+\.js' "$LIVE/index.html" | head -1) == /asr/assets/index-CAoW7gmy.js ]] || { echo 'STOP: dashboard changed since diagnosis' >&2; exit 1; }
cmp -s "$PIN/index.html" "$LIVE/index.html" || { echo 'STOP: live and pinned dashboard differ' >&2; exit 1; }
[[ -f "$LIVE/assets/index-CAoW7gmy.js" && -f "$PIN/assets/index-CAoW7gmy.js" ]] || { echo 'STOP: original assets missing' >&2; exit 1; }
command -v curl >/dev/null; command -v sha256sum >/dev/null
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
curl --fail --location --silent --show-error --retry 2 "$BASE/index-DJmKzIR3.js" -o "$TMP/index-DJmKzIR3.js"
curl --fail --location --silent --show-error --retry 2 "$BASE/index.html" -o "$TMP/index.html"
echo "$EXPECTED  $TMP/index-DJmKzIR3.js" | sha256sum -c -
grep -Fq '/asr/assets/index-DJmKzIR3.js' "$TMP/index.html" || { echo 'STOP: invalid index' >&2; exit 1; }
grep -Fq '/asr/assets/index-AkBgFY7U.css' "$TMP/index.html" || { echo 'STOP: invalid CSS' >&2; exit 1; }
BACKUP="/var/backups/asr-192-node67098-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP/live" "$BACKUP/pinned" "$LIVE/assets" "$PIN/assets"
cp -a "$LIVE/index.html" "$BACKUP/live/index.html"
cp -a "$PIN/index.html" "$BACKUP/pinned/index.html"
cp -a "$LIVE/assets/index-CAoW7gmy.js" "$BACKUP/live/"
cp -a "$PIN/assets/index-CAoW7gmy.js" "$BACKUP/pinned/"
restore() { cp -a "$BACKUP/live/index.html" "$LIVE/index.html"; cp -a "$BACKUP/pinned/index.html" "$PIN/index.html"; rm -f "$LIVE/assets/index-DJmKzIR3.js" "$PIN/assets/index-DJmKzIR3.js"; echo "ROLLBACK completed; backup $BACKUP" >&2; }
trap 'restore; exit 1' ERR
install -m 644 "$TMP/index-DJmKzIR3.js" "$PIN/assets/index-DJmKzIR3.js"
install -m 644 "$TMP/index-DJmKzIR3.js" "$LIVE/assets/index-DJmKzIR3.js"
install -m 644 "$TMP/index.html" "$PIN/index.html"
install -m 644 "$TMP/index.html" "$LIVE/index.html"
cmp -s "$LIVE/index.html" "$PIN/index.html"
curl -fsS -o /dev/null --max-time 10 http://127.0.0.1/asr/
trap - ERR
printf '\nPASS: #192 dashboard fix installed; no service restart. Backup: %s\n' "$BACKUP"
printf 'To roll back manually: sudo cp %s/live/index.html %s/index.html; sudo cp %s/pinned/index.html %s/index.html\n' "$BACKUP" "$LIVE" "$BACKUP" "$PIN"
