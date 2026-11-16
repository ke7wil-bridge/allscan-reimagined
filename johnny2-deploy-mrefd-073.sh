#!/bin/bash
set -Eeuo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }

root=/opt/mrefd-073
unit=/etc/systemd/system/mrefd-073.service
commit=cd1bdff9bdf768b474ea2a26bb8830ae3b0ee0ed
created=0

rollback() {
  status=$?
  trap - ERR INT TERM
  if [ "$created" -eq 1 ]; then
    echo "MREFd deployment failed; startup diagnostics follow:" >&2
    systemctl status mrefd-073.service --no-pager >&2 || true
    journalctl -u mrefd-073.service -n 80 --no-pager -o cat >&2 || true
    systemctl disable --now mrefd-073.service >/dev/null 2>&1 || true
    [ ! -f "$unit" ] || rm -f -- "$unit"
    systemctl daemon-reload >/dev/null 2>&1 || true
    if [ -d "$root" ] && [ ! -L "$root" ]; then rm -rf -- "$root"; fi
  fi
  exit "$status"
}
trap rollback ERR INT TERM

[ ! -e "$root" ] && [ ! -L "$root" ] || { echo "Refusing to adopt an existing MREFd root." >&2; exit 1; }
[ ! -e "$unit" ] && [ ! -L "$unit" ] || { echo "Refusing to replace an existing MREFd unit." >&2; exit 1; }
! ss -lunH | awk '{print $4}' | grep -Eq '(^|:)17000$' || { echo "UDP 17000 is already occupied." >&2; exit 1; }
id asr-bridge >/dev/null 2>&1 || { echo "The ASR service account is missing." >&2; exit 1; }

install -d -o root -g root -m 755 "$root" "$root/bin" "$root/config"
created=1
git clone --quiet https://github.com/n7tae/mrefd.git "$root/source"
git -C "$root/source" checkout --quiet "$commit"
[ "$(git -C "$root/source" rev-parse HEAD)" = "$commit" ]

cat > "$root/source/mrefd.mk" <<'EOF'
USESYMLINK = false
BINDIR = /opt/mrefd-073/bin
CFGDIR = /opt/mrefd-073/config
DEBUG = false
DHT = false
DAEMON = false
EOF

make -C "$root/source" -j2
install -o root -g root -m 755 "$root/source/mrefd" "$root/bin/mrefd"
install -o root -g asr-bridge -m 640 "$root/source/config/mrefd.blacklist" "$root/config/mrefd.blacklist"
install -o root -g asr-bridge -m 640 "$root/source/config/mrefd.whitelist" "$root/config/mrefd.whitelist"
install -o root -g asr-bridge -m 640 "$root/source/config/mrefd.interlink" "$root/config/mrefd.interlink"

cat > "$root/config/mrefd.cfg" <<'EOF'
Callsign = M17-073
Modules = A
IPv4BindAddr = 0.0.0.0
Port = 17000
PidPath = /run/mrefd-073/mrefd.pid
JsonPath = /run/mrefd-073/mrefd.json
WhitelistPath = /opt/mrefd-073/config/mrefd.whitelist
BlacklistPath = /opt/mrefd-073/config/mrefd.blacklist
InterlinkPath = /opt/mrefd-073/config/mrefd.interlink
EOF
chown root:asr-bridge "$root/config/mrefd.cfg"
chmod 640 "$root/config/mrefd.cfg"

cat > "$root/.asr-migration-owner.json" <<EOF
{"schema":1,"owner":"johnny2-dvswitch-migration","service":"mrefd-073.service","commit":"$commit","designator":"M17-073","port":17000,"module":"A"}
EOF
chmod 640 "$root/.asr-migration-owner.json"

cat > "$unit" <<'EOF'
[Unit]
Description=Johnny2 standalone M17-073 MREFd reflector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=asr-bridge
Group=asr-bridge
RuntimeDirectory=mrefd-073
RuntimeDirectoryMode=0750
WorkingDirectory=/opt/mrefd-073
ExecStart=/opt/mrefd-073/bin/mrefd /opt/mrefd-073/config/mrefd.cfg
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictNamespaces=true
CapabilityBoundingSet=
AmbientCapabilities=
ReadWritePaths=/run/mrefd-073

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$unit"

systemctl daemon-reload
systemctl enable --now mrefd-073.service
for _ in $(seq 1 30); do
  if systemctl is-active --quiet mrefd-073.service && ss -lunH | awk '{print $4}' | grep -Eq '(^|:)17000$'; then
    break
  fi
  sleep 1
done
systemctl is-active --quiet mrefd-073.service
ss -lunH | awk '{print $4}' | grep -Eq '(^|:)17000$'
test -e /run/mrefd-073/mrefd.json

trap - ERR INT TERM
echo "MREFd M17-073 deployed successfully on UDP 17000, module A."
echo "Pinned source: $commit"
