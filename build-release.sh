#!/bin/bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VERSION=$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/package.json" | head -1)
VERSION_LABEL="v$(printf '%s' "$VERSION" | sed -E 's/-beta\.([0-9]+)/ Beta \1/; s/-test/ Test/; s/-/ /g')"
PUBLIC_BETA_LABEL="Beta ${VERSION#*-beta.}"
OUT="$ROOT/release"
STAGE="$OUT/allscan-reimagined-$VERSION"
PACKAGE="$OUT/allscan-reimagined-$VERSION.tar.gz"

command -v pnpm >/dev/null 2>&1 || { echo "pnpm is required." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "node is required." >&2; exit 1; }
[ -n "$VERSION" ] || { echo "package.json version is missing." >&2; exit 1; }

COMPAT_MANIFEST=$(cat <<'EOF'
allscan-v1.01/LICENSE
allscan-v1.01/asr-instructions/index.php
allscan-v1.01/asr-settings/index.php
allscan-v1.01/asr-settings/rollback-status.php
allscan-v1.01/astapi/AMI.php
allscan-v1.01/astapi/asrEchoLink.php
allscan-v1.01/astapi/server.php
allscan-v1.01/css/asr-admin.css
allscan-v1.01/echolink-lookup/index.php
allscan-v1.01/include/CfgModel.php
allscan-v1.01/include/UserModel.php
allscan-v1.01/include/asrBridgeStatus.php
allscan-v1.01/include/asrFavorites.php
allscan-v1.01/include/asrRuntime.php
allscan-v1.01/include/common.php
allscan-v1.01/include/dbUtils.php
allscan-v1.01/lookup/index.php
allscan-v1.01/performance/index.php
allscan-v1.01/user/settings/index.php
EOF
)
ACTUAL_COMPAT_MANIFEST=$(cd "$ROOT/compat" && find . -type f -print | sed 's#^\./##' | LC_ALL=C sort)
[ -z "$(find "$ROOT/compat" -type l -print -quit)" ] || {
  echo "compat contains a symlink; refusing to package it." >&2
  exit 1
}
[ "$ACTUAL_COMPAT_MANIFEST" = "$COMPAT_MANIFEST" ] || {
  echo "compat file manifest changed; review it before packaging." >&2
  printf '%s\n' "$ACTUAL_COMPAT_MANIFEST" >&2
  exit 1
}

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
grep -Fq "This archive is **$PUBLIC_BETA_LABEL**" "$ROOT/README.md" || {
  echo "README.md public release wording does not match: $PUBLIC_BETA_LABEL" >&2
  exit 1
}
grep -Fq "<p>$PUBLIC_BETA_LABEL keeps the original AllScan" \
  "$ROOT/compat/allscan-v1.01/asr-instructions/index.php" || {
  echo "Help public release wording does not match: $PUBLIC_BETA_LABEL" >&2
  exit 1
}
python3 "$ROOT/scripts/asr-rollback.py" self-test
python3 "$ROOT/scripts/asr-installer-rollback-self-test.py" --self-test
python3 "$ROOT/scripts/asr-bridge-control.py" --self-test
python3 "$ROOT/scripts/asr-ysf-bridge-control.py" --self-test
python3 "$ROOT/scripts/asr-bridge-stale-status-self-test.py"
python3 "$ROOT/scripts/asr-p25-bridge-control.py" self-test
python3 "$ROOT/scripts/asr-nxdn-bridge-control.py" self-test
python3 "$ROOT/scripts/asr-m17-bridge-control.py" --self-test
python3 "$ROOT/scripts/asr-m17-usrp-connector.py" --self-test
python3 "$ROOT/scripts/asr-fixed-bridge-recovery.py" --self-test
python3 "$ROOT/scripts/asr-bridge-lifecycle.py" self-test
python3 "$ROOT/scripts/asr-startup-bridge-summary.py" --self-test
sh -n "$ROOT/scripts/asr-asterisk-read.sh"
sh "$ROOT/scripts/asr-asterisk-read.sh" --self-test
node "$ROOT/scripts/asr-bridge-dashboard-self-test.mjs"
node "$ROOT/scripts/asr-favorites-placement-self-test.mjs"
python3 "$ROOT/scripts/asr-protected-config-metadata.py" --self-test
bash "$ROOT/scripts/asr-side-by-side-self-test.sh"
python3 "$ROOT/scripts/asr-favorites-update.py" --self-test
python3 "$ROOT/scripts/asr-favorites-source.py" --self-test
python3 "$ROOT/scripts/asr-loopback-validate.py" --self-test
python3 "$ROOT/scripts/asr-loopback-validate-integration-self-test.py"
python3 "$ROOT/scripts/asr-installer-prompts-self-test.py"
python3 "$ROOT/scripts/asr-instructions-self-test.py"
python3 "$ROOT/scripts/asr-stock-count-helper.py" --self-test
node "$ROOT/scripts/asr-lookup-map-browser-self-test.mjs"
if command -v php >/dev/null 2>&1; then
	php -l "$ROOT/compat/allscan-v1.01/astapi/AMI.php" >/dev/null
	php -l "$ROOT/compat/allscan-v1.01/astapi/server.php" >/dev/null
	php -l "$ROOT/compat/allscan-v1.01/astapi/asrEchoLink.php" >/dev/null
	php "$ROOT/scripts/asr-bridge-clients.php" --self-test
  php "$ROOT/scripts/asr-settings-bridge-self-test.php"
  php "$ROOT/scripts/asr-bridge-status-privacy-self-test.php"
  php "$ROOT/scripts/asr-echolink-self-test.php"
  php "$ROOT/scripts/asr-favorites-discovery-self-test.php"
  php "$ROOT/scripts/asr-runtime-source-self-test.php"
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
install -m 755 scripts/asr-protected-config-metadata.py "$STAGE/payload/scripts/asr-protected-config-metadata.py"
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
install -m 755 scripts/asr-ysf-bridge-control.py "$STAGE/payload/scripts/asr-ysf-bridge-control.py"
install -m 644 scripts/asr_bridge_status.py "$STAGE/payload/scripts/asr_bridge_status.py"
install -m 755 scripts/asr-bridge-stale-status-self-test.py "$STAGE/payload/scripts/asr-bridge-stale-status-self-test.py"
install -m 755 scripts/asr-p25-bridge-control.py "$STAGE/payload/scripts/asr-p25-bridge-control.py"
install -m 755 scripts/asr-nxdn-bridge-control.py "$STAGE/payload/scripts/asr-nxdn-bridge-control.py"
install -m 755 scripts/asr-m17-bridge-control.py "$STAGE/payload/scripts/asr-m17-bridge-control.py"
install -m 755 scripts/asr-m17-usrp-connector.py "$STAGE/payload/scripts/asr-m17-usrp-connector.py"
install -m 755 scripts/asr-fixed-bridge-recovery.py "$STAGE/payload/scripts/asr-fixed-bridge-recovery.py"
install -m 755 scripts/asr-bridge-lifecycle.py "$STAGE/payload/scripts/asr-bridge-lifecycle.py"
install -m 755 scripts/asr-startup-bridge-summary.py "$STAGE/payload/scripts/asr-startup-bridge-summary.py"
install -m 755 scripts/asr-settings-bridge-self-test.php "$STAGE/payload/scripts/asr-settings-bridge-self-test.php"
install -m 755 scripts/asr-bridge-status-privacy-self-test.php "$STAGE/payload/scripts/asr-bridge-status-privacy-self-test.php"
install -m 755 scripts/asr-echolink-self-test.php "$STAGE/payload/scripts/asr-echolink-self-test.php"
install -m 755 scripts/asr-side-by-side-self-test.sh "$STAGE/payload/scripts/asr-side-by-side-self-test.sh"
install -m 755 scripts/asr-favorites-update.py "$STAGE/payload/scripts/asr-favorites-update.py"
install -m 755 scripts/asr-favorites-source.py "$STAGE/payload/scripts/asr-favorites-source.py"
install -m 755 scripts/asr-loopback-validate.py "$STAGE/payload/scripts/asr-loopback-validate.py"
install -m 755 scripts/asr-loopback-validate-integration-self-test.py "$STAGE/payload/scripts/asr-loopback-validate-integration-self-test.py"
install -m 755 scripts/asr-favorites-discovery-self-test.php "$STAGE/payload/scripts/asr-favorites-discovery-self-test.php"
install -m 755 scripts/asr-installer-prompts-self-test.py "$STAGE/payload/scripts/asr-installer-prompts-self-test.py"
install -m 755 scripts/asr-instructions-self-test.py "$STAGE/payload/scripts/asr-instructions-self-test.py"
install -m 755 scripts/asr-stock-count-helper.py "$STAGE/payload/scripts/asr-stock-count-helper.py"
install -m 755 scripts/asr-lookup-map-self-test.php "$STAGE/payload/scripts/asr-lookup-map-self-test.php"
install -m 755 scripts/asr-lookup-map-browser-self-test.mjs "$STAGE/payload/scripts/asr-lookup-map-browser-self-test.mjs"
install -m 755 scripts/asr-access-policy-self-test.php "$STAGE/payload/scripts/asr-access-policy-self-test.php"
install -m 755 scripts/asr-runtime-source-self-test.php "$STAGE/payload/scripts/asr-runtime-source-self-test.php"
while IFS= read -r compat_file; do
  mkdir -p "$STAGE/payload/compat/$(dirname "$compat_file")"
  install -m 644 "compat/$compat_file" "$STAGE/payload/compat/$compat_file"
