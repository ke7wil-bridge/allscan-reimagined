#!/bin/bash
# Run only inside the dedicated Debian QA guest, never on an existing node.
set -Eeuo pipefail
[ "$(hostname)" = thomas-asr-qa ] || { echo 'Wrong host'; exit 1; }
[ "$(. /etc/os-release; echo "$VERSION_ID")" = 13 ] || exit 1
[ "$EUID" = 0 ] || exit 1
[ ! -f /root/asr-qa/asl-provisioned ] || { echo 'Already provisioned; existing settings preserved'; exit 1; }
systemctl mask asterisk.service
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl wget git python3 php-cli php-sqlite3 php-curl apache2 unzip rsync nftables
curl --fail --location --output /tmp/asl-apt-repos.deb https://repo.allstarlink.org/public/asl-apt-repos.deb13_all.deb
dpkg -i /tmp/asl-apt-repos.deb
apt-get update
apt-get install -y asl3
install -d -m 700 /root/asr-qa
python3 - <<'PY'
import json, re, secrets
from pathlib import Path
secret = secrets.token_urlsafe(32)
p = Path('/root/asr-qa/ami.json')
p.write_text(json.dumps({'username': 'asrqa', 'secret': secret}))
p.chmod(0o600)
Path('/etc/asterisk/manager.conf').write_text('''[general]
enabled=yes
port=5038
bindaddr=127.0.0.1
[asrqa]
secret=''' + secret + '''
deny=0.0.0.0/0.0.0.0
permit=127.0.0.1/255.255.255.255
read=all
write=all
''')
Path('/etc/asterisk/manager.conf').chmod(0o640)
Path('/etc/asterisk/iax.conf').write_text('''[general]
bindaddr=127.0.0.1
bindport=4569
disallow=all
allow=ulaw
''')
Path('/etc/asterisk/rpt.conf').write_text('''[1999]
rxchannel=USRP/127.0.0.1:34099:32099
duplex=0
hangtime=0
althangtime=0
totime=180000
idrecording=|iASRQA
idtime=0
politeid=0
functions=functions
nodes=nodes
[functions]
[nodes]
1999=radio@127.0.0.1/1999,NONE
''')
Path('/etc/asterisk/rpt_http_registrations.conf').write_text('; QA: no public registrations\n')
Path('/etc/asterisk/rpt_registrations.conf').write_text('; QA: no public registrations\n')
modules = Path('/etc/asterisk/modules.conf')
text = re.sub(r'^noload\s*=\s*chan_usrp.so.*$', 'load = chan_usrp.so', modules.read_text(), flags=re.M)
modules.write_text(text)
PY
chown root:asterisk /etc/asterisk/manager.conf
systemctl unmask asterisk.service
systemctl enable asterisk.service
systemctl start asterisk.service
systemctl enable --now apache2.service
usermod -aG asterisk www-data
systemctl restart apache2.service
asterisk -rx 'core show version'
asterisk -rx 'module show like app_rpt'
asterisk -rx 'rpt nodes 1999'
touch /root/asr-qa/asl-provisioned
