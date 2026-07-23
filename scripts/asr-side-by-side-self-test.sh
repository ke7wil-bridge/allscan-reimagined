#!/bin/bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

grep -Fq "! -path './bridge-live.json'" "$SCRIPT_DIR/asr-reapply.sh"
grep -Fq "! -path './connected-clients.json'" "$SCRIPT_DIR/asr-reapply.sh"
grep -Fq "! -path './zello-status-data.json'" "$SCRIPT_DIR/asr-reapply.sh"
TEST_ROOT=$(mktemp -d /tmp/asr-side-by-side-self-test.XXXXXX)
trap 'rm -rf -- "$TEST_ROOT"' EXIT

WEB_ROOT="$TEST_ROOT/www"
MASTER_DIR="$TEST_ROOT/master"
STOCK_ALLSCAN_DIR="$WEB_ROOT/allscan"
ASR_WEB_DIR="$WEB_ROOT/asr"
mkdir -p \
  "$STOCK_ALLSCAN_DIR/include" \
  "$TEST_ROOT/etc/allscan" \
  "$MASTER_DIR/web/assets" \
  "$MASTER_DIR/server" \
  "$MASTER_DIR/compat/allscan-v1.01/include"

printf '%s\n' 'stock sentinel' > "$STOCK_ALLSCAN_DIR/stock-sentinel.txt"
printf '%s\n' '$AllScanVersion = "v1.01";' > "$STOCK_ALLSCAN_DIR/include/common.php"
printf '%s\n' '<?php echo "stock";' > "$STOCK_ALLSCAN_DIR/index.php"
printf '%s\n' '<html>stock</html>' > "$STOCK_ALLSCAN_DIR/index.html"
printf '%s\n' '1000=Shared favorite' > "$TEST_ROOT/etc/allscan/favorites.ini"
ln -s "$TEST_ROOT/etc/allscan/favorites.ini" "$STOCK_ALLSCAN_DIR/favorites.ini"
printf '%s\n' '<script src="/asr/assets/index-test.js"></script>' > "$MASTER_DIR/web/index.html"
printf '%s\n' 'console.log("asr");' > "$MASTER_DIR/web/assets/index-test.js"
printf '%s\n' '<?php const ASR_VERSION = "1.0.0-beta.6.0";' > "$MASTER_DIR/server/asr-api.php"
printf '%s\n' '$AllScanVersion = "v1.01"; // ASR compatibility' \
  > "$MASTER_DIR/compat/allscan-v1.01/include/common.php"

tree_digest() {
  (
    cd "$1"
    find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
  ) | sha256sum | awk '{print $1}'
}

stock_before=$(tree_digest "$STOCK_ALLSCAN_DIR")
ASR_MASTER_DIR="$MASTER_DIR" \
ASR_WEB_ROOT="$WEB_ROOT" \
STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" \
ASR_WEB_DIR="$ASR_WEB_DIR" \
ASR_INSTALL_LOCK_HELD=1 \
ASR_REAPPLY_WEB_ONLY=1 \
  bash "$SCRIPT_DIR/asr-reapply.sh"
stock_after=$(tree_digest "$STOCK_ALLSCAN_DIR")

[ "$stock_before" = "$stock_after" ]
[ "$(cat "$ASR_WEB_DIR/stock-sentinel.txt")" = "stock sentinel" ]
grep -q 'ASR compatibility' "$ASR_WEB_DIR/include/common.php"
grep -q '/asr/assets/index-test.js' "$ASR_WEB_DIR/index.html"
test -f "$ASR_WEB_DIR/asr-api.php"
test -L "$ASR_WEB_DIR/favorites.ini"

# An unsupported stock backend must leave the last working /asr untouched.
mv "$MASTER_DIR/compat/allscan-v1.01" "$MASTER_DIR/compat/allscan-v1.01.saved"
printf '%s\n' '<html>unsupported replacement</html>' > "$MASTER_DIR/web/index.html"
unsupported_status=0
ASR_MASTER_DIR="$MASTER_DIR" \
ASR_WEB_ROOT="$WEB_ROOT" \
STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" \
ASR_WEB_DIR="$ASR_WEB_DIR" \
ASR_INSTALL_LOCK_HELD=1 \
ASR_REAPPLY_WEB_ONLY=1 \
  bash "$SCRIPT_DIR/asr-reapply.sh" >/dev/null 2>&1 || unsupported_status=$?
[ "$unsupported_status" -ne 0 ]
grep -q '/asr/assets/index-test.js' "$ASR_WEB_DIR/index.html"
[ "$(cat "$ASR_WEB_DIR/favorites.ini")" = "1000=Shared favorite" ]
[ "$stock_before" = "$(tree_digest "$STOCK_ALLSCAN_DIR")" ]
mv "$MASTER_DIR/compat/allscan-v1.01.saved" "$MASTER_DIR/compat/allscan-v1.01"
printf '%s\n' '<script src="/asr/assets/index-test.js"></script>' > "$MASTER_DIR/web/index.html"

printf '%s\n' 'damaged ASR only' > "$ASR_WEB_DIR/index.html"
ASR_MASTER_DIR="$MASTER_DIR" \
ASR_WEB_ROOT="$WEB_ROOT" \
STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" \
ASR_WEB_DIR="$ASR_WEB_DIR" \
ASR_INSTALL_LOCK_HELD=1 \
ASR_INTEGRITY_WEB_ONLY=1 \
ASR_REAPPLY_COMMAND="$SCRIPT_DIR/asr-reapply.sh" \
  bash "$SCRIPT_DIR/asr-integrity-check.sh"

[ "$stock_before" = "$(tree_digest "$STOCK_ALLSCAN_DIR")" ]
grep -q '/asr/assets/index-test.js' "$ASR_WEB_DIR/index.html"

archive="$TEST_ROOT/asr-webroot.tar.gz"
ln -sfn /etc/allscan/favorites.ini "$ASR_WEB_DIR/favorites.ini"
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$archive" -C "$WEB_ROOT" asr
python3 - "$archive" "$SCRIPT_DIR/asr-rollback.py" <<'PY'
import importlib.util
import sys
import tarfile

archive_path, module_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("asr_rollback_test", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
module.validate_archive(module.Path(archive_path), "webroot")
with tarfile.open(archive_path, "r:gz") as archive:
    names = [member.name.removeprefix("./") for member in archive.getmembers()]
assert names and all(name == "asr" or name.startswith("asr/") for name in names)
assert not any(name == "allscan" or name.startswith("allscan/") for name in names)
PY

# Exercise the same schema-2 web swap boundary used by rollback: extract asr/,
# replace only /asr, and prove the stock sentinel tree is byte-for-byte unchanged.
printf '%s\n' 'post-backup ASR change' > "$ASR_WEB_DIR/index.html"
rollback_stage="$WEB_ROOT/.asr-rollback-self-test"
mkdir "$rollback_stage"
COPYFILE_DISABLE=1 tar -xzf "$archive" -C "$rollback_stage"
mv "$ASR_WEB_DIR" "$WEB_ROOT/.asr-rollback-old"
mv "$rollback_stage/asr" "$ASR_WEB_DIR"
rm -rf -- "$rollback_stage" "$WEB_ROOT/.asr-rollback-old"
[ "$stock_before" = "$(tree_digest "$STOCK_ALLSCAN_DIR")" ]
grep -q '/asr/assets/index-test.js' "$ASR_WEB_DIR/index.html"

echo "ASR side-by-side self-test: ok"
