#!/bin/bash
set -euo pipefail

source_dir=/opt/asrplus/current/web
destination=/var/www/html/asrplus
web_group=www-data
getent group "$web_group" >/dev/null 2>&1 || web_group=apache

test -f "$source_dir/index.php"
test -f "$source_dir/api.php"
test -f "$source_dir/asrplus.js"
test -f "$source_dir/asrplus.css"
test -f "$source_dir/netoholics-logo.png"
test -r /var/www/html/asr/include/common.php
php -l "$source_dir/index.php" >/dev/null
php -l "$source_dir/api.php" >/dev/null
php -r 'require "/var/www/html/asr/include/common.php"; exit(function_exists("asInit") ? 0 : 1);'

install -d -o root -g "$web_group" -m 0750 "$destination"
for file in index.php api.php asrplus.js asrplus.css netoholics-logo.png; do
  install -o root -g "$web_group" -m 0644 "$source_dir/$file" "$destination/$file"
done
