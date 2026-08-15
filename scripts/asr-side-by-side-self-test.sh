#!/bin/bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

grep -Fq 'is_stock_status_artifact()' "$SCRIPT_DIR/asr-reapply.sh"
grep -Fq 'public-status.json' "$SCRIPT_DIR/asr-reapply.sh"
grep -Fq '.*-public-status-talker-live.json' "$SCRIPT_DIR/asr-reapply.sh"
grep -Fq '.connected-clients.*.json' "$SCRIPT_DIR/asr-reapply.sh"
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
  "$MASTER_DIR/scripts" \
  "$MASTER_DIR/compat/allscan-v1.01/include" \
  "$MASTER_DIR/compat/allscan-v1.01/astapi"

printf '%s\n' 'stock sentinel' > "$STOCK_ALLSCAN_DIR/stock-sentinel.txt"
printf '%s\n' '$AllScanVersion = "v1.01";' > "$STOCK_ALLSCAN_DIR/include/common.php"
printf '%s\n' '<?php echo "stock";' > "$STOCK_ALLSCAN_DIR/index.php"
printf '%s\n' '<html>stock</html>' > "$STOCK_ALLSCAN_DIR/index.html"
printf '%s\n' '{"guarded":true}' > "$STOCK_ALLSCAN_DIR/include/public-status.json"
printf '%s\n' '{"runtime":true}' > "$STOCK_ALLSCAN_DIR/public-status.json"
printf '%s\n' '{"runtime":true}' > "$STOCK_ALLSCAN_DIR/.public-status-talkers.json"
printf '%s\n' '{"runtime":true}' > "$STOCK_ALLSCAN_DIR/.example-public-status-talker-live.json"
printf '%s\n' '1000=Shared favorite' > "$TEST_ROOT/etc/allscan/favorites.ini"
ln -s "$TEST_ROOT/etc/allscan/favorites.ini" "$STOCK_ALLSCAN_DIR/favorites.ini"
printf '%s\n' '<script src="/asr/assets/index-test.js"></script>' > "$MASTER_DIR/web/index.html"
printf '%s\n' 'console.log("asr");' > "$MASTER_DIR/web/assets/index-test.js"
printf '%s\n' '<?php const ASR_VERSION = "1.0.0-beta.6.0";' > "$MASTER_DIR/server/asr-api.php"
cat > "$MASTER_DIR/scripts/asr-protected-config-metadata.py" <<PY
raise SystemExit("web-only reapply invoked protected-config metadata repair")
PY
printf '%s\n' '$AllScanVersion = "v1.01"; // ASR compatibility' \
  > "$MASTER_DIR/compat/allscan-v1.01/include/common.php"
printf '%s\n' '<?php // EchoLink compatibility sentinel' \
  > "$MASTER_DIR/compat/allscan-v1.01/astapi/asrEchoLink.php"

tree_digest() {
  (
    cd "$1"
    find . -type f \
      ! -path './public-status.json' \
      ! -path './.public-status-talkers.json' \
      ! -path './.example-public-status-talker-live.json' \
      -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
  ) | sha256sum | awk '{print $1}'
}

stock_before=$(tree_digest "$STOCK_ALLSCAN_DIR")
writer_flag="$TEST_ROOT/runtime-writer-active"
: > "$writer_flag"
(
  i=0
  while [ -e "$writer_flag" ]; do
    i=$((i + 1))
    runtime_tmp="$STOCK_ALLSCAN_DIR/.public-status.$i.json"
    printf '{"runtime":%s}\n' "$i" > "$runtime_tmp"
    sleep 0.005
    mv "$runtime_tmp" "$STOCK_ALLSCAN_DIR/public-status.json"
    sleep 0.01
  done
) &
writer_pid=$!
sleep 0.05
ASR_MASTER_DIR="$MASTER_DIR" \
ASR_WEB_ROOT="$WEB_ROOT" \
STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" \
ASR_WEB_DIR="$ASR_WEB_DIR" \
ASR_INSTALL_LOCK_HELD=1 \
ASR_REAPPLY_WEB_ONLY=1 \
  bash "$SCRIPT_DIR/asr-reapply.sh"
rm -f -- "$writer_flag"
wait "$writer_pid"
stock_after=$(tree_digest "$STOCK_ALLSCAN_DIR")

