# ASR Plus signing activation staging bundle

**STAGED ONLY — DO NOT COPY OVER PRODUCTION FILES YET.** No keys are included. The three production hosts were inspected read-only; the shared module and signing configuration are currently absent.

## Bundle map

- `masterhub/`: central control helper, remote endpoint, shared integrity module, and central config for Phoenix (`phoenix`) and KY2's legacy internal identity (`georgia`).
- `phoenix/`: unified agent, shared module, and merged `/etc/asrplus-agent.json` based on the live config, retaining SSH server/key/known-hosts, astdb, and public-status paths.
- `ky2/`: unified agent under its live executable name, shared module, merged control config retaining `controlPath` and SSH identity, and unchanged watcher config. The watcher intentionally receives no signing-key reference.

All hubs start in `optional` compatibility mode. This is a migration setting, not the final security posture. Do not switch to `required` until both agents have verified the signed read-only qualification and queues are drained.

## Key provisioning still required

Generate **two independent random 256-bit keys**, one Phoenix and one KY2. The same per-hub key must be installed on MasterHub and its matching remote hub, with owner `root:root`, mode `0600`, under `/etc/asrplus/command-keys/`. Never put key bytes in this repository, command-line arguments, shell history, chat, logs, or issue comments. Do not reuse one hub's key for another. No keys have been generated or installed by this staging step.

## Coordinated install still required

1. Create protected key directories and provision matching keys out-of-band.
2. Back up current executables, configs, units, queue, result, audit and ledger state.
3. Install module beside each entrypoint, then its matching code/config as one versioned set.
4. Preserve Phoenix's live systemd drop-ins (`asl-ban.conf`, `event-rate.conf`) and existing Asterisk writable paths; merge unit changes, never replace the live unit wholesale.
5. Preserve KY2's separate control and watcher services and `/run/asrplus-ky` paths. Watcher remains keyless.
6. Run deployed-interpreter import/config/permission tests on each host before service activation. Then execute signed read-only `ban-list` qualification.
7. Required-mode cutover is a separate approval after qualification and queue drain.

This bundle is not deployed. No service was restarted and no production file was changed.
