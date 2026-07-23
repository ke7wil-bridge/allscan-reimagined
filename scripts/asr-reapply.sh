#!/bin/bash
set -Eeuo pipefail

MASTER_DIR="${ASR_MASTER_DIR:-/opt/allscan-reimagined/current}"
CONFIG_DIR="/etc/allscan-reimagined"
DATA_DIR="/var/lib/allscan-reimagined"
ROLLBACK_MODE="${ASR_ROLLBACK_MODE:-0}"
WEB_ONLY="${ASR_REAPPLY_WEB_ONLY:-0}"
if [ "${ASR_INSTALL_LOCK_HELD:-0}" != "1" ]; then
  LOCK_PATH="${ASR_LOCK_PATH:-/run/lock/allscan-reimagined-rollback.lock}"
  mkdir -p "$(dirname "$LOCK_PATH")"
  exec 9>"$LOCK_PATH"
  flock -n 9 || { echo "Another ASR installation, reapply, or rollback is running." >&2; exit 1; }
fi

if [ -n "${ASR_WEB_ROOT:-}" ]; then
  WEB_ROOT="$ASR_WEB_ROOT"
elif [ -d /var/www/html/allscan ]; then
  WEB_ROOT="/var/www/html"
elif [ -d /srv/http/allscan ]; then
  WEB_ROOT="/srv/http"
else
  echo "AllScan installation not found." >&2
  exit 1
fi
STOCK_ALLSCAN_DIR="${STOCK_ALLSCAN_DIR:-$WEB_ROOT/allscan}"
ASR_WEB_DIR="${ASR_WEB_DIR:-$WEB_ROOT/asr}"
[ -d "$STOCK_ALLSCAN_DIR" ] || { echo "Stock AllScan installation not found." >&2; exit 1; }

[ -d "$MASTER_DIR/web" ] || { echo "Reimagined master web files are missing." >&2; exit 1; }
[ -f "$MASTER_DIR/server/asr-api.php" ] || { echo "Reimagined API is missing." >&2; exit 1; }

WEB_GROUP="www-data"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="apache"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="http"
if ! getent group "$WEB_GROUP" >/dev/null 2>&1; then
  [ "$WEB_ONLY" = "1" ] || { echo "Web-server group not found." >&2; exit 1; }
  WEB_GROUP="$(id -gn)"
fi

safe_chown_files() {
  local owner="$1"
  shift
  [ "$#" -gt 0 ] || return 0
  for file in "$@"; do
    [ -e "$file" ] || continue
    chown "$owner" "$file" 2>/dev/null || true
  done
}

safe_chmod_files() {
  local mode="$1"
  shift
  [ "$#" -gt 0 ] || return 0
  for file in "$@"; do
    [ -e "$file" ] || continue
    chmod "$mode" "$file" 2>/dev/null || true
  done
}

tree_digest() {
  local target="$1"
  (
    cd "$target"
    # Live status and user-data files can legitimately change while /asr is
    # being staged. They are copied/preserved separately and are not stock
    # application code, so exclude them from the stock-code isolation guard.
    find . -type f \
      ! -path './bridge-live.json' \
      ! -path './connected-clients.json' \
      ! -path './asr-connected-clients.json' \
      ! -path './zello-status-data.json' \
      ! -path './zello-stream-debug.json' \
      ! -path './zello-talkers.json' \
      ! -path './astdb.txt' \
      ! -path './favorites.ini' \
      ! -path './favorites.ini.bak' \
      -print0 | LC_ALL=C sort -z | \
      xargs -0 -r sha256sum
    find . -type l -print0 | LC_ALL=C sort -z | \
      while IFS= read -r -d '' link; do printf '%s  %s\n' "$(readlink "$link")" "$link"; done
  ) | sha256sum | awk '{print $1}'
}

