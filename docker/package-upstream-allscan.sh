#!/bin/sh
set -eu

MANIFEST=${1:-/opt/asr-source/stock-allscan.json}
DESTINATION=${2:-/opt/upstream-allscan}
ORIGIN=https://github.com/davidgsd/AllScan.git

test -f "$MANIFEST" || {
  echo "Packaged AllScan manifest is missing: $MANIFEST" >&2
  exit 1
}

manifest_value() {
  python3 -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]]; print(value)' \
    "$MANIFEST" "$1"
}

commit=$(manifest_value commit)
validator=$(dirname "$MANIFEST")/scripts/asr-stock-allscan.py
test -f "$validator" || {
  echo "Packaged AllScan manifest validator is missing." >&2
  exit 1
}
version=$(python3 "$validator" manifest-version "$MANIFEST")

case "$commit" in
  *[!0-9a-f]*|'') echo "Packaged AllScan commit is invalid." >&2; exit 1 ;;
esac
[ "${#commit}" -eq 40 ] || { echo "Packaged AllScan commit is invalid." >&2; exit 1; }
case "$version" in
  v[0-9]*.[0-9]*) ;;
  *) echo "Packaged AllScan version is invalid." >&2; exit 1 ;;
esac
[ ! -e "$DESTINATION" ] || {
  echo "Packaged AllScan destination already exists: $DESTINATION" >&2
  exit 1
}
parent=$(dirname "$DESTINATION")
mkdir -p "$parent"
stage=$(mktemp -d "$parent/.upstream-allscan.XXXXXX")
trap 'rm -rf "$stage"' EXIT HUP INT TERM

git init -q "$stage"
git -C "$stage" remote add origin "$ORIGIN"
git -C "$stage" fetch --depth 1 origin "$commit"
git -C "$stage" checkout --detach --force FETCH_HEAD
[ "$(git -C "$stage" rev-parse HEAD)" = "$commit" ] || {
  echo "Packaged AllScan checkout does not match its manifest." >&2
  exit 1
}
git -C "$stage" fsck --no-dangling >/dev/null

actual_version=$(python3 -c '
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
match = re.search(r"^\$AllScanVersion\s*=\s*\"([^\"]+)\"", text, re.MULTILINE)
if not match:
    raise SystemExit(1)
print(match.group(1))
' "$stage/include/common.php") || {
  echo "Packaged AllScan source has no version metadata." >&2
  exit 1
}
[ "$actual_version" = "$version" ] || {
  echo "Packaged AllScan source version does not match its manifest." >&2
  exit 1
}

mv "$stage" "$DESTINATION"
trap - EXIT HUP INT TERM
echo "Packaged AllScan $version at $commit"
