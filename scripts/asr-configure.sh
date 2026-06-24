#!/bin/bash
set -Eeuo pipefail

CONFIG_DIR="/etc/allscan-reimagined"
CONFIG_FILE="$CONFIG_DIR/config.json"
DATA_DIR="/var/lib/allscan-reimagined"
if [ -d /var/www/html/allscan ]; then
  ALLSCAN_DIR="/var/www/html/allscan"
else
  ALLSCAN_DIR="/srv/http/allscan"
fi
NON_INTERACTIVE=0
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --non-interactive) NON_INTERACTIVE=1 ;;
    --force) FORCE=1 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

[ "${EUID:-$(id -u)}" -eq 0 ] || { echo "Run this setup as root." >&2; exit 1; }

WEB_GROUP="www-data"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="apache"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="http"
getent group "$WEB_GROUP" >/dev/null 2>&1 || { echo "Web-server group not found." >&2; exit 1; }

if [ -s "$CONFIG_FILE" ] && [ "$FORCE" -eq 0 ]; then
  echo "Keeping existing Reimagined configuration: $CONFIG_FILE"
  exit 0
fi

detect_node() {
  [ -r /etc/asterisk/rpt.conf ] || return 0
  sed -n 's/^\[\([0-9]\{5,10\}\)\]$/\1/p' /etc/asterisk/rpt.conf | head -1
}

node_db_files() {
  for file in \
    "$ALLSCAN_DIR/astdb.txt" \
    /etc/allscan/asdb.txt \
    /var/log/asterisk/astdb.txt; do
    [ -r "$file" ] && printf '%s\n' "$file"
  done
}

detect_callsign() {
  local node="$1" file result
  while IFS= read -r file; do
    result=$(awk -F'|' -v node="$node" '$1 == node && $2 != "" { print toupper($2); exit }' "$file")
    [ -n "$result" ] && { printf '%s\n' "$result"; return 0; }
  done < <(node_db_files)
}

find_bridge_node() {
  local expression="$1" file result
  for file in /etc/allscan/asdb.txt "$ALLSCAN_DIR/asdb.txt"; do
    [ -r "$file" ] || continue
    result=$(grep -iE "$expression" "$file" 2>/dev/null | awk -F'|' '$1 ~ /^[0-9]{3,10}$/ { print $1; exit }' || true)
    [ -n "$result" ] && { printf '%s\n' "$result"; return 0; }
  done
}

service_list=$(systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '{print tolower($1)}' || true)
json_has_bridge() {
  local id="$1"
  [ -r "$ALLSCAN_DIR/bridge-live.json" ] || return 1
  php -r '
    $data = json_decode((string) @file_get_contents($argv[1]), true);
    exit(is_array($data) && !empty($data[$argv[2]]) ? 0 : 1);
  ' "$ALLSCAN_DIR/bridge-live.json" "$id"
}

bridge_detected() {
  local id="$1"
  case "$id" in
    dmr) { grep -E '(^|/)(dmrbridge|mmdvm_bridge|analog_bridge|md380-emu|tgif)[^/]*\.service' <<<"$service_list" | grep -Ev '(ysf|dstar|d-star)'; } >/dev/null || json_has_bridge dmr ;;
    ysf) grep -Eq 'ysf[^/]*\.service' <<<"$service_list" || json_has_bridge ysf ;;
    zello) grep -Eq 'zello[^/]*\.service' <<<"$service_list" || json_has_bridge zello ;;
    dstar) grep -Eq '(dstar|ircddb)[^/]*\.service' <<<"$service_list" || json_has_bridge dstar ;;
    *) return 1 ;;
  esac
}

prompt() {
  local label="$1" default="$2" answer
  if [ "$NON_INTERACTIVE" -eq 1 ] || [ ! -t 0 ]; then
    printf '%s' "$default"
    return
  fi
  read -r -p "$label [$default]: " answer
  printf '%s' "${answer:-$default}"
}

detected_node=$(detect_node)
[ -n "$detected_node" ] || { echo "No 5+ digit AllStar node was found in rpt.conf." >&2; exit 1; }
detected_call=$(detect_callsign "$detected_node")

