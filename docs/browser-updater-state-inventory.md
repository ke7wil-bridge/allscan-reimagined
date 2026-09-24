# Browser updater state inventory (Beta 8 qualification)

The updater reuses `install.sh` backup and `asr-reapply.sh` restoration. This is an inventory for sandbox qualification, not a claim that every state path has passed destructive testing.

| User state | Location | Current protection | Sandbox check |
| --- | --- | --- | --- |
| AllStar node configuration | `/etc/asterisk`, stock AllScan files | Installer does not replace the Asterisk configuration; stock installer is blocked for browser updates | Hash selected node files before and after update and rollback |
| ASR node, bridges, login, scanning, dashboard server settings, Custom Commands | `/etc/allscan-reimagined/config.json` | Root backup of protected config and reapply keeps existing config | Compare JSON and file digest after update and rollback |
| Other protected ASR secrets and station map | `/etc/allscan-reimagined/secrets.json`, `station-map-cache.json` | Protected backup, rollback restore | Compare encrypted digests without printing contents |
| Favorites and metadata (description, order, color, appearance) | `/etc/allscan/favorites.ini`, `/etc/allscan/allscan.db`, ASR user content | Installer backs up canonical Favorites and database, reapply retains canonical source | Add metadata-rich fixtures and compare values |
| Branding, images, custom assets | `/var/lib/allscan-reimagined/header-logo.*`, `/asr/img`, `/asr/asr-user-content`, root-level image files | Persistent directory unchanged; webroot archive and reapply copy uploaded assets | Hash and compare files across update and rollback |
| TGIF credentials and user sessions | `/etc/allscan-reimagined/connected-clients-daemon.env`, systemd token drop-in, `/var/lib/allscan-reimagined/tgif-users` | Environment and drop-in backed up; persistent session directory retained | Compare digests, file modes, and session behavior without displaying tokens |
| Bridge state and other ASR persistent data | `/var/lib/allscan-reimagined`, `/etc/allscan-reimagined` | Installer does not replace these directories; individual protected files have backup | Inventory all files and compare unchanged user-owned entries |
| Browser-only dashboard preferences | Browser local storage | Server installer does not touch browser storage | Check in the same browser profile before and after update |

Release assets and executable helpers are release-owned. Logs and ephemeral bridge status are not user settings. The sandbox qualification must verify that newly added user-owned files are covered by either untouched persistent paths or the explicit backup and rollback paths. A forced failure must also verify the pre-update served version and the selected user fixtures.
