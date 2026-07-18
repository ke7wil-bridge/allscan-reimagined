#!/bin/bash
set -Eeuo pipefail

MASTER_DIR="/opt/allscan-reimagined/current"
CONFIG_DIR="/etc/allscan-reimagined"
DATA_DIR="/var/lib/allscan-reimagined"

if [ -d /var/www/html/allscan ]; then
  ALLSCAN_DIR="/var/www/html/allscan"
elif [ -d /srv/http/allscan ]; then
  ALLSCAN_DIR="/srv/http/allscan"
else
  echo "AllScan installation not found." >&2
  exit 1
fi

[ -d "$MASTER_DIR/web" ] || { echo "Reimagined master web files are missing." >&2; exit 1; }
[ -f "$MASTER_DIR/server/asr-api.php" ] || { echo "Reimagined API is missing." >&2; exit 1; }

WEB_GROUP="www-data"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="apache"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="http"
getent group "$WEB_GROUP" >/dev/null 2>&1 || { echo "Web-server group not found." >&2; exit 1; }

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

echo "Reapplying AllScan Reimagined interface..."
cp -a "$MASTER_DIR/web/." "$ALLSCAN_DIR/"
if [ -d "$MASTER_DIR/web/assets" ] && [ -d "$ALLSCAN_DIR/assets" ]; then
  for asset in "$ALLSCAN_DIR"/assets/index-*.js "$ALLSCAN_DIR"/assets/index-*.css; do
    [ -f "$asset" ] || continue
    [ -f "$MASTER_DIR/web/assets/${asset##*/}" ] || rm -f -- "$asset"
  done
fi
install -o root -g root -m 644 "$MASTER_DIR/server/asr-api.php" "$ALLSCAN_DIR/asr-api.php"
install -o root -g root -m 755 "$MASTER_DIR/bin/allscan_wt_clients.sh" /usr/local/bin/allscan_wt_clients.sh
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-asterisk-read.sh" /usr/local/sbin/allscan-reimagined-asterisk-read
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-friendly-names.php" /usr/local/sbin/allscan-reimagined-friendly-names
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-bridge-clients.php" /usr/local/sbin/allscan-reimagined-bridge-clients
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-manager-perms.sh" /usr/local/sbin/allscan-reimagined-manager-perms
install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-patch-connected-clients.py" /usr/local/sbin/allscan-reimagined-patch-connected-clients
mkdir -p "$CONFIG_DIR"
chown "root:$WEB_GROUP" "$CONFIG_DIR"
chmod 775 "$CONFIG_DIR"
[ -f "$CONFIG_DIR/config.json" ] && chown "root:$WEB_GROUP" "$CONFIG_DIR/config.json"
[ -f "$CONFIG_DIR/config.json" ] && chmod 664 "$CONFIG_DIR/config.json"
[ -f "$CONFIG_DIR/secrets.json" ] && chown "root:$WEB_GROUP" "$CONFIG_DIR/secrets.json"
[ -f "$CONFIG_DIR/secrets.json" ] && chmod 640 "$CONFIG_DIR/secrets.json"
cat > /etc/tmpfiles.d/allscan-reimagined.conf <<EOF
d /run/allscan-reimagined 0775 root $WEB_GROUP -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/allscan-reimagined.conf
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
cat > /etc/systemd/system/allscan-reimagined-bridge-clients.service <<'EOF'
[Unit]
Description=Collect AllScan Reimagined bridge connected-client status
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
ExecStart=/usr/local/sbin/allscan-reimagined-bridge-clients --once
EOF
cat > /etc/systemd/system/allscan-reimagined-bridge-clients.timer <<'EOF'
[Unit]
Description=Refresh AllScan Reimagined bridge connected-client status

