#!/bin/bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VERSION=$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/package.json" | head -1)
VERSION_LABEL="v$(printf '%s' "$VERSION" | sed -E 's/-beta\.([0-9]+)/ Beta \1/; s/-test/ Test/; s/-/ /g')"
OUT="$ROOT/release"
STAGE="$OUT/allscan-reimagined-$VERSION"
PACKAGE="$OUT/allscan-reimagined-$VERSION.tar.gz"

command -v pnpm >/dev/null 2>&1 || { echo "pnpm is required." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "node is required." >&2; exit 1; }
[ -n "$VERSION" ] || { echo "package.json version is missing." >&2; exit 1; }

for file in install.sh asr-api.php src/lib/allscanLive.ts scripts/asr-release-check.py compat/allscan-v1.01/include/common.php; do
  if ! grep -Fq "$VERSION_LABEL" "$ROOT/$file"; then
    echo "$file does not contain expected version label: $VERSION_LABEL" >&2
    exit 1
  fi
done
grep -Fq "ASR_VERSION=\"$VERSION\"" "$ROOT/install.sh" || {
  echo "install.sh ASR_VERSION does not match package.json version: $VERSION" >&2
  exit 1
}
grep -Fq "const ASR_VERSION = '$VERSION';" "$ROOT/asr-api.php" || {
  echo "asr-api.php ASR_VERSION does not match package.json version: $VERSION" >&2
  exit 1
}
grep -Fq "ASR_INSTALLED_VERSION = \"$VERSION\"" "$ROOT/scripts/asr-release-check.py" || {
  echo "asr-release-check.py installed version does not match package.json version: $VERSION" >&2
  exit 1
}
grep -Fq '<title>AllScan Reimagined</title>' "$ROOT/index.html" || {
  echo "index.html must use the generic pre-configuration browser title." >&2
  exit 1
}
python3 "$ROOT/scripts/asr-rollback.py" self-test
python3 "$ROOT/scripts/asr-installer-rollback-self-test.py" --self-test
python3 "$ROOT/scripts/asr-bridge-control.py" --self-test
bash "$ROOT/scripts/asr-side-by-side-self-test.sh"
python3 "$ROOT/scripts/asr-favorites-update.py" --self-test
python3 "$ROOT/scripts/asr-favorites-source.py" --self-test
python3 "$ROOT/scripts/asr-instructions-self-test.py"
python3 "$ROOT/scripts/asr-stock-count-helper.py" --self-test
node "$ROOT/scripts/asr-lookup-map-browser-self-test.mjs"
if command -v php >/dev/null 2>&1; then
  php "$ROOT/scripts/asr-lookup-map-self-test.php"
  php "$ROOT/scripts/asr-access-policy-self-test.php"
else
  echo "PHP is not available locally; packaged PHP tests must pass on the target node."
fi

rm -rf "$STAGE"
mkdir -p "$STAGE/payload/web" "$STAGE/payload/server" "$STAGE/payload/bin" "$STAGE/payload/scripts"
mkdir -p "$STAGE/payload/compat"
mkdir -p "$STAGE/docs"
mkdir -p "$STAGE/release-notes"

cd "$ROOT"
ASR_BASE_PATH=/asr/ pnpm run build
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
install -m 755 scripts/asr-favorites-permissions.sh "$STAGE/payload/scripts/asr-favorites-permissions.sh"
install -m 755 scripts/asr-patch-connected-clients.py "$STAGE/payload/scripts/asr-patch-connected-clients.py"
install -m 755 scripts/asr-migrate-tgif-environment.py "$STAGE/payload/scripts/asr-migrate-tgif-environment.py"
install -m 755 scripts/asr-patch-allscan-index.py "$STAGE/payload/scripts/asr-patch-allscan-index.py"
install -m 755 scripts/asr-release-check.py "$STAGE/payload/scripts/asr-release-check.py"
install -m 755 scripts/asr-rollback.py "$STAGE/payload/scripts/asr-rollback.py"
install -m 755 scripts/asr-installer-rollback-self-test.py "$STAGE/payload/scripts/asr-installer-rollback-self-test.py"
install -m 755 scripts/asr-bridge-control.py "$STAGE/payload/scripts/asr-bridge-control.py"
install -m 755 scripts/asr-side-by-side-self-test.sh "$STAGE/payload/scripts/asr-side-by-side-self-test.sh"
install -m 755 scripts/asr-favorites-update.py "$STAGE/payload/scripts/asr-favorites-update.py"
install -m 755 scripts/asr-favorites-source.py "$STAGE/payload/scripts/asr-favorites-source.py"
install -m 755 scripts/asr-instructions-self-test.py "$STAGE/payload/scripts/asr-instructions-self-test.py"
install -m 755 scripts/asr-stock-count-helper.py "$STAGE/payload/scripts/asr-stock-count-helper.py"
install -m 755 scripts/asr-lookup-map-self-test.php "$STAGE/payload/scripts/asr-lookup-map-self-test.php"
install -m 755 scripts/asr-lookup-map-browser-self-test.mjs "$STAGE/payload/scripts/asr-lookup-map-browser-self-test.mjs"
install -m 755 scripts/asr-access-policy-self-test.php "$STAGE/payload/scripts/asr-access-policy-self-test.php"
cp -a compat/. "$STAGE/payload/compat/"
find "$STAGE/payload/compat" -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -delete
install -m 755 install.sh "$STAGE/install.sh"
install -m 644 README.md "$STAGE/README.md"
install -m 644 LICENSE "$STAGE/LICENSE"
install -m 644 ATTRIBUTION.md "$STAGE/ATTRIBUTION.md"
install -m 644 docs/lookup-map.md "$STAGE/docs/lookup-map.md"
install -m 644 release-notes/v1.0.0-beta.6.md "$STAGE/release-notes/v1.0.0-beta.6.md"

find "$STAGE" \( -name '._*' -o -name '.DS_Store' \) -delete
if command -v xattr >/dev/null 2>&1; then
  xattr -cr "$STAGE" 2>/dev/null || true
fi
COPYFILE_DISABLE=1 tar --no-xattrs --format ustar --uid 0 --gid 0 --uname root --gname root \
  --exclude='._*' --exclude='.DS_Store' -czf "$PACKAGE" -C "$OUT" "allscan-reimagined-$VERSION"
if command -v xattr >/dev/null 2>&1; then
  xattr -c "$PACKAGE" 2>/dev/null || true
fi
if command -v sha256sum >/dev/null 2>&1; then
  (
    cd "$OUT"
    sha256sum "$(basename "$PACKAGE")"
  ) > "$PACKAGE.sha256"
else
  (
    cd "$OUT"
    shasum -a 256 "$(basename "$PACKAGE")"
  ) > "$PACKAGE.sha256"
fi
echo "Created: $PACKAGE"
cat "$PACKAGE.sha256"