[ "$stock_before" = "$stock_after" ]
[ "$(cat "$ASR_WEB_DIR/stock-sentinel.txt")" = "stock sentinel" ]
grep -q 'ASR compatibility' "$ASR_WEB_DIR/include/common.php"
grep -q '/asr/assets/index-test.js' "$ASR_WEB_DIR/index.html"
test -f "$ASR_WEB_DIR/asr-api.php"
test -L "$ASR_WEB_DIR/favorites.ini"
test ! -e "$ASR_WEB_DIR/public-status.json"
test ! -e "$ASR_WEB_DIR/.public-status-talkers.json"
test ! -e "$ASR_WEB_DIR/.example-public-status-talker-live.json"
test -f "$ASR_WEB_DIR/include/public-status.json"

# A status-name symlink is never adopted into the web tree.
rm "$STOCK_ALLSCAN_DIR/.public-status-talkers.json"
ln -s "$TEST_ROOT/etc/allscan/favorites.ini" \
  "$STOCK_ALLSCAN_DIR/.public-status-talkers.json"
symlink_status=0
ASR_MASTER_DIR="$MASTER_DIR" \
ASR_WEB_ROOT="$WEB_ROOT" \
STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" \
ASR_WEB_DIR="$ASR_WEB_DIR" \
ASR_INSTALL_LOCK_HELD=1 \
ASR_REAPPLY_WEB_ONLY=1 \
  bash "$SCRIPT_DIR/asr-reapply.sh" >/dev/null 2>&1 || symlink_status=$?
[ "$symlink_status" -ne 0 ]
test ! -e "$ASR_WEB_DIR/.public-status-talkers.json"
test ! -L "$ASR_WEB_DIR/.public-status-talkers.json"
test -z "$(find "$WEB_ROOT" -mindepth 1 -maxdepth 1 -name '.asr-reapply.*' -print -quit)"
rm "$STOCK_ALLSCAN_DIR/.public-status-talkers.json"
printf '%s\n' '{"runtime":true}' > "$STOCK_ALLSCAN_DIR/.public-status-talkers.json"

# A changing unlisted stock file must still trip the isolation guard and leave
# the working /asr tree untouched.
guard_writer_flag="$TEST_ROOT/guard-writer-active"
: > "$guard_writer_flag"
(
  i=0
  while [ -e "$guard_writer_flag" ]; do
    i=$((i + 1))
    printf '{"guarded":%s}\n' "$i" \
      > "$STOCK_ALLSCAN_DIR/include/public-status.json"
    sleep 0.01
  done
) &
guard_writer_pid=$!
sleep 0.05
guard_status=0
ASR_MASTER_DIR="$MASTER_DIR" \
ASR_WEB_ROOT="$WEB_ROOT" \
STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" \
ASR_WEB_DIR="$ASR_WEB_DIR" \
ASR_INSTALL_LOCK_HELD=1 \
ASR_REAPPLY_WEB_ONLY=1 \
  bash "$SCRIPT_DIR/asr-reapply.sh" >/dev/null 2>&1 || guard_status=$?
rm -f -- "$guard_writer_flag"
wait "$guard_writer_pid"
[ "$guard_status" -ne 0 ]
grep -q '/asr/assets/index-test.js' "$ASR_WEB_DIR/index.html"
printf '%s\n' '{"guarded":true}' > "$STOCK_ALLSCAN_DIR/include/public-status.json"
stock_before=$(tree_digest "$STOCK_ALLSCAN_DIR")

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

rm "$ASR_WEB_DIR/astapi/asrEchoLink.php"
ASR_MASTER_DIR="$MASTER_DIR" \
ASR_WEB_ROOT="$WEB_ROOT" \
STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" \
ASR_WEB_DIR="$ASR_WEB_DIR" \
ASR_INSTALL_LOCK_HELD=1 \
ASR_INTEGRITY_WEB_ONLY=1 \
ASR_REAPPLY_COMMAND="$SCRIPT_DIR/asr-reapply.sh" \
  bash "$SCRIPT_DIR/asr-integrity-check.sh"
grep -q 'EchoLink compatibility sentinel' "$ASR_WEB_DIR/astapi/asrEchoLink.php"

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
