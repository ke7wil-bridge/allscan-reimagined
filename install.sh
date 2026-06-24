#!/bin/bash
set -Eeuo pipefail

ASR_VERSION="1.0.0-beta.2"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PAYLOAD_DIR="$SCRIPT_DIR/payload"
RELEASE_DIR="/opt/allscan-reimagined/releases/$ASR_VERSION"
BACKUP_DIR="/root/allscan-reimagined-backups/$(date +%Y%m%d-%H%M%S)"
OFFICIAL_INSTALLER_URL="https://raw.githubusercontent.com/davidgsd/AllScan/main/AllScanInstallUpdate.php"
CHANGES_STARTED=0

fail() { printf 'ERROR: %s\n' "$*" >&2; return 1; }
restore_runtime_backup() {
  [ -d "$BACKUP_DIR/runtime" ] || return 0
  mkdir -p "$ALLSCAN_DIR"
  for runtime_file in bridge-live.json connected-clients.json zello-status-data.json; do
    [ -f "$BACKUP_DIR/runtime/$runtime_file" ] && cp -p "$BACKUP_DIR/runtime/$runtime_file" "$ALLSCAN_DIR/$runtime_file"
  done
  [ -d "$BACKUP_DIR/runtime/img" ] && cp -a "$BACKUP_DIR/runtime/img" "$ALLSCAN_DIR/img"
}

rollback_on_error() {
  status=$?
  set +e
  if [ "$CHANGES_STARTED" -eq 1 ] && [ -f "$BACKUP_DIR/allscan-webroot.tar.gz" ]; then
    echo
    echo "Installation failed. Restoring the previous AllScan installation..." >&2
    [ -d "$ALLSCAN_DIR" ] && mv "$ALLSCAN_DIR" "$BACKUP_DIR/failed-allscan"
    tar -xzf "$BACKUP_DIR/allscan-webroot.tar.gz" -C "$WEB_ROOT"
    restore_runtime_backup
    if [ -f "$BACKUP_DIR/allscan.db" ]; then
      install -o "$WEB_GROUP" -g "$WEB_GROUP" -m 660 "$BACKUP_DIR/allscan.db" /etc/allscan/allscan.db
    fi
    systemctl reload apache2 2>/dev/null || true
    echo "Previous AllScan installation restored." >&2
  fi
  exit "$status"
}
trap rollback_on_error ERR
ask() {
  local prompt="$1" default="${2:-y}" answer
  [ -t 0 ] || { [ "$default" = "y" ]; return; }
  read -r -p "$prompt " answer
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy]$ ]]
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Run this installer with sudo."
[ -d "$PAYLOAD_DIR/web" ] || fail "Installer payload is incomplete."

for command in curl php tar install find systemctl; do
  command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

if [ -d /var/www/html ]; then
  WEB_ROOT="/var/www/html"
elif [ -d /srv/http ]; then
  WEB_ROOT="/srv/http"
else
  fail "A supported web root was not found."
fi
ALLSCAN_DIR="$WEB_ROOT/allscan"
WEB_GROUP="www-data"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="apache"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="http"
getent group "$WEB_GROUP" >/dev/null 2>&1 || fail "A supported web-server group was not found."

current_version="not installed"
if [ -r "$ALLSCAN_DIR/include/common.php" ]; then
  current_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$ALLSCAN_DIR/include/common.php" | head -1)
  current_version="${current_version:-unknown}"
