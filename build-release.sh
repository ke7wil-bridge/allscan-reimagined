#!/bin/bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VERSION=$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/package.json" | head -1)
VERSION_LABEL="v$(printf '%s' "$VERSION" | sed -E 's/-beta\.([0-9]+)/ Beta \1/; s/-test/ Test/; s/-/ /g')"
OUT="$ROOT/release"
STAGE="$OUT/allscan-reimagined-$VERSION"
PACKAGE="$OUT/allscan-reimagined-$VERSION.tar.gz"

command -v pnpm >/dev/null 2>&1 || { echo "pnpm is required." >&2; exit 1; }
[ -n "$VERSION" ] || { echo "package.json version is missing." >&2; exit 1; }

for file in install.sh asr-api.php src/lib/allscanLive.ts; do
  if ! grep -Fq "$VERSION_LABEL" "$ROOT/$file"; then
    echo "$file does not contain expected version label: $VERSION_LABEL" >&2
    exit 1
  fi
done
grep -Fq "ASR_VERSION=\"$VERSION\"" "$ROOT/install.sh" || {
  echo "install.sh ASR_VERSION does not match package.json version: $VERSION" >&2
  exit 1
}

rm -rf "$STAGE"
mkdir -p "$STAGE/payload/web" "$STAGE/payload/server" "$STAGE/payload/bin" "$STAGE/payload/scripts"
mkdir -p "$STAGE/payload/compat"
mkdir -p "$STAGE/docs"

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
install -m 755 scripts/asr-reapply.sh "$STAGE/payload/scripts/asr-reapply.sh"
install -m 755 scripts/asr-integrity-check.sh "$STAGE/payload/scripts/asr-integrity-check.sh"
install -m 755 scripts/asr-asterisk-read.sh "$STAGE/payload/scripts/asr-asterisk-read.sh"
install -m 755 scripts/asr-friendly-names.php "$STAGE/payload/scripts/asr-friendly-names.php"
install -m 755 scripts/asr-bridge-clients.php "$STAGE/payload/scripts/asr-bridge-clients.php"
install -m 755 scripts/asr-manager-perms.sh "$STAGE/payload/scripts/asr-manager-perms.sh"
cp -a compat/. "$STAGE/payload/compat/"
find "$STAGE/payload/compat" -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -delete
install -m 755 install.sh "$STAGE/install.sh"
install -m 644 README.md "$STAGE/README.md"
install -m 644 LICENSE "$STAGE/LICENSE"
install -m 644 ATTRIBUTION.md "$STAGE/ATTRIBUTION.md"
install -m 644 docs/lookup-map.md "$STAGE/docs/lookup-map.md"

find "$STAGE" \( -name '._*' -o -name '.DS_Store' \) -delete
if command -v xattr >/dev/null 2>&1; then
  xattr -cr "$STAGE" 2>/dev/null || true
fi
COPYFILE_DISABLE=1 tar --format ustar --uid 0 --gid 0 --uname root --gname root \
  --exclude='._*' --exclude='.DS_Store' -czf "$PACKAGE" -C "$OUT" "allscan-reimagined-$VERSION"
if command -v xattr >/dev/null 2>&1; then
  xattr -c "$PACKAGE" 2>/dev/null || true
fi
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$PACKAGE" > "$PACKAGE.sha256"
else
  shasum -a 256 "$PACKAGE" > "$PACKAGE.sha256"
fi
echo "Created: $PACKAGE"
cat "$PACKAGE.sha256"
