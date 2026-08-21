#!/bin/bash
set -Eeuo pipefail

MASTER_DIR="${ASR_MASTER_DIR:-/opt/allscan-reimagined/current}"
CONFIG_DIR="/etc/allscan-reimagined"
DATA_DIR="/var/lib/allscan-reimagined"
ROLLBACK_MODE="${ASR_ROLLBACK_MODE:-0}"
WEB_ONLY="${ASR_REAPPLY_WEB_ONLY:-0}"
BRIDGE_LIFECYCLE_FAILED=0
STARTUP_BRIDGE_SUMMARY_FAILED=0
PROTECTED_CONFIG_HELPER="${ASR_PROTECTED_CONFIG_HELPER:-$MASTER_DIR/scripts/asr-protected-config-metadata.py}"
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

repair_protected_config_metadata() {
  [ "$WEB_ONLY" = "1" ] && return 0
  # The authenticated Settings page replaces these files atomically as the
  # web-server user. Restore the root ownership required by the privileged
  # bridge helpers before any validation or bridge processing is attempted.
  [ -f "$PROTECTED_CONFIG_HELPER" ] || {
    echo "Protected-config metadata helper is missing." >&2
    return 1
  }
  python3 "$PROTECTED_CONFIG_HELPER" \
    --web-group "$WEB_GROUP"
}

repair_protected_config_metadata

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

