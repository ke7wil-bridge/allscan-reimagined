# ASR Plus HTTPS activation runbook
Status: staged only; DNS and live qualification pending.
Use asrplus.netoholicsnetwork.net on Kentucky 674982 (44.30.157.21).
1. Verify public A record and absence of conflicting AAAA; confirm inbound TCP 80/443 and Apache vhost inventory. Preserve existing HTTP dashboard.
2. Back up Apache configuration. Install Debian certbot and python3-certbot-apache; enable SSL module. Configure a dedicated named vhost using the verified existing document root and access rules. Run apache2ctl configtest before reload.
3. Obtain Let's Encrypt certificate using certbot certonly --apache -d asrplus.netoholicsnetwork.net. Configure dedicated TLS vhost with issued fullchain and private key; no global HTTP redirect. Configtest and reload.
4. Verify external trusted certificate, hostname, dashboard session login, API and HTTP compatibility. Enable/check certbot.timer and run certbot renew --dry-run. Verify Apache reload on successful renewal.
5. Only after TLS passes, back up existing session API and compare against current source. Install reviewed token helper, restricted sudoers (visudo -cf), dedicated root-only /var/lib/asrplus/tokens directory and API as a single rollback-capable transaction. Verify session compatibility, short-lived scoped test token, cross-node denial, expiry, revocation and audit. Never transmit a bearer token over HTTP or log its value.
6. Roll back API/helper/sudoers together if any check fails; preserve audit and token state. Restore Apache backup if TLS changes fail. No Asterisk restart.
Texas and UK remain separate activation gates. No release publication authorized.
