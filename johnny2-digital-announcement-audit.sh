#!/usr/bin/env bash
set -Eeuo pipefail

# Audit and suppress locally generated digital-bridge announcements on Johnny2.
# Run on Johnny2 as root.  The default action is read-only; --apply makes the
# reversible local-announcement changes, and --quarantine MODE stops one mode
# after the audit when network-originated audio must be isolated.

action=audit
quarantine_mode=
case "${1:-}" in
  "") ;;
  --audit) action=audit ;;
  --apply) action=apply ;;
  --quarantine)
    action=quarantine
    quarantine_mode="${2:-}"
    ;;
  *)
    echo "Usage: sudo $0 [--audit|--apply|--quarantine dmr|ysf|p25|nxdn|m17]" >&2
    exit 2
    ;;
esac

if [[ $action == quarantine && ! $quarantine_mode =~ ^(dmr|ysf|p25|nxdn|m17)$ ]]; then
  echo "Choose one quarantine mode: dmr, ysf, p25, nxdn, or m17." >&2
  exit 2
fi
if [[ $action != audit && $(id -u) -ne 0 ]]; then
  echo "Run --apply and --quarantine as root." >&2
  exit 1
fi

config=/etc/allscan-reimagined/config.json
rpt=/etc/asterisk/rpt.conf
installations=/var/lib/allscan-reimagined/bridge-setup/installations
stamp=$(date -u +%Y%m%dT%H%M%SZ)
report_dir=/var/log/allscan-reimagined
if [[ ! -d $report_dir || ! -w $report_dir ]]; then
  report_dir=/tmp
fi
report="$report_dir/johnny2-digital-announcement-audit-$stamp.txt"

for required in "$config" "$rpt"; do
  [[ -f $required && ! -L $required ]] || {
    echo "Required regular file is missing or unsafe: $required" >&2
    exit 1
  }
done

hostname_now=$(hostname)
main_node=$(python3 - "$config" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text(encoding="utf-8"))
print(data.get("node", ""))
PY
)
if [[ $main_node != 623123 ]]; then
  echo "Refusing to operate: expected Johnny2 node 623123, found ${main_node:-none}." >&2
  exit 1
fi

exec > >(tee -a "$report") 2>&1
echo "Johnny2 digital announcement audit"
echo "UTC: $stamp"
echo "Host: $hostname_now"
echo "Action: $action${quarantine_mode:+ $quarantine_mode}"
echo

echo "Configured bridge map:"
python3 - "$config" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
wanted = {"dmr", "ysf", "p25", "nxdn", "m17"}
for item in data.get("bridges", []):
    if isinstance(item, dict) and str(item.get("mode", "")).lower() in wanted:
        print(f"  {item.get('mode', '').upper():4} node={item.get('node', '-')} id={item.get('id', '-')}")
print("  startup summary:", bool(data.get("announceStartupBridgeSummary")))
print("  announce-none:", bool(data.get("announceNoConnectedBridges")))
PY
echo

echo "Bridge service state:"
systemctl list-unit-files --no-legend 2>/dev/null \
  | awk 'tolower($1) ~ /(dmr|ysf|p25|nxdn|m17)/ && $1 ~ /\.service$/ {print $1}' \
  | sort -u \
  | while read -r unit; do
      active=$(systemctl is-active "$unit" 2>/dev/null || true)
      enabled=$(systemctl is-enabled "$unit" 2>/dev/null || true)
      printf '  %-64s active=%-10s enabled=%s\n' "$unit" "$active" "$enabled"
    done
echo