is_stock_status_artifact() {
  local relative="${1#./}"
  case "$relative" in
    */*) return 1 ;;
  esac
  case "$relative" in
    bridge-live.json|bridge-live.json.tmp|connected-clients.json|asr-connected-clients.json|\
    zello-status-data.json|zello-stream-debug.json|zello-talkers.json|dstar-clients.json|\
    public-status.json|.public-status-talkers.json|.public-status.*.json|\
    .connected-clients.*.json|.*-public-status-push.json|\
    .*-public-status-talker-live.json|.*-public-status-live-push.json|\
    .*-public-status-live-push.json.tmp)
      return 0
      ;;
  esac
  return 1
}

is_stock_digest_excluded() {
  is_stock_status_artifact "$1" && return 0
  case "$1" in
    ./astdb.txt|./favorites.ini|./favorites.ini.bak) return 0 ;;
  esac
  return 1
}

tree_digest() {
  local target="$1"
  (
    cd "$target"
    # Live status and user-data files can legitimately change while /asr is
    # being staged. They are not stock application code, so exclude only the
    # bounded root-level runtime contracts from the stock-code guard.
    while IFS= read -r -d '' file; do
      is_stock_digest_excluded "$file" && continue
      sha256sum -- "$file"
    done < <(find . -type f -print0 | LC_ALL=C sort -z)
    find . -type l -print0 | LC_ALL=C sort -z | \
      while IFS= read -r -d '' link; do printf '%s  %s\n' "$(readlink "$link")" "$link"; done
  ) | sha256sum | awk '{print $1}'
}

copy_stable_stock_tree() {
  local destination="$1" source relative
  while IFS= read -r -d '' source; do
    relative=".${source#"$STOCK_ALLSCAN_DIR"}"
    if is_stock_status_artifact "$relative"; then
      if [ -L "$source" ]; then
        echo "Refusing stock runtime path symlink: $relative" >&2
        return 1
      fi
      # Atomic writers can remove or replace these regular files between the
      # directory scan and this check. Never open or copy them into /asr.
      if [ -f "$source" ] || [ ! -e "$source" ]; then
        continue
      fi
      echo "Refusing non-regular stock runtime path: $relative" >&2
      return 1
    fi
    cp -a -- "$source" "$destination/"
  done < <(find "$STOCK_ALLSCAN_DIR" -mindepth 1 -maxdepth 1 -print0)
}

stage_asr_web() {
  local stage previous="" stock_before stock_after backend_version compat_dir relative
  stage=$(mktemp -d "$WEB_ROOT/.asr-reapply.XXXXXX")
  trap 'rm -rf -- "$stage"' RETURN
  stock_before=$(tree_digest "$STOCK_ALLSCAN_DIR")

  copy_stable_stock_tree "$stage" || return 1
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
[ -f "$MASTER_DIR/scripts/asr_bridge_status.py" ] && \
  install -o root -g root -m 644 "$MASTER_DIR/scripts/asr_bridge_status.py" /usr/local/sbin/asr_bridge_status.py
[ -f "$MASTER_DIR/scripts/asr_bridge_status.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr_bridge_status.py" /usr/local/sbin/allscan-reimagined-standard-bridge-status
install -d -o root -g root -m 755 /run/allscan-reimagined-bridge-control
[ -f "$MASTER_DIR/scripts/asr-ysf-bridge-control.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-ysf-bridge-control.py" /usr/local/sbin/allscan-reimagined-ysf-bridge-control
[ -f "$MASTER_DIR/scripts/asr-p25-bridge-control.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-p25-bridge-control.py" /usr/local/sbin/allscan-reimagined-p25-bridge-control
[ -f "$MASTER_DIR/scripts/asr-nxdn-bridge-control.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-nxdn-bridge-control.py" /usr/local/sbin/allscan-reimagined-nxdn-bridge-control
[ -f "$MASTER_DIR/scripts/asr-m17-bridge-control.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-m17-bridge-control.py" /usr/local/sbin/allscan-reimagined-m17-bridge-control
[ -f "$MASTER_DIR/scripts/asr-m17-usrp-connector.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-m17-usrp-connector.py" /usr/local/sbin/allscan-reimagined-m17-usrp-connector
[ -f "$MASTER_DIR/scripts/asr-fixed-bridge-recovery.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-fixed-bridge-recovery.py" /usr/local/sbin/allscan-reimagined-fixed-bridge-recovery
[ -f "$MASTER_DIR/scripts/asr-bridge-lifecycle.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-bridge-lifecycle.py" /usr/local/sbin/allscan-reimagined-bridge-lifecycle
[ -f "$MASTER_DIR/scripts/asr-startup-bridge-summary.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-startup-bridge-summary.py" /usr/local/sbin/allscan-reimagined-startup-bridge-summary
install -d -o root -g root -m 755 /run/allscan-reimagined-ysf-bridge-control
install -d -o root -g root -m 755 /run/allscan-reimagined-standard-bridge-status
install -d -o root -g root -m 755 /var/lib/allscan-reimagined/ysf-hosts
install -d -o root -g root -m 750 /var/log/allscan-reimagined
install -d -o root -g root -m 700 /var/lib/allscan-reimagined/bridge-ownership
install -d -o root -g root -m 700 /var/lib/allscan-reimagined/bridge-tombstones
install -d -o root -g root -m 700 /var/lib/allscan-reimagined/bridge-deletion-queue
install -d -o root -g root -m 700 /var/lib/allscan-reimagined/bridge-creation-intents
[ -f "$MASTER_DIR/scripts/asr-release-check.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-release-check.py" /usr/local/sbin/allscan-reimagined-release-check
[ -f "$MASTER_DIR/scripts/asr-rollback.py" ] && \
  install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-rollback.py" /usr/local/sbin/allscan-reimagined-rollback
mkdir -p "$CONFIG_DIR"
chown "root:$WEB_GROUP" "$CONFIG_DIR"
chmod 775 "$CONFIG_DIR"
repair_protected_config_metadata
MQTT_SECRETS_FILE="$CONFIG_DIR/bridge-mqtt-secrets.json"
if [ -e "$MQTT_SECRETS_FILE" ] || [ -L "$MQTT_SECRETS_FILE" ]; then
  [ -f "$MQTT_SECRETS_FILE" ] \
    && [ ! -L "$MQTT_SECRETS_FILE" ] \
    && [ "$(stat -c '%u:%g:%a:%h' "$MQTT_SECRETS_FILE")" = "0:0:600:1" ] || {
      echo "Root-only bridge MQTT credential file is unsafe." >&2
      exit 1
    }
fi
cat > /etc/tmpfiles.d/allscan-reimagined.conf <<EOF
d /run/allscan-reimagined 1775 root $WEB_GROUP -
d /run/allscan-reimagined/release-check 0750 root $WEB_GROUP -
d /run/allscan-reimagined/rollback-jobs 0700 root root -
d /run/allscan-reimagined-standard-bridge-status 0755 root root -
d /run/allscan-reimagined-ysf-bridge-control 0755 root root -
d /run/allscan-reimagined-p25-bridge-control 2750 root $WEB_GROUP -
d /run/allscan-reimagined-nxdn-bridge-control 2750 root $WEB_GROUP -
d /run/allscan-reimagined-m17 0755 root root -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/allscan-reimagined.conf
chmod 1775 /run/allscan-reimagined
install -d -o root -g "$WEB_GROUP" -m 750 /run/allscan-reimagined/release-check
install -d -o root -g root -m 700 /run/allscan-reimagined/rollback-jobs
if [ "$ROLLBACK_MODE" != "1" ] && [ "${ASR_INSTALL_LOCK_HELD:-0}" != "1" ] \
  && [ -x /usr/local/sbin/allscan-reimagined-bridge-lifecycle ]; then
  if ! /usr/local/sbin/allscan-reimagined-bridge-lifecycle reconcile; then
    BRIDGE_LIFECYCLE_FAILED=1
    echo "One or more deleted bridges still have registered resources. ASR preserved their ownership manifests and will retry cleanup on the next reapply." >&2
  fi
fi
cat > /etc/systemd/system/allscan-reimagined-standard-bridge-status.service <<'EOF'
[Unit]
Description=Reconcile live activity for configured Standard DMR and YSF bridges
After=network.target asterisk.service

[Service]
Type=simple
ExecStart=/usr/local/sbin/allscan-reimagined-standard-bridge-status --watch
Restart=on-failure
RestartSec=2s
Nice=10
MemoryMax=64M
TasksMax=16
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
LockPersonality=true
RestrictAddressFamilies=AF_UNIX
RestrictSUIDSGID=true
ReadWritePaths=/run/allscan-reimagined-standard-bridge-status

[Install]
WantedBy=multi-user.target
EOF
if [ -x /usr/local/sbin/allscan-reimagined-standard-bridge-status ] \
  && python3 - "$CONFIG_DIR/config.json" <<'PY'
import json
import re
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if any(
    isinstance(item, dict)
    and item.get("cardType", "standard") == "standard"
    and re.sub(r"[^a-z0-9]", "", str(item.get("mode", item.get("id", ""))).lower()).startswith(("dmr", "ysf"))
    for item in payload.get("bridges", [])
) else 1)
PY
then
  systemctl daemon-reload
  systemctl enable allscan-reimagined-standard-bridge-status.service >/dev/null
  systemctl restart allscan-reimagined-standard-bridge-status.service
else
  systemctl disable --now allscan-reimagined-standard-bridge-status.service >/dev/null 2>&1 || true
  rm -f /run/allscan-reimagined-standard-bridge-status/bridge-live.json
fi
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
cat > /etc/systemd/system/allscan-reimagined-ysf-net-live.service <<'EOF'
[Unit]
Description=Collect live activity for configured ASR YSF Net Bridges
After=network.target asterisk.service

[Service]
Type=simple
ExecStart=/usr/local/sbin/allscan-reimagined-ysf-bridge-control --watch
Restart=on-failure
RestartSec=2s
Nice=10
MemoryMax=64M
TasksMax=16
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/run/allscan-reimagined-ysf-bridge-control /var/lib/allscan-reimagined/ysf-hosts

[Install]
WantedBy=multi-user.target
EOF
systemctl disable --now allscan-reimagined-ysf-hosts-refresh.timer \
  allscan-reimagined-ysf-hosts-refresh.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/allscan-reimagined-ysf-hosts-refresh.timer \
  /etc/systemd/system/allscan-reimagined-ysf-hosts-refresh.service
systemctl daemon-reload
systemctl reset-failed allscan-reimagined-ysf-hosts-refresh.timer \
  allscan-reimagined-ysf-hosts-refresh.service >/dev/null 2>&1 || true
repair_protected_config_metadata
if [ -x /usr/local/sbin/allscan-reimagined-ysf-bridge-control ] \
  && python3 - "$CONFIG_DIR/config.json" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(
    0 if any(
        isinstance(item, dict) and item.get("cardType") == "ysf_net"
        for item in payload.get("bridges", [])
    ) else 1
)
PY
then
  systemctl daemon-reload
  if ! /usr/local/sbin/allscan-reimagined-ysf-bridge-control --sync-custom-hosts >/dev/null; then
    echo "A valid YSF reflector list is not installed yet; import YSFHosts.txt in Reimagined Settings." >&2
  fi
  systemctl enable allscan-reimagined-ysf-net-live.service >/dev/null
  systemctl restart allscan-reimagined-ysf-net-live.service
else
  systemctl daemon-reload
  systemctl disable --now allscan-reimagined-ysf-net-live.service >/dev/null 2>&1 || true
  rm -f /run/allscan-reimagined-ysf-bridge-control/ysf-live.json
fi
for digital_mode in p25 nxdn; do
  digital_label=$(printf '%s' "$digital_mode" | tr '[:lower:]' '[:upper:]')
  digital_helper="/usr/local/sbin/allscan-reimagined-${digital_mode}-bridge-control"
  digital_unit="allscan-reimagined-${digital_mode}-bridge-status.service"
  cat > "/etc/systemd/system/$digital_unit" <<EOF
[Unit]
Description=Cache configured ASR $digital_label bridge status
After=network.target

[Service]
Type=simple
ExecStart=$digital_helper watch --interval 2
Restart=on-failure
RestartSec=3s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/run/allscan-reimagined-${digital_mode}-bridge-control /var/log/allscan-reimagined

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  if [ -x "$digital_helper" ] && python3 - "$CONFIG_DIR/config.json" "$digital_mode" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
mode = sys.argv[2]
raise SystemExit(0 if any(
    isinstance(item, dict)
    and item.get("digitalMode") == mode
    and item.get("cardType") in {"standard", f"{mode}_net"}
    and item.get("bridgePermission") in {"self_owned", "approved"}
    for item in payload.get("bridges", [])
) else 1)
PY
  then
    systemctl enable --now "$digital_unit" >/dev/null
    systemctl restart "$digital_unit"
  else
    systemctl disable --now "$digital_unit" >/dev/null 2>&1 || true
    rm -f "/run/allscan-reimagined-${digital_mode}-bridge-control/status.json"
  fi
done
cat > /etc/systemd/system/allscan-reimagined-m17-bridge@.service <<'EOF'
[Unit]
Description=Run isolated ASR M17 bridge instance %i
After=network-online.target asterisk.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/sbin/allscan-reimagined-m17-usrp-connector --bridge %i --run
Restart=on-failure
RestartSec=3s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/run/allscan-reimagined-m17
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
for m17_unit_link in /etc/systemd/system/multi-user.target.wants/allscan-reimagined-m17-bridge@*.service; do
  [ -L "$m17_unit_link" ] || continue
  systemctl disable --now "${m17_unit_link##*/}" >/dev/null 2>&1 || true