echo
echo "=== AllScan Reimagined Personalization ==="
node=$(prompt "Primary node number" "$detected_node")
[[ "$node" =~ ^[0-9]{3,10}$ ]] || { echo "Invalid node number." >&2; exit 1; }
callsign=$(prompt "Callsign" "${detected_call:-NODE$node}")
header_title=$(prompt "Header title" "$callsign | Node $node")
browser_title=$(prompt "Browser tab title" "$callsign - $node | ASR")
brand_byline=$(prompt "Brand byline" "by $callsign")
footer_byline=$(prompt "Footer byline" "customized by $callsign")

logo_url="/allscan/asr-logo-bright-r-tight.png"
if [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
  read -r -p "Optional PNG/JPEG/WebP logo path [use included ASR logo]: " logo_path
  if [ -n "${logo_path:-}" ]; then
    [ -f "$logo_path" ] || { echo "Logo file not found: $logo_path" >&2; exit 1; }
    case "${logo_path##*.}" in
      png|PNG) logo_ext="png" ;;
      jpg|JPG|jpeg|JPEG) logo_ext="jpg" ;;
      webp|WEBP) logo_ext="webp" ;;
      *) echo "Logo must be PNG, JPEG, or WebP." >&2; exit 1 ;;
    esac
    mkdir -p "$DATA_DIR"
    install -o root -g root -m 644 "$logo_path" "$DATA_DIR/header-logo.$logo_ext"
    logo_url="/allscan/asr-custom-logo.$logo_ext"
  fi
fi

bridge_file=$(mktemp)
trap 'rm -f "$bridge_file"' EXIT

add_bridge() {
  local id="$1" title="$2" detail="$3" expression="$4" bridge_node
  bridge_detected "$id" || return 0
  bridge_node=$(find_bridge_node "$expression")
  printf '%s\t%s\t%s\t%s\n' "$id" "$bridge_node" "$title" "$detail" >> "$bridge_file"
  printf 'Detected %-6s bridge%s\n' "${id^^}" "${bridge_node:+ on private node $bridge_node}"
}

echo
echo "=== Bridge Detection ==="
add_bridge dmr "DMR Bridge" "Connected Clients" 'DMR|TGIF'
add_bridge ysf "YSF Bridge" "Linked Gateways" 'YSF'
add_bridge zello "Zello Bridge" "Recent Talkers" 'Zello'
add_bridge dstar "D-Star Bridge" "Linked Gateways" 'D-Star|DSTAR|DStar'
[ -s "$bridge_file" ] || echo "No supported bridges detected; bridge cards will be hidden."

mkdir -p "$CONFIG_DIR"
export ASR_NODE="$node" ASR_CALLSIGN="$callsign" ASR_HEADER_TITLE="$header_title"
export ASR_BROWSER_TITLE="$browser_title" ASR_BRAND_BYLINE="$brand_byline"
export ASR_FOOTER_BYLINE="$footer_byline" ASR_LOGO_URL="$logo_url" ASR_BRIDGE_FILE="$bridge_file"
php <<'PHP' > "$CONFIG_FILE.tmp"
<?php
$bridges = [];
$handle = fopen(getenv('ASR_BRIDGE_FILE'), 'r');
while ($handle && ($row = fgetcsv($handle, 0, "\t")) !== false) {
    if (count($row) !== 4) continue;
    $bridges[] = ['id' => $row[0], 'node' => $row[1], 'title' => $row[2], 'detailTitle' => $row[3]];
}
if ($handle) fclose($handle);
$config = [
    'node' => getenv('ASR_NODE'),
    'callsign' => strtoupper(getenv('ASR_CALLSIGN')),
    'headerTitle' => getenv('ASR_HEADER_TITLE'),
    'browserTitle' => getenv('ASR_BROWSER_TITLE'),
    'brandByline' => getenv('ASR_BRAND_BYLINE'),
    'footerByline' => getenv('ASR_FOOTER_BYLINE'),
    'headerLogo' => getenv('ASR_LOGO_URL'),
    'footerLogo' => getenv('ASR_LOGO_URL'),
    'bridges' => $bridges,
];
echo json_encode($config, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), PHP_EOL;
PHP

php -r 'json_decode(file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);' "$CONFIG_FILE.tmp"
install -o root -g "$WEB_GROUP" -m 640 "$CONFIG_FILE.tmp" "$CONFIG_FILE"
rm -f "$CONFIG_FILE.tmp"

echo
echo "Configuration saved: $CONFIG_FILE"
echo "Node: $node"
echo "Callsign: $callsign"
echo "Header: $header_title"
