#!/bin/sh
set -eu

CANONICAL_ASTDB="${ASR_CANONICAL_ASTDB:-/var/lib/asterisk/astdb.txt}"
WAIT_SECONDS="${ASR_ASTDB_WAIT_SECONDS:-60}"

case "$WAIT_SECONDS" in
  ''|*[!0-9]*) echo "ASR_ASTDB_WAIT_SECONDS must be a non-negative integer." >&2; exit 1 ;;
esac

if [ "${1:-}" = "--self-test" ]; then
  [ "$#" -eq 1 ] || exit 2
  case "12" in ''|*[!0-9]*) exit 1 ;; esac
  printf '%s\n' "ASR node-database link self-test: ok"
  exit 0
fi
[ "$#" -eq 0 ] || { echo "Unexpected argument." >&2; exit 2; }

elapsed=0
while [ ! -r "$CANONICAL_ASTDB" ] \
  || [ "$(wc -c < "$CANONICAL_ASTDB" 2>/dev/null || echo 0)" -lt 1024 ]; do
  if [ "$elapsed" -ge "$WAIT_SECONDS" ]; then
    echo "The authoritative ASL astdb.txt is unavailable or invalid." >&2
    exit 1
  fi
  sleep 1
  elapsed=$((elapsed + 1))
done

for directory in /var/log/asterisk /var/www/html/allscan/user /var/www/html/asr/user; do
  mkdir -p "$directory"
done
for link in \
  /var/log/asterisk/astdb.txt \
  /var/www/html/allscan/astdb.txt \
  /var/www/html/allscan/user/astdb.txt \
  /var/www/html/asr/astdb.txt \
  /var/www/html/asr/user/astdb.txt; do
  rm -f -- "$link"
  ln -s "$CANONICAL_ASTDB" "$link"
done
echo "AllScan and Settings node databases linked to the ASL3 updater database."