done
if [ -x /usr/local/sbin/allscan-reimagined-m17-bridge-control ] \
  && [ -x /usr/local/sbin/allscan-reimagined-m17-usrp-connector ]; then
  while IFS= read -r bridge_id; do
    [ -n "$bridge_id" ] || continue
    if /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge "$bridge_id" validate >/dev/null \
      && /usr/local/sbin/allscan-reimagined-m17-usrp-connector --bridge "$bridge_id" --check >/dev/null; then
      systemctl enable --now "allscan-reimagined-m17-bridge@${bridge_id}.service" >/dev/null
    else
      echo "M17 bridge $bridge_id is not qualified; its connector remains stopped." >&2
    fi
  done < <(python3 - "$CONFIG_DIR/config.json" <<'PY'
import json
import re
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(0)
for bridge in payload.get("bridges", []):
    if not isinstance(bridge, dict):
        continue
    bridge_id = str(bridge.get("id", ""))
    if (
        bridge.get("mode") == "m17"
        and bridge.get("cardType") in {"standard", "m17_net"}
        and bridge.get("m17AudioQualified") is True
        and re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id)
    ):
        print(bridge_id)
PY
  )
fi
cat > /etc/systemd/system/allscan-reimagined-fixed-bridge-recovery.service <<EOF
[Unit]
Description=Restore opted-in fixed ASR bridge links
After=asterisk.service
ConditionPathExists=/etc/allscan-reimagined/config.json
ConditionPathExists=/etc/asterisk/rpt.conf

