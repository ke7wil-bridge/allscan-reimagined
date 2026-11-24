# ASR Plus signing module installation layout — staging specification

**Not an authorization to deploy.** All installation and service switches require a separate approved window.

## Required pairing

Every Python entrypoint imports `asrplus_command_integrity` as a sibling module. A safe deployment must stage the **same reviewed version** of `asrplus_command_integrity.py` alongside each of `asrplus-agent.py`, `asrplus-control.py`, and `asrplus-remote-endpoint.py`. For example, a versioned, root-owned directory can hold a complete tested set of Python files, with entrypoint symlinks or unit `ExecStart` updated only during coordinated activation. Do not replace a live executable without its matching module.

The deployment-layout test `python3 scripts/asrplus-import-layout-self-test.py` verifies all three entrypoints import in an isolated Python interpreter with no `PYTHONPATH` dependency. It does **not** test deployed systemd sandbox permissions or command execution.

## Per-host constraints

- **MasterHub:** both central entrypoints must import the module and access the root-only hub signing keys. Preserve existing queue/results directory modes and root-run dashboard sudo helper behavior.
- **Phoenix:** preserve existing `/var/lib/allscan-reimagined/admin`, `/etc/asterisk`, `/var/lib/asterisk` writable paths in the merged unit; add only required ledger/runtime paths. Keep the existing agent config, SSH key and known-hosts settings.
- **KY2:** preserve separate control and watcher units/configs and `/run/asrplus-ky` watcher paths. Watcher-only mode must not require signing-key access. Do not rename the internal `georgia` hub identifier.

## Verification before switch

Verify ownership (`root:root`), executable permissions, module import using the **same Python interpreter** as the service, syntax checks, config readability, required `ReadWritePaths`, ledger persistence, and rollback file presence. No secrets should appear in command output, logs, docs, or git history. Re-run offline regression tests before requesting approval.
