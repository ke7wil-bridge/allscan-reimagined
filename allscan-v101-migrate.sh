#!/bin/bash
set -Eeuo pipefail

PACKAGE_URL="${ASR_PACKAGE_URL:-http://192.168.0.100:8008/allscan-v101-reimagined.tar.gz}"
PACKAGE_SHA256="17620d6b1cabe12056aa359e94ed80f48eb3a548be0a0a609f0b4cdfe2e64bd1"
WEB_ROOT="/var/www/html"
LIVE_DIR="$WEB_ROOT/allscan"
STAMP="$(date +%Y%m%d-%H%M%S)"
STAGE_DIR="$WEB_ROOT/.allscan-v101-stage-$STAMP"
BACKUP_DIR="$WEB_ROOT/allscan.pre-v101-$STAMP"
FAILED_DIR="$WEB_ROOT/allscan.failed-v101-$STAMP"
PACKAGE_FILE="/tmp/allscan-v101-reimagined-$STAMP.tar.gz"
BACKUP_CREATED=0

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

rollback_on_error() {
  status=$?
  set +e
  if [ "$BACKUP_CREATED" -eq 1 ] && [ -d "$BACKUP_DIR" ]; then
    printf '\nMigration failed. Restoring the original AllScan directory...\n' >&2
    if [ -d "$LIVE_DIR" ]; then
      mv "$LIVE_DIR" "$FAILED_DIR"
    fi
    mv "$BACKUP_DIR" "$LIVE_DIR"
    printf 'Original page restored. Failed files retained at: %s\n' "$FAILED_DIR" >&2
  fi
  exit "$status"
}

trap rollback_on_error ERR

[ "${EUID:-$(id -u)}" -eq 0 ] || fail 'Run this migration as root.'
[ -d "$LIVE_DIR" ] || fail "$LIVE_DIR was not found."

for command in curl tar php sha256sum install find grep; do
  command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

WEB_GROUP='www-data'
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP='apache'
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP='http'
getent group "$WEB_GROUP" >/dev/null 2>&1 || fail 'Could not identify the web-server group.'

printf 'Downloading the verified AllScan 1.01 + Reimagined package...\n'
curl -fL "$PACKAGE_URL" -o "$PACKAGE_FILE"
printf '%s  %s\n' "$PACKAGE_SHA256" "$PACKAGE_FILE" | sha256sum -c -

mkdir -p "$STAGE_DIR"
tar -xzf "$PACKAGE_FILE" -C "$STAGE_DIR"

grep -q '\$AllScanVersion = "v1.01"' "$STAGE_DIR/include/common.php" || \
  fail 'The staged backend is not AllScan 1.01.'
[ -f "$STAGE_DIR/asr-api.php" ] || fail 'The Reimagined API is missing.'
[ -f "$STAGE_DIR/index.html" ] || fail 'The Reimagined page is missing.'
[ -d "$STAGE_DIR/assets" ] || fail 'The Reimagined assets are missing.'

while IFS= read -r php_file; do
  php -l "$php_file" >/dev/null
done < <(find "$STAGE_DIR" -type f -name '*.php' -print)

bash -n "$STAGE_DIR/allscan_wt_clients.sh"

while IFS= read -r asset; do
  [ -f "$STAGE_DIR/$asset" ] || fail "Referenced asset is missing: $asset"
done < <(grep -o 'assets/index-[^" ]*\.[a-z]*' "$STAGE_DIR/index.html" | sort -u)

printf 'Preserving node-specific files...\n'
shopt -s nullglob
for file in "$LIVE_DIR"/*.ini "$LIVE_DIR"/*.ini.bak; do
  cp -p "$file" "$STAGE_DIR/"
done
shopt -u nullglob

if [ -d "$LIVE_DIR/img" ]; then
  mkdir -p "$STAGE_DIR/img"
  cp -a "$LIVE_DIR/img/." "$STAGE_DIR/img/"
fi

for json_file in bridge-live.json connected-clients.json; do
  if [ -s "$LIVE_DIR/$json_file" ]; then
    php -r 'json_decode(file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);' \
      "$LIVE_DIR/$json_file"
    cp -p "$LIVE_DIR/$json_file" "$STAGE_DIR/$json_file"
  fi
done

if [ ! -s "$STAGE_DIR/bridge-live.json" ]; then
  printf '%s\n' '{"updated":"","dmr":{},"ysf":{},"zello":{},"dstar":{}}' \
    > "$STAGE_DIR/bridge-live.json"
fi
if [ ! -s "$STAGE_DIR/connected-clients.json" ]; then
  printf '%s\n' '{"dmr":[],"ysf":[],"zello":[],"dstar":[]}' \
    > "$STAGE_DIR/connected-clients.json"
fi

if [ -f /etc/allscan/allscan.db ]; then
  cp -p /etc/allscan/allscan.db "/etc/allscan/allscan.db.pre-v101-$STAMP"
fi

chown -R "root:$WEB_GROUP" "$STAGE_DIR"
find "$STAGE_DIR" -type d -exec chmod 775 {} +
find "$STAGE_DIR" -type f -exec chmod 664 {} +
chmod 775 "$STAGE_DIR/AllScanInstallUpdate.php" "$STAGE_DIR/allscan_wt_clients.sh"

install -o root -g root -m 755 \
  "$STAGE_DIR/allscan_wt_clients.sh" \
  /usr/local/bin/allscan_wt_clients.sh

printf 'Activating AllScan 1.01 + Reimagined...\n'
mv "$LIVE_DIR" "$BACKUP_DIR"
BACKUP_CREATED=1
mv "$STAGE_DIR" "$LIVE_DIR"

page_html="$(curl -fsS http://127.0.0.1/allscan/)"
printf '%s' "$page_html" | grep -q 'assets/index-'

favorites_json="$(curl -fsS 'http://127.0.0.1/allscan/asr-api.php?action=favorites')"
printf '%s' "$favorites_json" | php -r '
  $payload = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
  if (($payload["ok"] ?? false) !== true) exit(1);
'

BACKUP_CREATED=0
trap - ERR

printf '\nMigration complete.\n'
printf 'Live directory: %s\n' "$LIVE_DIR"
printf 'Rollback directory: %s\n' "$BACKUP_DIR"
printf 'AllScan backend: v1.01\n'
printf 'Reimagined page and custom services: installed\n'
printf 'Refresh the browser once with Ctrl-F5.\n'