[Service]
Type=oneshot
User=asterisk
Group=asterisk
SupplementaryGroups=$WEB_GROUP
ExecStart=/usr/local/sbin/allscan-reimagined-fixed-bridge-recovery --once
Nice=10
TimeoutStartSec=30s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
EOF
cat > /etc/systemd/system/allscan-reimagined-fixed-bridge-recovery.timer <<'EOF'
[Unit]
Description=Check opted-in fixed ASR bridge links

[Timer]
OnBootSec=30s
OnUnitActiveSec=15s
AccuracySec=2s
Unit=allscan-reimagined-fixed-bridge-recovery.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
if [ -f "$MASTER_DIR/scripts/asr-fixed-bridge-recovery.py" ] \
  && [ -x /usr/local/sbin/allscan-reimagined-fixed-bridge-recovery ] \
  && /usr/local/sbin/allscan-reimagined-fixed-bridge-recovery --has-fallback-targets; then
  systemctl enable --now allscan-reimagined-fixed-bridge-recovery.timer >/dev/null
  if ! systemctl start allscan-reimagined-fixed-bridge-recovery.service; then
    echo "Fixed-bridge recovery is enabled, but its first check did not complete; the timer will retry." >&2
  fi
else
  systemctl disable --now allscan-reimagined-fixed-bridge-recovery.timer >/dev/null 2>&1 || true
  systemctl stop allscan-reimagined-fixed-bridge-recovery.service >/dev/null 2>&1 || true
