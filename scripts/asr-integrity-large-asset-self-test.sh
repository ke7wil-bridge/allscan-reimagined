#!/bin/bash
set -Eeuo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Extract the exact production helper without executing the integrity checker.
helper=$(sed -n '/^files_match() {/,/^}/p' "$SCRIPT_DIR/asr-integrity-check.sh")
[ -n "$helper" ]
eval "$helper"
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
python3 - "$root" <<'PYTEST'
from pathlib import Path
import sys
root = Path(sys.argv[1])
(root / 'master.js').write_bytes(b'A' * (2 * 1024 * 1024))
(root / 'installed.js').write_bytes(b'B' * (2 * 1024 * 1024))
PYTEST
if files_match "$root/master.js" "$root/installed.js"; then
  echo 'ERROR: equal-size stale frontend asset was accepted' >&2
  exit 1
fi
cp "$root/master.js" "$root/installed.js"
files_match "$root/master.js" "$root/installed.js"
echo 'ASR integrity large-asset content regression: PASS'