done <<< "$COMPAT_MANIFEST"
install -m 755 install.sh "$STAGE/install.sh"
install -m 644 README.md "$STAGE/README.md"
install -m 644 LICENSE "$STAGE/LICENSE"
install -m 644 ATTRIBUTION.md "$STAGE/ATTRIBUTION.md"
install -m 644 docs/lookup-map.md "$STAGE/docs/lookup-map.md"
install -m 644 release-notes/v1.0.0-beta.7.4.md "$STAGE/release-notes/v1.0.0-beta.7.4.md"

if command -v php >/dev/null 2>&1; then
  php -l "$STAGE/payload/compat/allscan-v1.01/astapi/AMI.php" >/dev/null
  php -l "$STAGE/payload/compat/allscan-v1.01/astapi/server.php" >/dev/null
  php -l "$STAGE/payload/compat/allscan-v1.01/astapi/asrEchoLink.php" >/dev/null
  php -l "$STAGE/payload/compat/allscan-v1.01/include/asrBridgeStatus.php" >/dev/null
  php "$STAGE/payload/scripts/asr-bridge-status-privacy-self-test.php"
  php "$STAGE/payload/scripts/asr-echolink-self-test.php"
fi
sh -n "$STAGE/payload/scripts/asr-asterisk-read.sh"
sh "$STAGE/payload/scripts/asr-asterisk-read.sh" --self-test
python3 "$STAGE/payload/scripts/asr-installer-rollback-self-test.py" --self-test
python3 "$STAGE/payload/scripts/asr-bridge-stale-status-self-test.py"

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