fi
STARTUP_SUMMARY_SOUND_DIR=/usr/share/asterisk/sounds/en/custom/allscan-reimagined
STARTUP_SUMMARY_MARKER="$STARTUP_SUMMARY_SOUND_DIR/.asr-startup-summary-owner.json"
STARTUP_SUMMARY_SOUND_READY=1
if [ -L "$STARTUP_SUMMARY_SOUND_DIR" ] || { [ -e "$STARTUP_SUMMARY_SOUND_DIR" ] && [ ! -d "$STARTUP_SUMMARY_SOUND_DIR" ]; }; then
  echo "The startup bridge summary sound directory is unsafe; it was preserved." >&2
  STARTUP_SUMMARY_SOUND_READY=0
elif [ ! -e "$STARTUP_SUMMARY_SOUND_DIR" ]; then
  install -d -o asterisk -g asterisk -m 750 "$STARTUP_SUMMARY_SOUND_DIR"
  printf '%s\n' '{"createdBy":"allscan-reimagined","purpose":"startup-bridge-summary","schema":1}' > "$STARTUP_SUMMARY_MARKER"
  chown root:asterisk "$STARTUP_SUMMARY_MARKER"
  chmod 640 "$STARTUP_SUMMARY_MARKER"
elif [ "$(stat -c '%U:%G:%a' "$STARTUP_SUMMARY_SOUND_DIR" 2>/dev/null)" != "asterisk:asterisk:750" ] \
  || [ -L "$STARTUP_SUMMARY_MARKER" ] || [ ! -f "$STARTUP_SUMMARY_MARKER" ] \
  || [ "$(stat -c '%U:%G:%a:%h' "$STARTUP_SUMMARY_MARKER" 2>/dev/null)" != "root:asterisk:640:1" ] \
  || ! python3 - "$STARTUP_SUMMARY_MARKER" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
expected = {"schema": 1, "createdBy": "allscan-reimagined", "purpose": "startup-bridge-summary"}
raise SystemExit(0 if payload == expected else 1)
PY
then
  echo "The pre-existing startup bridge summary sound directory is not ASR-owned; it was preserved." >&2
  STARTUP_SUMMARY_SOUND_READY=0
fi
cat > /etc/systemd/system/allscan-reimagined-startup-bridge-summary.service <<'EOF'
[Unit]
Description=Announce established Standard ASR digital bridges once after startup
After=network-online.target asterisk.service asl-startup-announce.service allscan-reimagined-fixed-bridge-recovery.service
Wants=network-online.target allscan-reimagined-fixed-bridge-recovery.service
ConditionPathExists=/etc/allscan-reimagined/config.json

[Service]
Type=oneshot
User=asterisk
Group=asterisk
ExecStart=/usr/local/sbin/allscan-reimagined-startup-bridge-summary
TimeoutStartSec=150s
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/usr/share/asterisk/sounds/en/custom/allscan-reimagined

