#!/bin/bash
set -Eeuo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }

live=/opt/allscan-reimagined-bridges/dmr_johnny2/analog/Analog_Bridge.ini
render_a=/opt/allscan-reimagined/current/scripts/asr-bridge-setup-dmr-render.py
render_b=/usr/local/libexec/allscan-reimagined/asr-bridge-setup-dmr-render.py
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/var/backups/allscan-reimagined/johnny2-dmr-analog-gain-$stamp
install -d -m 700 "$backup"
cp -a "$live" "$backup/Analog_Bridge.ini"
cp -a "$render_a" "$backup/asr-bridge-setup-dmr-render.current.py"
cp -a "$render_b" "$backup/asr-bridge-setup-dmr-render.libexec.py"

for file in "$render_a" "$render_b"; do
  grep -Fq "colorCode={s['color_code']}" "$file"
  grep -Fq "tlvGain={g['aslToDmr']:.4f}" "$file"
  perl -0pi -e 's/(colorCode=\{s\['"'"'color_code'"'"'\]\}\n)tlvGain=\{g\['"'"'aslToDmr'"'"'\]:\.4f\}\n/$1/; s/(tlvAudio=AUDIO_USE_GAIN\n)(?!tlvGain=)/$1tlvGain={g['"'"'aslToDmr'"'"']:.4f}\n/' "$file"
  count=$(grep -Fc "tlvGain={g['aslToDmr']:.4f}" "$file")
  [ "$count" -eq 1 ]
done

grep -Fxq 'tlvGain=1.0000' "$live"
perl -0pi -e 's/(colorCode=1\n)tlvGain=1\.0000\n/$1/; s/(tlvAudio=AUDIO_USE_GAIN\n)(?!tlvGain=)/$1tlvGain=1.0000\n/' "$live"

systemctl restart asr-dmr-analog-dmr_johnny2.service
sleep 3
systemctl is-active --quiet asr-dmr-analog-dmr_johnny2.service
ss -lunH | awk '{print $4}' | grep -Eq '(^|:)23162$'
grep -A8 '^\[USRP\]' "$live" | grep -Fxq 'tlvGain=1.0000'
! sed -n '/^\[AMBE_AUDIO\]/,/^\[/p' "$live" | grep -q '^tlvGain='

echo "DMR analog bridge repaired and running. Backup: $backup"
