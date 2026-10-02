#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CHROME=${CHROME:-$(command -v google-chrome || command -v chromium || true)}
[ -n "$CHROME" ] || { echo "Chrome is unavailable; skipping browser tab self-test." >&2; exit 77; }
PROFILE=$(mktemp -d)
trap 'rm -rf "$PROFILE"' EXIT INT TERM
for VIEWPORT in 1440,1000 768,1024 1024,768 390,844 844,390; do
  OUTPUT=$(
    "$CHROME" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
      --allow-file-access-from-files --user-data-dir="$PROFILE" --window-size="$VIEWPORT" --dump-dom \
      "file://$ROOT/scripts/fixtures/settings-bridge-tabs.html?viewport=$VIEWPORT" 2>/dev/null
  )
  printf '%s' "$OUTPUT" | grep -F 'data-result="pass"' >/dev/null || {
    printf '%s\n' "$OUTPUT" >&2
    echo "ASR bridge editor browser tab self-test failed at $VIEWPORT" >&2
    exit 1
  }
  echo "ASR bridge editor browser tab self-test passed at $VIEWPORT"
done