[Install]
WantedBy=multi-user.target
EOF
startup_bridge_summary_enabled=$(python3 - "$CONFIG_DIR/config.json" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(0)
print("1" if payload.get("announceStartupBridgeSummary") is True else "0")
PY
)
systemctl daemon-reload
if [ "$startup_bridge_summary_enabled" = "1" ] \
  && [ "$STARTUP_SUMMARY_SOUND_READY" = "1" ] \
  && [ -x /usr/local/sbin/allscan-reimagined-startup-bridge-summary ] \
  && [ -x /usr/bin/flite ] \
  && [ -x /usr/bin/sox ]; then
  # This is deliberately enabled without starting it. It runs once on the
  # next boot, after Asterisk and fixed-link recovery have settled.
  systemctl enable allscan-reimagined-startup-bridge-summary.service >/dev/null
else
  systemctl disable allscan-reimagined-startup-bridge-summary.service >/dev/null 2>&1 || true
  if [ "$startup_bridge_summary_enabled" = "1" ]; then
    STARTUP_BRIDGE_SUMMARY_FAILED=1
    echo "Startup bridge summary is enabled, but its helper, flite, or sox is unavailable." >&2
  fi
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
cat > /etc/systemd/system/allscan-reimagined-bridge-clients.service <<EOF
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
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadWritePaths=/run/allscan-reimagined $ASR_WEB_DIR
EOF
cat > /etc/systemd/system/allscan-reimagined-bridge-clients.timer <<'EOF'
[Unit]
Description=Refresh AllScan Reimagined bridge connected-client status

[Timer]
OnBootSec=20s
OnUnitInactiveSec=20s
AccuracySec=2s
Unit=allscan-reimagined-bridge-clients.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
bridge_client_source_count=$(php -r '
  $data = json_decode((string) @file_get_contents($argv[1]), true);
  $count = 0;
  foreach ((array) ($data["bridges"] ?? []) as $bridge) {
    $mode = strtolower((string) ($bridge["mode"] ?? $bridge["id"] ?? ""));
    $mode = preg_replace("/[^a-z0-9].*$/", "", $mode);
    $cardType = (string) ($bridge["cardType"] ?? "standard");
    $source = (string) ($bridge["clientSource"] ?? "auto");
    $url = trim((string) ($bridge["clientUrl"] ?? ""));
    $explicit = $cardType === "standard"
      && in_array($source, ["local_json", "http_api"], true)
      && $url !== "";
    $builtin = $cardType === "standard"
      && in_array($mode, ["p25", "nxdn", "m17"], true)
      && in_array($source, ["auto", "disabled"], true)
      && $url === "";
    $simpleAuto = $cardType === "standard"
      && in_array($mode, ["ysf", "zello"], true)
      && in_array($source, ["auto", "disabled"], true)
      && $url === "";
    if ($explicit || $builtin || $simpleAuto) $count++;
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
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-ysf-bridge-control --connect [a-zA-Z0-9_-]* [0-9][0-9][0-9][0-9][0-9] --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-ysf-bridge-control --disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-ysf-bridge-control --import-hosts [a-zA-Z0-9_-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-ysf-bridge-control --catalog-status [a-zA-Z0-9_-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-p25-bridge-control connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-p25-bridge-control disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-nxdn-bridge-control connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-nxdn-bridge-control disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* status
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]* connect --reflector M17-[A-Z0-9]* --module [A-Z]
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]* disconnect
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle preview-all
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle status
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle queue-deletion
$WEB_GROUP ALL=(root) NOPASSWD: /usr/bin/systemctl start allscan-reimagined-reapply.service
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

if [ "$BRIDGE_LIFECYCLE_FAILED" = "1" ]; then
  echo "AllScan Reimagined was reapplied, but deleted-bridge cleanup is incomplete. Review /run/allscan-reimagined/bridge-lifecycle.json." >&2
  exit 1
fi
if [ "$STARTUP_BRIDGE_SUMMARY_FAILED" = "1" ]; then
  echo "AllScan Reimagined was reapplied, but the optional startup bridge summary is not ready." >&2
  exit 1
fi
echo "AllScan Reimagined interface and security protections are active."
