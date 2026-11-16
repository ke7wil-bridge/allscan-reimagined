#!/bin/bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this hotfix as root." >&2
  exit 1
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="/var/backups/allscan-reimagined/johnny2-dmr-admin-runtime-${stamp}"
install -d -o root -g root -m 700 "$backup"
targets=(
  /opt/allscan-reimagined/current/scripts/asr-bridge-setup-dmr-render.py
  /usr/local/libexec/allscan-reimagined/asr-bridge-setup-dmr-render.py
)

for target in "${targets[@]}"; do
  [ -f "$target" ] || { echo "Required installed file is missing: $target" >&2; exit 1; }
  cp -a -- "$target" "$backup/$(printf '%s' "$target" | tr / _ )"
done

python3 - "${targets[@]}" <<'PY'
import os, pathlib, py_compile, sys, tempfile
old = '''User=root
Group=root
ExecStartPre=/usr/bin/install -d -o root -g asr-bridge -m 0750 /run/asr-standalone-admin/{bridge_id}
ExecStart=/usr/bin/python3'''
new = '''User=root
Group=root
RuntimeDirectory=asr-standalone-admin/{bridge_id}
RuntimeDirectoryMode=0750
ExecStart=/usr/bin/python3'''
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
print("DMR admin RuntimeDirectory hotfix validated.")
PY

cmp -s "${targets[0]}" "${targets[1]}"
grep -Fq 'RuntimeDirectory=asr-standalone-admin/{bridge_id}' "${targets[0]}"
! grep -Fq 'ExecStartPre=/usr/bin/install' "${targets[0]}"

echo "Johnny2 DMR admin runtime hotfix applied. Backup: $backup"
echo "No bridge configuration or service was changed."
