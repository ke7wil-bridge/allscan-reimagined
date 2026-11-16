#!/bin/bash
# Disable internet directory fetches only in the isolated test guest.
set -Eeuo pipefail
[ "$(hostname)" = thomas-asr-qa ] || exit 1
systemctl disable --now asl3-update-astdb.timer asl3-update-nodelist.timer
systemctl disable --now asl3-update-astdb.service asl3-update-nodelist.service
systemctl reset-failed
# Stock installer can change this group; Asterisk must retain read access on boot.
chown root:asterisk /etc/asterisk/manager.conf
chmod 640 /etc/asterisk/manager.conf
usermod -aG asterisk www-data
systemctl restart asterisk.service apache2.service