fi
latest_version=$(curl -fsSL https://raw.githubusercontent.com/davidgsd/AllScan/main/include/common.php \
  | sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' | head -1)
latest_version="${latest_version:-unknown}"
[ "$latest_version" != "unknown" ] || fail "The latest official AllScan version could not be verified."

echo
echo "============================================================"
echo " AllScan Reimagined Installer"
echo "============================================================"
echo "Existing AllScan backend: $current_version"
echo "Latest official backend:  $latest_version"
echo "Reimagined release:        v1.0.0 Beta 2"
echo
echo "Existing AllScan users, passwords, permissions, Favorites,"
echo "database, and node settings will be preserved."
echo "A complete rollback backup will be created before changes."
echo

mkdir -p "$BACKUP_DIR"
if [ -d "$ALLSCAN_DIR" ]; then
  echo "[1/8] Backing up the existing AllScan installation..."
  mkdir -p "$BACKUP_DIR/runtime"
  for runtime_file in bridge-live.json connected-clients.json zello-status-data.json; do
    [ -f "$ALLSCAN_DIR/$runtime_file" ] && cp -p "$ALLSCAN_DIR/$runtime_file" "$BACKUP_DIR/runtime/"
  done
  [ -d "$ALLSCAN_DIR/img" ] && cp -a "$ALLSCAN_DIR/img" "$BACKUP_DIR/runtime/img"
  COPYFILE_DISABLE=1 tar --ignore-failed-read --warning=no-file-changed \
    --exclude='allscan/bridge-live.json' \
    --exclude='allscan/connected-clients.json' \
    --exclude='allscan/zello-status-data.json' \
    --exclude='allscan/astdb.txt' \
    --exclude='allscan/backup-*' \
    --exclude='allscan/*.bak' \
    --exclude='allscan/*.bak.*' \
    --exclude='allscan/._*' \
    --exclude='allscan/.DS_Store' \
    -czf "$BACKUP_DIR/allscan-webroot.tar.gz" -C "$WEB_ROOT" allscan
fi
if [ -f /etc/allscan/allscan.db ]; then
  install -o root -g root -m 600 /etc/allscan/allscan.db "$BACKUP_DIR/allscan.db"
fi
CHANGES_STARTED=1

echo "[2/8] Checking the official AllScan backend..."
if [ "$current_version" != "$latest_version" ]; then
  echo "Official AllScan will be installed or upgraded: $current_version -> $latest_version"
  [ -t 0 ] || fail "The official AllScan update requires an interactive terminal."
  if ask "Run David Gleason's official AllScan installer now? [Y/n]" y; then
    official_installer="/tmp/AllScanInstallUpdate-$(date +%s).php"
    curl -fsSL "$OFFICIAL_INSTALLER_URL" -o "$official_installer"
    chmod 755 "$official_installer"
    echo "The official installer will explain and confirm its own update steps."
    "$official_installer"
    rm -f "$official_installer"
  else
    fail "Official AllScan installation/update was declined."
  fi
else
  echo "Official AllScan is already current ($current_version)."
fi

[ -d "$ALLSCAN_DIR" ] || fail "Official AllScan installation was not completed."
installed_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$ALLSCAN_DIR/include/common.php" | head -1)
[ "$installed_version" = "$latest_version" ] || fail "Official AllScan is still $installed_version; expected $latest_version."

if [ -d "$BACKUP_DIR/runtime" ]; then
  for runtime_file in bridge-live.json connected-clients.json zello-status-data.json; do
    if [ -s "$BACKUP_DIR/runtime/$runtime_file" ] && [ ! -s "$ALLSCAN_DIR/$runtime_file" ]; then
      cp -p "$BACKUP_DIR/runtime/$runtime_file" "$ALLSCAN_DIR/$runtime_file"
    fi
  done
  if [ -d "$BACKUP_DIR/runtime/img" ] && [ ! -d "$ALLSCAN_DIR/img" ]; then
    cp -a "$BACKUP_DIR/runtime/img" "$ALLSCAN_DIR/img"
  fi
fi

echo "[3/8] Installing the Reimagined master files outside the web root..."
mkdir -p "$RELEASE_DIR"
cp -a "$PAYLOAD_DIR/." "$RELEASE_DIR/"
chown -R root:root "$RELEASE_DIR"
find "$RELEASE_DIR" -type d -exec chmod 755 {} +
find "$RELEASE_DIR" -type f -exec chmod 644 {} +
chmod 755 "$RELEASE_DIR/bin/"*.sh "$RELEASE_DIR/scripts/"*.sh
ln -sfn "$RELEASE_DIR" /opt/allscan-reimagined/current

echo "[4/8] Detecting node identity, branding, and bridges..."
"$RELEASE_DIR/scripts/asr-configure.sh"

echo "[5/8] Applying the Reimagined interface and security protections..."
"$RELEASE_DIR/scripts/asr-reapply.sh"

echo "[6/8] Installing automatic update-survival services..."
install -o root -g root -m 755 "$RELEASE_DIR/scripts/asr-reapply.sh" /usr/local/sbin/allscan-reimagined-reapply
install -o root -g root -m 755 "$RELEASE_DIR/scripts/asr-integrity-check.sh" /usr/local/sbin/allscan-reimagined-integrity-check
cat > /etc/systemd/system/allscan-reimagined-reapply.service <<'EOF'
[Unit]
Description=Reapply AllScan Reimagined after an official AllScan update
After=apache2.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/allscan-reimagined-integrity-check
EOF
cat > /etc/systemd/system/allscan-reimagined-reapply.path <<EOF
[Unit]
Description=Watch official AllScan files for replacement

[Path]
PathChanged=$ALLSCAN_DIR/include/common.php
Unit=allscan-reimagined-reapply.service

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/allscan-reimagined-reapply.timer <<'EOF'
[Unit]
Description=Periodic AllScan Reimagined integrity check

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Unit=allscan-reimagined-reapply.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now allscan-reimagined-reapply.path allscan-reimagined-reapply.timer

echo "[7/8] Validating the installed application..."
php -l "$ALLSCAN_DIR/asr-api.php" >/dev/null
bash -n /usr/local/bin/allscan_wt_clients.sh
runtime_json=$(curl -fsS "http://127.0.0.1/allscan/asr-api.php?action=runtime-config")
printf '%s' "$runtime_json" | php -r '
  $data = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
  if (($data["ok"] ?? false) !== true || empty($data["node"])) exit(1);
'
auth_json=$(curl -fsS "http://127.0.0.1/allscan/asr-api.php?action=auth-status")
printf '%s' "$auth_json" | php -r '
  $data = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
  if (($data["publicPermission"] ?? 2) > 2) {
      fwrite(STDERR, "Public Permission must be Read Only or lower before internet exposure.\n");
      exit(1);
  }
'
curl -fsS http://127.0.0.1/allscan/ | grep -q 'assets/index-'

echo "[8/8] Installation complete."
echo
echo "AllScan backend:       $latest_version"
echo "AllScan Reimagined:    v1.0.0 Beta 2"
echo "Personal configuration: /etc/allscan-reimagined/config.json"
echo "Rollback backup:        $BACKUP_DIR"
echo "Open:                    http://$(hostname -I | awk '{print $1}')/allscan/"
echo
echo "Existing user accounts and passwords were not changed."
CHANGES_STARTED=0
trap - ERR
