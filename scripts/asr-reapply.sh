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

echo "Reapplying AllScan Reimagined interface..."
cp -a "$MASTER_DIR/web/." "$ALLSCAN_DIR/"
install -o root -g root -m 644 "$MASTER_DIR/server/asr-api.php" "$ALLSCAN_DIR/asr-api.php"
install -o root -g root -m 755 "$MASTER_DIR/bin/allscan_wt_clients.sh" /usr/local/bin/allscan_wt_clients.sh

backend_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$ALLSCAN_DIR/include/common.php" | head -1)
compat_dir="$MASTER_DIR/compat/allscan-${backend_version:-unknown}"
if [ -d "$compat_dir" ]; then
  echo "Applying verified Reimagined compatibility layer for AllScan $backend_version..."
  install -o root -g root -m 644 "$compat_dir/include/common.php" "$ALLSCAN_DIR/include/common.php"
  install -o root -g root -m 644 "$compat_dir/include/UserModel.php" "$ALLSCAN_DIR/include/UserModel.php"
  install -o root -g root -m 644 "$compat_dir/user/settings/index.php" "$ALLSCAN_DIR/user/settings/index.php"
  install -o root -g root -m 644 "$compat_dir/css/asr-admin.css" "$ALLSCAN_DIR/css/asr-admin.css"
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
EOF
chmod 440 /etc/sudoers.d/allscan-reimagined
visudo -cf /etc/sudoers.d/allscan-reimagined >/dev/null

chown -R root:root "$ALLSCAN_DIR"
find "$ALLSCAN_DIR" -type d -exec chmod 755 {} +
find "$ALLSCAN_DIR" -type f -exec chmod 644 {} +

[ -s "$ALLSCAN_DIR/bridge-live.json" ] || printf '%s\n' '{"updated":""}' > "$ALLSCAN_DIR/bridge-live.json"
[ -s "$ALLSCAN_DIR/connected-clients.json" ] || printf '%s\n' '{}' > "$ALLSCAN_DIR/connected-clients.json"

for runtime_file in "$ALLSCAN_DIR"/favorites*.ini \
  "$ALLSCAN_DIR/bridge-live.json" \
  "$ALLSCAN_DIR/connected-clients.json" \
  "$ALLSCAN_DIR/zello-status-data.json"; do
  [ -f "$runtime_file" ] || continue
  chown "root:$WEB_GROUP" "$runtime_file"
  chmod 664 "$runtime_file"
done

[ -f "$ALLSCAN_DIR/AllScanInstallUpdate.php" ] && chmod 755 "$ALLSCAN_DIR/AllScanInstallUpdate.php"
[ -f "$ALLSCAN_DIR/docs/extensions.conf" ] && chmod 600 "$ALLSCAN_DIR/docs/extensions.conf"
[ -f "$ALLSCAN_DIR/docs/rpt.conf" ] && chmod 600 "$ALLSCAN_DIR/docs/rpt.conf"
if [ -f /etc/allscan/allscan.db ]; then
  chown "$WEB_GROUP:$WEB_GROUP" /etc/allscan/allscan.db
  chmod 660 /etc/allscan/allscan.db
fi

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

<IfModule mod_php.c>
    php_admin_value session.cookie_httponly 1
    php_admin_value session.cookie_samesite Strict
    php_admin_value session.use_strict_mode 1
</IfModule>
EOF
  a2enmod headers >/dev/null
  a2enconf allscan-reimagined >/dev/null
  apache2ctl configtest
  systemctl reload apache2
fi

echo "AllScan Reimagined interface and security protections are active."
