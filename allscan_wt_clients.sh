#!/bin/bash
set -u

AST="/usr/sbin/asterisk"
CONFIG_FILE="/etc/allscan-reimagined/config.json"
NODE_NUM=""

if [ -r "$CONFIG_FILE" ] && command -v php >/dev/null 2>&1; then
  NODE_NUM=$(php -r '
    $config = json_decode((string) @file_get_contents($argv[1]), true);
    $node = is_array($config) ? (string) ($config["node"] ?? "") : "";
    if (preg_match("/^\\d{3,10}$/", $node)) echo $node;
  ' "$CONFIG_FILE")
fi

if [ -z "$NODE_NUM" ] && [ -r /etc/asterisk/rpt.conf ]; then
  NODE_NUM=$(sed -n 's/^\[\([0-9]\{5,10\}\)\]$/\1/p' /etc/asterisk/rpt.conf | head -1)
fi

if ! [[ "$NODE_NUM" =~ ^[0-9]{3,10}$ ]]; then
  echo "Error: local node number could not be detected" >&2
  exit 4
fi

if [ "$EUID" -ne 0 ]; then
  echo "Error: must run as root" >&2
  exit 1
fi

if [ "${1:-}" = "drop" ]; then
  CHANNEL="${2:-}"
  if ! [[ "$CHANNEL" =~ ^IAX2/[A-Za-z0-9_.@:+-]+-[0-9]+$ ]]; then
    echo "Error: unsafe channel name" >&2
    exit 2
  fi

  OUTPUT=$("$AST" -rx "channel request hangup $CHANNEL" 2>&1)
  if echo "$OUTPUT" | grep -Eqi 'requested hangup|success'; then
    printf 'Drop requested: %s\n' "$CHANNEL"
    exit 0
  fi

  printf '%s\n' "$OUTPUT" >&2
  exit 3
fi

"$AST" -rx "core show channels concise" | awk -F'!' '/^IAX2\// {print $1}' | while read -r CHANNEL; do
  if echo "$CHANNEL" | grep -q '^IAX2/allstar-public-'; then
    IAX_ID="${CHANNEL##*-}"
    PADDED_ID=$(printf "%05d" "$IAX_ID" 2>/dev/null)

    IP=$("$AST" -rx "iax2 show channels" | awk -v id="$PADDED_ID" '$4 ~ "^" id "/" {print $2; exit}')
    [ -z "$IP" ] && continue

    CALL=$("$AST" -rx "rpt lstats $NODE_NUM" | awk -v ip="$IP" '$2 == ip && $4 == "IN" {print $1; exit}')
    [ -z "$CALL" ] && CALL="Web Transceiver"

    printf '%s|%s|%s\n' "$CALL (Web Transceiver)" "$IP" "$CHANNEL"
    continue
  fi

  NAME="${CHANNEL#IAX2/}"
  NAME="${NAME%-*}"

  case "$NAME" in
    127.0.0.1:*|radio-secure|radio)
      continue
      ;;
  esac

  if echo "$NAME" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}(:[0-9]+)?$'; then
    continue
  fi

  printf '%s|%s|%s\n' "$NAME" "0.0.0.0" "$CHANNEL"
done
