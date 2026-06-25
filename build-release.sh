#!/bin/bash
set -Eeuo pipefail

VERSION="1.0.0-beta.5"
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT="$ROOT/release"
STAGE="$OUT/allscan-reimagined-$VERSION"
PACKAGE="$OUT/allscan-reimagined-$VERSION.tar.gz"

command -v pnpm >/dev/null 2>&1 || { echo "pnpm is required." >&2; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE/payload/web" "$STAGE/payload/server" "$STAGE/payload/bin" "$STAGE/payload/scripts"
mkdir -p "$STAGE/payload/compat"

cd "$ROOT"
ASR_BASE_PATH=/allscan/ pnpm run build
cp -a dist/. "$STAGE/payload/web/"
find "$STAGE/payload/web" -maxdepth 1 -type f \
  ! -name 'index.html' \
  ! -name 'favicon-bolt-r-c.png' \
  ! -name 'asr-logo-bright-r-tight.png' \
  ! -name 'bolt-test-tight.png' \
  -delete
install -m 644 asr-api.php "$STAGE/payload/server/asr-api.php"
install -m 755 allscan_wt_clients.sh "$STAGE/payload/bin/allscan_wt_clients.sh"
install -m 755 scripts/asr-configure.sh "$STAGE/payload/scripts/asr-configure.sh"
install -m 755 scripts/asr-tgif-client-tracking.sh "$STAGE/payload/scripts/asr-tgif-client-tracking.sh"
install -m 755 scripts/asr-reapply.sh "$STAGE/payload/scripts/asr-reapply.sh"
install -m 755 scripts/asr-integrity-check.sh "$STAGE/payload/scripts/asr-integrity-check.sh"
cp -a compat/. "$STAGE/payload/compat/"
install -m 755 install.sh "$STAGE/install.sh"
install -m 644 README.md "$STAGE/README.md"
install -m 644 LICENSE "$STAGE/LICENSE"
install -m 644 ATTRIBUTION.md "$STAGE/ATTRIBUTION.md"

find "$STAGE" \( -name '._*' -o -name '.DS_Store' \) -delete
COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='.DS_Store' -czf "$PACKAGE" -C "$OUT" "allscan-reimagined-$VERSION"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$PACKAGE" > "$PACKAGE.sha256"
else
  shasum -a 256 "$PACKAGE" > "$PACKAGE.sha256"
fi
echo "Created: $PACKAGE"
cat "$PACKAGE.sha256"