stage_asr_web() {
  local stage previous="" stock_before stock_after backend_version compat_dir relative
  stage=$(mktemp -d "$WEB_ROOT/.asr-reapply.XXXXXX")
  trap 'rm -rf -- "$stage"' RETURN
  stock_before=$(tree_digest "$STOCK_ALLSCAN_DIR")

  cp -a "$STOCK_ALLSCAN_DIR/." "$stage/"
  if [ -d "$ASR_WEB_DIR" ]; then
    for relative in \
      bridge-live.json connected-clients.json asr-connected-clients.json \
      zello-status-data.json favorites.ini; do
      [ -f "$ASR_WEB_DIR/$relative" ] || continue
      if [ "$relative" = "favorites.ini" ]; then
        # Stock and ASR normally share the canonical /etc/allscan file through
        # symlinks. Copying one symlink over the other dereferences both to the
        # same inode and fails with "same file" on a repeated reapply.
        if [ -L "$ASR_WEB_DIR/$relative" ] \
          || { [ -e "$stage/$relative" ] \
            && [ "$ASR_WEB_DIR/$relative" -ef "$stage/$relative" ]; }; then
          continue
        fi
        rm -f -- "$stage/$relative"
      fi
      cp -p "$ASR_WEB_DIR/$relative" "$stage/$relative"
    done
    for relative in img asr-user-content; do
      if [ -d "$ASR_WEB_DIR/$relative" ]; then
        rm -rf -- "$stage/$relative"
        cp -a "$ASR_WEB_DIR/$relative" "$stage/$relative"
      fi
    done
  fi

  cp -a "$MASTER_DIR/web/." "$stage/"
  install -m 644 "$MASTER_DIR/server/asr-api.php" "$stage/asr-api.php"
  backend_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' \
    "$STOCK_ALLSCAN_DIR/include/common.php" | head -1)
  compat_dir="$MASTER_DIR/compat/allscan-${backend_version:-unknown}"
  if [ -d "$compat_dir" ]; then
    echo "Applying verified Reimagined compatibility layer for AllScan $backend_version..."
    while IFS= read -r -d '' source; do
      relative=${source#"$compat_dir/"}
      install -d -m 755 "$(dirname "$stage/$relative")"
      install -m 644 "$source" "$stage/$relative"
    done < <(find "$compat_dir" -type f -print0)
  else
    echo "No exact ASR compatibility layer exists for AllScan ${backend_version:-unknown}; keeping the current /asr tree." >&2
    return 1
  fi
  if [ -d "$DATA_DIR" ]; then
    for logo in "$DATA_DIR"/header-logo.*; do
      [ -f "$logo" ] || continue
      install -m 644 "$logo" "$stage/asr-custom-logo.${logo##*.}"
    done
  fi

  stock_after=$(tree_digest "$STOCK_ALLSCAN_DIR")
  [ "$stock_before" = "$stock_after" ] || {
    echo "Stock AllScan changed while staging /asr; refusing to continue." >&2
    return 1
  }
  if [ -e "$ASR_WEB_DIR" ]; then
    previous="$WEB_ROOT/.asr-previous.$$"
    mv "$ASR_WEB_DIR" "$previous"
  fi
  if ! mv "$stage" "$ASR_WEB_DIR"; then
    [ -n "$previous" ] && mv "$previous" "$ASR_WEB_DIR"
    return 1
  fi
  stage=""
  [ -n "$previous" ] && rm -rf -- "$previous"
  trap - RETURN
}

echo "Staging AllScan Reimagined beside untouched stock AllScan..."
stage_asr_web
ALLSCAN_DIR="$ASR_WEB_DIR"
[ "$WEB_ONLY" = "1" ] && exit 0

install -o root -g root -m 755 "$MASTER_DIR/bin/allscan_wt_clients.sh" /usr/local/bin/allscan_wt_clients.sh
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-asterisk-read.sh" /usr/local/sbin/allscan-reimagined-asterisk-read
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-friendly-names.php" /usr/local/sbin/allscan-reimagined-friendly-names
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-bridge-clients.php" /usr/local/sbin/allscan-reimagined-bridge-clients
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-manager-perms.sh" /usr/local/sbin/allscan-reimagined-manager-perms
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-favorites-permissions.sh" /usr/local/sbin/allscan-reimagined-favorites-permissions
[ -f "$MASTER_DIR/scripts/asr-favorites-update.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-favorites-update.py" /usr/local/sbin/allscan-reimagined-favorites-update
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-patch-connected-clients.py" /usr/local/sbin/allscan-reimagined-patch-connected-clients
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-migrate-tgif-environment.py" /usr/local/sbin/allscan-reimagined-migrate-tgif-environment
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-patch-allscan-index.py" /usr/local/sbin/allscan-reimagined-patch-allscan-index
[ -f "$MASTER_DIR/scripts/asr-bridge-control.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-bridge-control.py" /usr/local/sbin/allscan-reimagined-bridge-control
install -d -o root -g root -m 755 /run/allscan-reimagined-bridge-control
[ -f "$MASTER_DIR/scripts/asr-release-check.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-release-check.py" /usr/local/sbin/allscan-reimagined-release-check
[ -f "$MASTER_DIR/scripts/asr-rollback.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-rollback.py" /usr/local/sbin/allscan-reimagined-rollback
mkdir -p "$CONFIG_DIR"
chown "root:$WEB_GROUP" "$CONFIG_DIR"
chmod 775 "$CONFIG_DIR"
[ -f "$CONFIG_DIR/config.json" ] && chown "root:$WEB_GROUP" "$CONFIG_DIR/config.json"
[ -f "$CONFIG_DIR/config.json" ] && chmod 664 "$CONFIG_DIR/config.json"
[ -f "$CONFIG_DIR/secrets.json" ] && chown "root:$WEB_GROUP" "$CONFIG_DIR/secrets.json"
[ -f "$CONFIG_DIR/secrets.json" ] && chmod 640 "$CONFIG_DIR/secrets.json"
cat > /etc/tmpfiles.d/allscan-reimagined.conf <<EOF
d /run/allscan-reimagined 1775 root $WEB_GROUP -
d /run/allscan-reimagined/release-check 0750 root $WEB_GROUP -
d /run/allscan-reimagined/rollback-jobs 0700 root root -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/allscan-reimagined.conf
chmod 1775 /run/allscan-reimagined
install -d -o root -g "$WEB_GROUP" -m 750 /run/allscan-reimagined/release-check
install -d -o root -g root -m 700 /run/allscan-reimagined/rollback-jobs
cat > /etc/systemd/system/allscan-reimagined-dmr-net-live.service <<'EOF'
[Unit]
Description=Collect live activity for configured ASR DMR Net Bridges
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/sbin/allscan-reimagined-bridge-control --watch-status
Restart=on-failure
RestartSec=2s
Nice=10
MemoryMax=64M
TasksMax=16
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/run/allscan-reimagined-bridge-control

[Install]
WantedBy=multi-user.target
EOF
if [ -x /usr/local/sbin/allscan-reimagined-bridge-control ] \
  && python3 - "$CONFIG_DIR/config.json" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(
    0 if any(
        isinstance(item, dict) and item.get("cardType") == "dmr_net"
        for item in payload.get("bridges", [])
    ) else 1
)
PY
then
  systemctl daemon-reload
  systemctl enable --now allscan-reimagined-dmr-net-live.service >/dev/null
else
  systemctl disable --now allscan-reimagined-dmr-net-live.service >/dev/null 2>&1 || true
  rm -f /run/allscan-reimagined-bridge-control/bridge-live.json
fi
cat > /etc/systemd/system/allscan-reimagined-release-check.service <<'EOF'
[Unit]
Description=Check for a newer AllScan Reimagined release
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
MemoryMax=64M
TasksMax=16
TimeoutStartSec=45s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/run/allscan-reimagined/release-check
ExecStart=/usr/local/sbin/allscan-reimagined-release-check
EOF
cat > /etc/systemd/system/allscan-reimagined-release-check.timer <<'EOF'
[Unit]
Description=Schedule the AllScan Reimagined release check

[Timer]
OnBootSec=2min
OnUnitActiveSec=1d
AccuracySec=5min
RandomizedDelaySec=10min
Unit=allscan-reimagined-release-check.service

[Install]
WantedBy=timers.target
EOF
if [ -x /usr/local/sbin/allscan-reimagined-release-check ] \
  && [ -f "$MASTER_DIR/scripts/asr-release-check.py" ]; then
  systemctl daemon-reload
  systemctl enable --now allscan-reimagined-release-check.timer >/dev/null
  systemctl is-enabled --quiet allscan-reimagined-release-check.timer
  systemctl is-active --quiet allscan-reimagined-release-check.timer
else
  systemctl disable --now allscan-reimagined-release-check.timer >/dev/null 2>&1 || true
  rm -f /usr/local/sbin/allscan-reimagined-release-check
  rm -f /etc/systemd/system/allscan-reimagined-release-check.service
  rm -f /etc/systemd/system/allscan-reimagined-release-check.timer
  systemctl daemon-reload
fi
cat > /etc/systemd/system/allscan-reimagined-rollback@.service <<'EOF'
[Unit]
Description=Run a queued AllScan Reimagined rollback
After=apache2.service

[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
MemoryMax=512M
TasksMax=64
TimeoutStartSec=infinity
ExecStart=/usr/local/sbin/allscan-reimagined-rollback run-job %i
EOF
systemctl daemon-reload
rm -f /var/cache/allscan-reimagined/astapi-*.json /var/cache/allscan-reimagined/astapi-*.lock 2>/dev/null || true
rmdir /var/cache/allscan-reimagined 2>/dev/null || true
cat > /etc/cron.d/allscan-reimagined-friendly-names <<'EOF'
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
@reboot root /usr/local/sbin/allscan-reimagined-friendly-names >/dev/null 2>&1
7,22,37,52 * * * * root nice -n 10 ionice -c 3 /usr/local/sbin/allscan-reimagined-friendly-names >/dev/null 2>&1
EOF
chmod 644 /etc/cron.d/allscan-reimagined-friendly-names
cat > /etc/cron.d/allscan-reimagined-manager-perms <<'EOF'
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
@reboot root /usr/local/sbin/allscan-reimagined-manager-perms >/dev/null 2>&1
23 4 * * * root nice -n 10 ionice -c 3 /usr/local/sbin/allscan-reimagined-manager-perms >/dev/null 2>&1
EOF
chmod 644 /etc/cron.d/allscan-reimagined-manager-perms
if [ "$ROLLBACK_MODE" != "1" ]; then
cat > /etc/systemd/system/allscan-reimagined-bridge-clients.service <<'EOF'
[Unit]
Description=Collect AllScan Reimagined bridge connected-client status
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
CPUQuota=25%
MemoryMax=128M
TimeoutStartSec=30s
ExecStart=/usr/local/sbin/allscan-reimagined-bridge-clients --once
EOF
cat > /etc/systemd/system/allscan-reimagined-bridge-clients.timer <<'EOF'
[Unit]
Description=Refresh AllScan Reimagined bridge connected-client status

[Timer]
OnBootSec=1min
OnUnitInactiveSec=1min
AccuracySec=5s
RandomizedDelaySec=10s
Unit=allscan-reimagined-bridge-clients.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
bridge_client_source_count=$(php -r '
  $data = json_decode((string) @file_get_contents($argv[1]), true);
  $count = 0;
  foreach ((array) ($data["bridges"] ?? []) as $bridge) {
    $source = (string) ($bridge["clientSource"] ?? "disabled");
    $url = trim((string) ($bridge["clientUrl"] ?? ""));
    if (in_array($source, ["local_json", "http_api"], true) && $url !== "") $count++;
  }
  echo $count;
' "$CONFIG_DIR/config.json" 2>/dev/null || printf '0')
if [ "$bridge_client_source_count" -gt 0 ]; then
  systemctl enable --now allscan-reimagined-bridge-clients.timer >/dev/null 2>&1 || true
else
  systemctl disable --now allscan-reimagined-bridge-clients.timer >/dev/null 2>&1 || true
  systemctl stop allscan-reimagined-bridge-clients.service >/dev/null 2>&1 || true
fi
if systemctl list-unit-files connected-clients-daemon.service --no-legend 2>/dev/null | grep -q '^connected-clients-daemon\.service'; then
  install -d -o root -g root -m 755 /etc/systemd/system/connected-clients-daemon.service.d
  tgif_environment_changed=0
  if /usr/local/sbin/allscan-reimagined-migrate-tgif-environment; then
    tgif_environment_changed=1
  else
    migration_status=$?
    [ "$migration_status" -eq 3 ] || exit "$migration_status"
  fi
  cat > /etc/systemd/system/connected-clients-daemon.service.d/asr-resource-guard.conf <<'EOF'
[Service]
MemoryHigh=128M
MemoryMax=192M
EOF
  cat > /etc/systemd/system/allscan-reimagined-connected-clients-maintenance.service <<'EOF'
[Unit]
Description=Perform scheduled maintenance restart of the companion connected-client collector
After=network-online.target connected-clients-daemon.service
ConditionPathExists=/usr/local/sbin/connected-clients-daemon.py

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl try-restart connected-clients-daemon.service
EOF
  cat > /etc/systemd/system/allscan-reimagined-connected-clients-maintenance.timer <<'EOF'
[Unit]
Description=Schedule the companion connected-client collector maintenance restart

[Timer]
OnCalendar=*-*-* 03:15:00
AccuracySec=1min
RandomizedDelaySec=15min
Persistent=true
Unit=allscan-reimagined-connected-clients-maintenance.service

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now allscan-reimagined-connected-clients-maintenance.timer >/dev/null 2>&1 || true
  connected_clients_changed=0
  if /usr/local/sbin/allscan-reimagined-patch-connected-clients; then
    connected_clients_changed=1
  fi
  if { [ "$connected_clients_changed" -eq 1 ] || [ "$tgif_environment_changed" -eq 1 ]; } \
    && [ "${ASR_ROLLBACK_MODE:-0}" != "1" ]; then
    systemctl try-restart connected-clients-daemon.service >/dev/null 2>&1 || true
  fi
else
  systemctl disable --now allscan-reimagined-connected-clients-maintenance.timer >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/allscan-reimagined-connected-clients-maintenance.service
  rm -f /etc/systemd/system/allscan-reimagined-connected-clients-maintenance.timer
  systemctl daemon-reload
fi
fi
if systemctl list-unit-files asl3-update-astdb.service --no-legend 2>/dev/null | grep -q '^asl3-update-astdb\.service'; then
  install -d -o root -g root -m 755 /etc/systemd/system/asl3-update-astdb.service.d
  cat > /etc/systemd/system/asl3-update-astdb.service.d/allscan-reimagined-friendly-names.conf <<'EOF'
[Service]
ExecStartPost=/usr/local/sbin/allscan-reimagined-friendly-names
EOF
  systemctl daemon-reload
fi

cat > /etc/sudoers.d/allscan-reimagined <<EOF
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/bin/allscan_wt_clients.sh
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asterisk-read
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-friendly-names
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-clients
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-favorites-update add --file /etc/allscan/favorites*.ini --node * --label *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-favorites-update delete --file /etc/allscan/favorites*.ini --node *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-control --connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-control --disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-rollback --list-json
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-rollback --queue-rollback [0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-rollback --status-json [0-9]*
EOF
chmod 440 /etc/sudoers.d/allscan-reimagined
visudo -cf /etc/sudoers.d/allscan-reimagined >/dev/null

find "$ALLSCAN_DIR" ! -name '*.tmp' -exec sh -c 'for file; do [ -e "$file" ] && chown root:root "$file" 2>/dev/null || true; done' sh {} +
find "$ALLSCAN_DIR" -type d -exec chmod 755 {} + || true
find "$ALLSCAN_DIR" -type f ! -name '*.tmp' -exec sh -c 'for file; do [ -e "$file" ] && chmod 644 "$file" 2>/dev/null || true; done' sh {} +
install -d -o root -g "$WEB_GROUP" -m 775 "$ALLSCAN_DIR/asr-user-content"
find "$ALLSCAN_DIR/asr-user-content" -type f ! -name '*.tmp' -exec sh -c 'owner="$1"; shift; for file; do [ -e "$file" ] && chown "$owner" "$file" 2>/dev/null || true; done' sh "root:$WEB_GROUP" {} +
find "$ALLSCAN_DIR/asr-user-content" -type f ! -name '*.tmp' -exec sh -c 'for file; do [ -e "$file" ] && chmod 664 "$file" 2>/dev/null || true; done' sh {} +

[ -s "$ALLSCAN_DIR/bridge-live.json" ] || printf '%s\n' '{"updated":""}' > "$ALLSCAN_DIR/bridge-live.json"
[ -s "$ALLSCAN_DIR/connected-clients.json" ] || printf '%s\n' '{}' > "$ALLSCAN_DIR/connected-clients.json"
[ -s "$ALLSCAN_DIR/asr-connected-clients.json" ] || printf '%s\n' '{}' > "$ALLSCAN_DIR/asr-connected-clients.json"

for runtime_file in "$ALLSCAN_DIR"/favorites*.ini \
  "$ALLSCAN_DIR/bridge-live.json" \
  "$ALLSCAN_DIR/connected-clients.json" \
  "$ALLSCAN_DIR/asr-connected-clients.json" \
  "$ALLSCAN_DIR/zello-status-data.json"; do
  [ -f "$runtime_file" ] || continue
  safe_chown_files "root:$WEB_GROUP" "$runtime_file"
  safe_chmod_files 664 "$runtime_file"
done
ASR_ALLSCAN_DIR="$ASR_WEB_DIR" ASR_WEB_GROUP="$WEB_GROUP" \
  /usr/local/sbin/allscan-reimagined-favorites-permissions --apply

[ -f "$ALLSCAN_DIR/AllScanInstallUpdate.php" ] && chmod 755 "$ALLSCAN_DIR/AllScanInstallUpdate.php"
[ -f "$ALLSCAN_DIR/docs/extensions.conf" ] && chmod 600 "$ALLSCAN_DIR/docs/extensions.conf"
[ -f "$ALLSCAN_DIR/docs/rpt.conf" ] && chmod 600 "$ALLSCAN_DIR/docs/rpt.conf"
if [ -f /etc/allscan/allscan.db ]; then
  chown "$WEB_GROUP:$WEB_GROUP" /etc/allscan/allscan.db
  chmod 660 /etc/allscan/allscan.db
fi
/usr/local/sbin/allscan-reimagined-friendly-names --once >/dev/null 2>&1 || true
[ -f /etc/allscan/asdb.txt ] && chown "root:$WEB_GROUP" /etc/allscan/asdb.txt
[ -f /etc/allscan/asdb.txt ] && chmod 664 /etc/allscan/asdb.txt
/usr/local/sbin/allscan-reimagined-manager-perms >/dev/null 2>&1 || true
if [ "$ROLLBACK_MODE" != "1" ]; then
  if [ "$bridge_client_source_count" -gt 0 ]; then
    /usr/local/sbin/allscan-reimagined-bridge-clients --once >/dev/null 2>&1 || true
  else
    printf '%s\n' '{}' > "$ALLSCAN_DIR/asr-connected-clients.json"
  fi
fi
[ -f "$ALLSCAN_DIR/connected-clients.json" ] && chown "root:$WEB_GROUP" "$ALLSCAN_DIR/connected-clients.json"
[ -f "$ALLSCAN_DIR/connected-clients.json" ] && chmod 664 "$ALLSCAN_DIR/connected-clients.json"
[ -f "$ALLSCAN_DIR/asr-connected-clients.json" ] && chown "root:$WEB_GROUP" "$ALLSCAN_DIR/asr-connected-clients.json"
[ -f "$ALLSCAN_DIR/asr-connected-clients.json" ] && chmod 664 "$ALLSCAN_DIR/asr-connected-clients.json"

if command -v apache2ctl >/dev/null 2>&1; then
  cat > /etc/apache2/conf-available/allscan-reimagined.conf <<EOF
<Directory "$ALLSCAN_DIR">
    Options -Indexes
</Directory>

<Directory "$ALLSCAN_DIR/include">
    Require all denied
</Directory>

<Directory "$ALLSCAN_DIR/astapi">
    Require all denied
    <FilesMatch "^(server|cmd|connect)\\.php$">
        Require all granted
    </FilesMatch>
</Directory>

<Directory "$ALLSCAN_DIR/_tools">
    Require all denied
</Directory>

<Directory "$ALLSCAN_DIR/asr-user-content">
    Options -Indexes
    <FilesMatch "\\.php$">
        Require all denied
    </FilesMatch>
</Directory>

<Directory "$ALLSCAN_DIR">
    <FilesMatch "(^\\.|\\.(bak|old|orig|save|sql|sqlite|db|key|pem|log|zip|tar|gz)$)">
        Require all denied
    </FilesMatch>
</Directory>

<IfModule mod_headers.c>
    <Location "/asr">
        Header always set X-Content-Type-Options "nosniff"
        Header always set Referrer-Policy "same-origin"
        Header always set X-Frame-Options "SAMEORIGIN"
        Header always set Permissions-Policy "camera=(), microphone=(), geolocation=()"
        Header always set X-Robots-Tag "noindex, nofollow"
    </Location>
</IfModule>

<IfModule mod_deflate.c>
    AddOutputFilterByType DEFLATE text/html text/plain text/css application/javascript application/json image/svg+xml
</IfModule>

<IfModule mod_headers.c>
    <FilesMatch "^index-[A-Za-z0-9_-]+\.(css|js)$">
        Header set Cache-Control "public, max-age=31536000, immutable"
    </FilesMatch>
    <FilesMatch "\.(png|gif|svg|webp)$">
        Header set Cache-Control "public, max-age=604800"
    </FilesMatch>
</IfModule>

<IfModule mod_php.c>
    php_admin_value session.cookie_httponly 1
    php_admin_value session.cookie_samesite Strict
    php_admin_value session.use_strict_mode 1
</IfModule>
EOF
  a2enmod headers deflate >/dev/null
  a2enconf allscan-reimagined >/dev/null
  apache2ctl configtest
  systemctl reload apache2
fi

echo "AllScan Reimagined interface and security protections are active."