echo "Announcement/beacon-like configuration (all modes):"
config_roots=(/opt/allscan-reimagined-bridges /opt /etc/asterisk)
find "${config_roots[@]}" -xdev -type f \
  \( -name '*.ini' -o -name '*.conf' -o -name '*.json' \) 2>/dev/null \
  | while read -r path; do
      case "$path" in
        *dmr*|*DMR*|*ysf*|*YSF*|*p25*|*P25*|*nxdn*|*NXDN*|*m17*|*M17*|*/asterisk/*)
          grep -EinH '(^|[^a-z])(announce|announcement|beacon|voice[_ -]?id|idrecording|telemdynamic|telemdefault|holdofftelem)([^a-z]|$)' "$path" 2>/dev/null || true
          ;;
      esac
    done
echo

echo "Recent mode activity (last 48 hours; newest 300 matching records):"
journalctl --since '48 hours ago' --no-pager 2>/dev/null \
  | grep -Ei 'dmr|ysf|p25|nxdn|m17|rpt localplay|playback|announce|beacon' \
  | tail -n 300 || true
echo

if [[ $action == audit ]]; then
  echo "Read-only audit complete."
  echo "Report: $report"
  exit 0
fi

backup=/var/backups/allscan-reimagined/johnny2-announcement-suppression-$stamp
install -d -o root -g root -m 700 "$backup"
install -m 600 "$config" "$backup/config.json"
install -m 600 "$rpt" "$backup/rpt.conf"
systemctl list-unit-files --no-legend > "$backup/systemd-unit-files.txt"

python3 - "$config" <<'PY'
import json, os, pathlib, tempfile, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["announceStartupBridgeSummary"] = False
data["announceNoConnectedBridges"] = False
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fchmod(handle.fileno(), path.stat().st_mode & 0o777)
        os.fsync(handle.fileno())
    os.replace(temporary, path)
finally:
    pathlib.Path(temporary).unlink(missing_ok=True)
PY
systemctl disable --now allscan-reimagined-startup-bridge-summary.service 2>/dev/null || true

# Every private digital transport node must suppress app_rpt telemetry.  The
# installer normally writes these values; this repairs older or hand-edited
# stanzas without touching the main repeater node.
python3 - "$config" "$rpt" <<'PY'
import json, os, pathlib, re, tempfile, sys
config_path, rpt_path = map(pathlib.Path, sys.argv[1:])
data = json.loads(config_path.read_text(encoding="utf-8"))
nodes = {
    str(item.get("node"))
    for item in data.get("bridges", [])
    if isinstance(item, dict)
    and str(item.get("mode", "")).lower() in {"dmr", "ysf", "p25", "nxdn", "m17"}
    and re.fullmatch(r"[0-9]{3,10}", str(item.get("node", "")))
}
text = rpt_path.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
sections = []
for index, line in enumerate(lines):
    match = re.match(r"^\s*\[([0-9]{3,10})\]", line)
    if match and match.group(1) in nodes:
        sections.append((index, match.group(1)))
for start, node in reversed(sections):
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[[^]]+\]", lines[i])), len(lines))
    block = lines[start:end]
    for key, value in (("holdofftelem", "1"), ("telemdefault", "0"), ("telemdynamic", "0")):
        matches = [i for i, row in enumerate(block) if re.match(rf"^\s*{key}\s*=", row, re.I)]
        if matches:
            block[matches[0]] = f"{key} = {value}\n"
            for duplicate in reversed(matches[1:]):
                del block[duplicate]
        else:
            block.append(f"{key} = {value}\n")
    lines[start:end] = block
updated = "".join(lines)
if updated != text:
    fd, temporary = tempfile.mkstemp(prefix=f".{rpt_path.name}.", dir=rpt_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fchmod(handle.fileno(), rpt_path.stat().st_mode & 0o777)
            os.fsync(handle.fileno())
        os.replace(temporary, rpt_path)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
print("Silent telemetry enforced for nodes: " + ", ".join(sorted(nodes)))
PY
asterisk -rx 'rpt reload' >/dev/null

if [[ $action == quarantine ]]; then
  echo "Quarantining $quarantine_mode bridge services:"
  mapfile -t quarantine_units < <(
    if [[ -d $installations ]]; then
      python3 - "$installations" "$quarantine_mode" <<'PY'
import json, pathlib, sys
root, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
for record in root.glob(f"{mode}_*.json"):
    try:
        payload = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    for service in payload.get("services", []):
        if isinstance(service, str) and service.endswith(".service"):
            print(service)
PY
    fi
  )
  if (( ${#quarantine_units[@]} == 0 )); then
    echo "No installer-owned $quarantine_mode services were found; no service was stopped." >&2
    exit 1
  fi
  printf '  %s\n' "${quarantine_units[@]}"
  systemctl disable --now "${quarantine_units[@]}"
fi

python3 -m json.tool "$config" >/dev/null
for node in 1001 1003 1004 1005 1006; do
  asterisk -rx "rpt stats $node" >/dev/null 2>&1 || true
done
echo "Local digital announcements suppressed successfully."
echo "Backup: $backup"
echo "Report: $report"
