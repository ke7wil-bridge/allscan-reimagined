#!/bin/bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this hotfix as root." >&2
  exit 1
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="/var/backups/allscan-reimagined/johnny2-dmr-auth-wait-${stamp}"
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
old='''    for _ in range(40):
        if health.is_file() and time.time() - health.stat().st_mtime < 15:
'''
new='''    # TGIF can retain the previous managed connection briefly.  Keep
    # this deadline bounded, but allow its old session to expire before the
    # replacement login is classified as failed.
    for _ in range(180):
        if health.is_file() and time.time() - health.stat().st_mtime < 15:
'''
for raw in sys.argv[1:]:
    path=pathlib.Path(raw); original=path.read_text(encoding="utf-8")
    if original.count(old) != 1:
        raise SystemExit(f"Refusing to patch unexpected installed content: {path}")
    updated=original.replace(old,new)
    fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            handle.write(updated); handle.flush(); os.fchmod(handle.fileno(),0o755); os.fsync(handle.fileno())
        os.replace(tmp,path)
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)
    os.chown(path,0,0)
    py_compile.compile(str(path),doraise=True)
print("DMR TGIF authentication deadline hotfix validated.")
PY

cmp -s "${targets[0]}" "${targets[1]}"
grep -Fq 'for _ in range(180):' "${targets[0]}"
echo "Johnny2 DMR authentication deadline hotfix applied. Backup: $backup"
echo "No bridge configuration or service was changed."
