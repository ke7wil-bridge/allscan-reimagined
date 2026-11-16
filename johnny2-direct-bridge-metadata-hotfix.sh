#!/bin/bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this hotfix as root." >&2
  exit 1
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="/var/backups/allscan-reimagined/johnny2-direct-metadata-${stamp}"
install -d -o root -g root -m 700 "$backup"

targets=(
  /opt/allscan-reimagined/current/scripts/asr-bridge-setup-digital-install.py
  /usr/local/libexec/allscan-reimagined/asr-bridge-setup-digital-install.py
)

for target in "${targets[@]}"; do
  [ -f "$target" ] || { echo "Required installed file is missing: $target" >&2; exit 1; }
  cp -a -- "$target" "$backup/$(printf '%s' "$target" | tr / _ )"
done

python3 - "$backup" "${targets[@]}" <<'PY'
import os
import pathlib
import py_compile
import sys
import tempfile

old_one = '''        "instance": instance, "bridgePermission": "self_owned",
        "backendMode": "managed", "clientSource": mode,
        "fixedDestination": str(plan["settings"]["destination"]),
'''
new_one = '''        "instance": instance, "bridgePermission": "self_owned",
        "backendMode": "managed",
        # Direct YSF/P25/NXDN runtimes are outbound reflector clients.  They
        # do not host inbound sessions and must not advertise local Kick/Ban.
        "clientSource": "disabled" if role == "standard" else "auto",
        "architecture": "direct-dvswitch",
        "bridgeType": f"direct-{mode}",
        "fixedDestination": str(plan["settings"]["destination"]),
'''
old_two = '''    if role == "standard":
        entry.update({"urfReflector": True, "urfGroupId": "urf", "urfName": "URFWIL"})
'''
new_two = '''    if role == "standard":
        entry.update({"fixedBridgeRecovery": True, "liveAudioVerified": False})
'''

for raw in sys.argv[2:]:
    path = pathlib.Path(raw)
    original = path.read_text(encoding="utf-8")
    if original.count(old_one) != 1 or original.count(old_two) != 1:
        raise SystemExit(f"Refusing to patch unexpected installed content: {path}")
    updated = original.replace(old_one, new_one).replace(old_two, new_two)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fchmod(handle.fileno(), 0o755)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
    os.chown(path, 0, 0)
    py_compile.compile(str(path), doraise=True)

print("Direct bridge metadata hotfix validated.")
PY

grep -Fq '"architecture": "direct-dvswitch"' "${targets[0]}"
grep -Fq '"clientSource": "disabled" if role == "standard" else "auto"' "${targets[0]}"
grep -Fq 'entry.update({"fixedBridgeRecovery": True, "liveAudioVerified": False})' "${targets[0]}"

echo "Johnny2 direct-bridge metadata hotfix applied. Backup: $backup"
echo "No bridge configuration or service was changed."
