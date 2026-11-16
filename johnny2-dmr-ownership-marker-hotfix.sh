#!/bin/bash
set -Eeuo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run this hotfix as root." >&2; exit 1; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="/var/backups/allscan-reimagined/johnny2-dmr-ownership-marker-${stamp}"
install -d -o root -g root -m 700 "$backup"
targets=(
  /opt/allscan-reimagined/current/scripts/asr-bridge-setup-dmr-install.py
  /usr/local/libexec/allscan-reimagined/asr-bridge-setup-dmr-install.py
)
for target in "${targets[@]}"; do
  [ -f "$target" ] || { echo "Required installed file is missing: $target" >&2; exit 1; }
  cp -a -- "$target" "$backup/$(printf '%s' "$target" | tr / _ )"
done
python3 - "${targets[@]}" <<'PY'
import os, pathlib, py_compile, sys, tempfile
old='''    rpt = rpt.replace(f"; ASR MANAGED BEGIN {identity}", f"; BEGIN ASR BRIDGE {identity} {creation_id}")
    rpt = rpt.replace(f"; ASR MANAGED END {identity}", f"; END ASR BRIDGE {identity} {creation_id}")
'''
new='''    rpt = rpt.replace(f"{m17.MANAGED_PREFIX}BEGIN {identity}", f"; BEGIN ASR BRIDGE {identity} {creation_id}")
    rpt = rpt.replace(f"{m17.MANAGED_PREFIX}END {identity}", f"; END ASR BRIDGE {identity} {creation_id}")
'''
for raw in sys.argv[1:]:
    path=pathlib.Path(raw); original=path.read_text(encoding="utf-8")
    if original.count(old) != 1: raise SystemExit(f"Refusing to patch unexpected installed content: {path}")
    updated=original.replace(old,new)
    fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            handle.write(updated); handle.flush(); os.fchmod(handle.fileno(),0o755); os.fsync(handle.fileno())
        os.replace(tmp,path)
    finally: pathlib.Path(tmp).unlink(missing_ok=True)
    os.chown(path,0,0); py_compile.compile(str(path),doraise=True)
print("DMR Asterisk ownership-marker hotfix validated.")
PY
cmp -s "${targets[0]}" "${targets[1]}"
grep -Fq 'm17.MANAGED_PREFIX}BEGIN' "${targets[0]}"
echo "Johnny2 DMR ownership-marker hotfix applied. Backup: $backup"
echo "No bridge configuration or service was changed."
