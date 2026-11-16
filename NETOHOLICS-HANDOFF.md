# Netoholics Codex workspace handoff

Last verified: 2026-11-13 (America/Phoenix)

## Purpose

This file is the durable orientation record for Netoholics work in the AllScan Reimagined repository. It documents environment identity and safe operating assumptions; it is not authorization to deploy. Read `AGENTS.md` first.

## Development workspace

- VS Code is connected with Remote SSH to the Linux development machine at `100.68.7.32`.
- Linux host observed during setup: `josh-Latitude-5420`.
- Repository: `/home/josh/repos/allscan-reimagined`.
- GitHub remote observed: `https://github.com/ke7wil-bridge/allscan-reimagined.git` for fetch and push.
- Branch observed during setup: `beta-8.3.5/urf-install-diagnostics`.
- The shared checkout had 139 pending changes, including many modified and untracked files, before these workspace files were added. Those changes were not created, altered, stashed, committed, or discarded by this setup.
- Numerous issue, release, and feature worktrees already exist under `/home/josh/worktrees/`. Always inspect `git worktree list` and choose a new, non-conflicting task worktree.

The branch name and working-tree counts above are a dated snapshot, not a direction to continue on that branch. Re-check current state at the start of every task.

## Netoholics systems

### WordPress development — default experiment target

- Site: `https://dev.netoholicsnetwork.net`
- REST root: `https://dev.netoholicsnetwork.net/index.php/wp-json/`
- Remote SSH MCP profile: `netoholics_wordpress_dev`
- Use for experiments and pre-production verification. It remains an external system, so writes require a clear user request and connector safeguards.

### WordPress production

- Site: `https://netoholicsnetwork.net`
- REST root: `https://netoholicsnetwork.net/index.php/wp-json/`
- Remote SSH MCP profile: `netoholics_wordpress_prod`
- Treat every mutation as a production website change requiring explicit action-time confirmation.

The profiles are pinned to different hostnames and use separate protected credential files and backup directories. Never reuse a development precondition for production.

### Kentucky — production

- Public address: `44.30.157.21`
- Verified hostname: `nth-serv-p01`
- Role: Netoholics production VPS
- Treat every mutating action as a production change.

### Georgia — legacy

- Public address: `45.76.255.39`
- Verified hostname: `node674980`
- Role: legacy Netoholics node
- Legacy does not mean disposable. Preserve service and data unless an explicitly approved retirement plan says otherwise.

## SSH facts and verification

Josh's Mac has these verified aliases in its local SSH configuration:

- `kentucky` -> `root@44.30.157.21`
- `georgia` -> `root@45.76.255.39`

These aliases are Mac-only as of this verification. Do not use them as proof of Linux configuration.

On 2026-11-13, the Linux development machine was checked without displaying or transferring credentials:

- no `~/.ssh/config` was present;
- no active SSH authentication agent was available;
- a non-interactive, read-only SSH command to `root@44.30.157.21` succeeded and returned `nth-serv-p01` with UID `0`;
- a non-interactive, read-only SSH command to `root@45.76.255.39` succeeded and returned `node674980` with UID `0`.

This proves connectivity at that time only. It does not prove that a future session still has access, that aliases exist on Linux, or that deployment is authorized. Do not copy the Mac's private keys to Linux. Do not document key material, credentials, or secret-bearing command output.

## Required operating safeguards

1. Begin with `git status --short --branch`, `git remote -v`, and `git worktree list`.
2. Preserve the dirty shared checkout. Use a dedicated issue branch/worktree for implementation whenever practical.
3. Inspect before changing. Keep diagnostics read-only unless the user explicitly authorizes a live mutation.
4. Before any Kentucky mutation, obtain explicit confirmation for the exact action at the moment it is ready to run. Include effect, service interruption risk, backup, rollback, and verification. Apply the same standard to Georgia when a change could disrupt service or data.
5. Do not deploy, restart/stop services, install packages, edit live configuration, modify databases, change network/firewall rules, rotate credentials, or run destructive commands as part of routine repository work.
6. Never treat administrative capability as standing permission.
7. Keep GitHub implementation, release publication, and node deployment as separate, independently verified states.
8. Record live acceptance only from observed evidence. A commit, pull request, issue comment, or another person's deployment report is not by itself acceptance.

## Suggested task workflow

1. Identify the GitHub issue and acceptance criteria.
2. Fetch and inspect relevant refs without modifying the shared checkout.
3. Create a fresh worktree and task branch under `/home/josh/worktrees/`.
4. Make the smallest scoped change and add or update tests.
5. Run the relevant repository checks. For front-end changes, available scripts include `pnpm lint`, `pnpm build`, `pnpm test:support`, and `pnpm test:support:browser`. Installer and bridge work should use the matching repository self-tests after reviewing their behavior.
6. Review the diff for secrets, node-specific data, generated artifacts, and unrelated changes.
7. Prepare a pull request or handoff with validation results. Do not deploy unless separately requested and confirmed.
8. If deployment is approved, capture the exact commit/version, backup, commands/effects, rollback path, and post-change verification without recording secrets.

## Setup result

The permanent workspace guidance consists of:

- `AGENTS.md` — standing agent instructions for the repository;
- `NETOHOLICS-HANDOFF.md` — this environment and continuity record.

Setup performed no deployment, service restart, live website or node configuration edit, database change, Git commit, push, merge, branch deletion, worktree deletion, credential transfer, or private-key transfer. The only installed component was the user-scoped Codex connector described below.

On 2026-11-13 the Netoholics WordPress connector 0.3.0 was installed for VS Code Remote SSH with separate `netoholics_wordpress_dev` and `netoholics_wordpress_prod` profiles. Read-only checks confirmed that both public REST roots and the `netoholics-codex/v1` bridge route are reachable. The protected remote credential files were created empty with mode `0600`; authenticated access remains disabled until Josh fills them locally.

## User action

- Fill the protected Remote SSH credential files at `~/.codex/netoholics-wordpress-credentials.json` and `~/.codex/netoholics-wordpress-dev-credentials.json` without pasting credentials into chat or the repository. They may contain the same identity only if both sites actually accept it.
- Restart the Codex VS Code extension after filling the files, then run each profile's `connection_status` and verify the site URL and authenticated identity before any write.
- These documentation files remain uncommitted in the already-dirty checkout so they can be reviewed and moved to an appropriate clean branch/worktree.