[Timer]
OnBootSec=30s
OnUnitActiveSec=15s
AccuracySec=2s
RandomizedDelaySec=3s
Unit=allscan-reimagined-bridge-clients.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
bridge_count=$(php -r '
  $data = json_decode((string) @file_get_contents($argv[1]), true);
  echo is_array($data["bridges"] ?? null) ? count($data["bridges"]) : 0;
' "$CONFIG_DIR/config.json" 2>/dev/null || printf '0')
if [ "$bridge_count" -gt 0 ]; then
  systemctl enable --now allscan-reimagined-bridge-clients.timer >/dev/null 2>&1 || true
else
  systemctl disable --now allscan-reimagined-bridge-clients.timer >/dev/null 2>&1 || true
fi
if systemctl list-unit-files connected-clients-daemon.service --no-legend 2>/dev/null | grep -q '^connected-clients-daemon\.service'; then
  install -d -o root -g root -m 755 /etc/systemd/system/connected-clients-daemon.service.d
  cat > /etc/systemd/system/connected-clients-daemon.service.d/asr-resource-guard.conf <<'EOF'
[Service]
MemoryHigh=128M
MemoryMax=192M
EOF
  systemctl daemon-reload
  if /usr/local/sbin/allscan-reimagined-patch-connected-clients; then
    systemctl try-restart connected-clients-daemon.service >/dev/null 2>&1 || true
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

backend_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$ALLSCAN_DIR/include/common.php" | head -1)
compat_dir="$MASTER_DIR/compat/allscan-${backend_version:-unknown}"
if [ -d "$compat_dir" ]; then
  echo "Applying verified Reimagined compatibility layer for AllScan $backend_version..."
  install -o root -g root -m 644 "$compat_dir/include/common.php" "$ALLSCAN_DIR/include/common.php"
  install -o root -g root -m 644 "$compat_dir/include/UserModel.php" "$ALLSCAN_DIR/include/UserModel.php"
  install -o root -g root -m 644 "$compat_dir/user/settings/index.php" "$ALLSCAN_DIR/user/settings/index.php"
  install -d -o root -g root -m 755 "$ALLSCAN_DIR/asr-settings"
  install -o root -g root -m 644 "$compat_dir/asr-settings/index.php" "$ALLSCAN_DIR/asr-settings/index.php"
  install -d -o root -g root -m 755 "$ALLSCAN_DIR/lookup"
  install -o root -g root -m 644 "$compat_dir/lookup/index.php" "$ALLSCAN_DIR/lookup/index.php"
  install -d -o root -g root -m 755 "$ALLSCAN_DIR/echolink-lookup"
  install -o root -g root -m 644 "$compat_dir/echolink-lookup/index.php" "$ALLSCAN_DIR/echolink-lookup/index.php"
  install -d -o root -g root -m 755 "$ALLSCAN_DIR/performance"
  install -o root -g root -m 644 "$compat_dir/performance/index.php" "$ALLSCAN_DIR/performance/index.php"
  install -o root -g root -m 644 "$compat_dir/css/asr-admin.css" "$ALLSCAN_DIR/css/asr-admin.css"
  if [ -d "$compat_dir/astapi" ]; then
    install -o root -g root -m 644 "$compat_dir/astapi/server.php" "$ALLSCAN_DIR/astapi/server.php"
    install -o root -g root -m 644 "$compat_dir/astapi/AMI.php" "$ALLSCAN_DIR/astapi/AMI.php"
  fi
else
  logger -t allscan-reimagined "No verified admin/security compatibility layer for AllScan ${backend_version:-unknown}; upstream files left unchanged"
  echo "WARNING: No verified admin-page compatibility layer exists for AllScan ${backend_version:-unknown}."
fi

if [ -d "$DATA_DIR" ]; then
  for logo in "$DATA_DIR"/header-logo.*; do
    [ -f "$logo" ] || continue
    extension="${logo##*.}"
    install -o root -g root -m 644 "$logo" "$ALLSCAN_DIR/asr-custom-logo.$extension"
  done
fi

cat > /etc/sudoers.d/allscan-reimagined <<EOF
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/bin/allscan_wt_clients.sh
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asterisk-read
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-friendly-names
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-clients
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
if [ -s /etc/allscan/favorites.ini ]; then
  install -o root -g "$WEB_GROUP" -m 664 /etc/allscan/favorites.ini "$ALLSCAN_DIR/favorites.ini"
fi

for runtime_file in "$ALLSCAN_DIR"/favorites*.ini \
  "$ALLSCAN_DIR/bridge-live.json" \
  "$ALLSCAN_DIR/connected-clients.json" \
  "$ALLSCAN_DIR/asr-connected-clients.json" \
  "$ALLSCAN_DIR/zello-status-data.json"; do
  [ -f "$runtime_file" ] || continue
  safe_chown_files "root:$WEB_GROUP" "$runtime_file"
  safe_chmod_files 664 "$runtime_file"
done

[ -f "$ALLSCAN_DIR/AllScanInstallUpdate.php" ] && chmod 755 "$ALLSCAN_DIR/AllScanInstallUpdate.php"
[ -f "$ALLSCAN_DIR/docs/extensions.conf" ] && chmod 600 "$ALLSCAN_DIR/docs/extensions.conf"
[ -f "$ALLSCAN_DIR/docs/rpt.conf" ] && chmod 600 "$ALLSCAN_DIR/docs/rpt.conf"
if [ -f /etc/allscan/allscan.db ]; then
  chown "$WEB_GROUP:$WEB_GROUP" /etc/allscan/allscan.db
  chmod 660 /etc/allscan/allscan.db
fi
if [ -f /etc/allscan/favorites.ini ]; then
  chown "root:$WEB_GROUP" /etc/allscan/favorites.ini
  chmod 664 /etc/allscan/favorites.ini
fi
/usr/local/sbin/allscan-reimagined-friendly-names --once >/dev/null 2>&1 || true
[ -f /etc/allscan/asdb.txt ] && chown "root:$WEB_GROUP" /etc/allscan/asdb.txt
[ -f /etc/allscan/asdb.txt ] && chmod 664 /etc/allscan/asdb.txt
/usr/local/sbin/allscan-reimagined-manager-perms >/dev/null 2>&1 || true
if [ "$bridge_count" -gt 0 ]; then
  /usr/local/sbin/allscan-reimagined-bridge-clients --once >/dev/null 2>&1 || true
else
  printf '%s\n' '{}' > "$ALLSCAN_DIR/asr-connected-clients.json"
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
    <Location "/allscan">
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
