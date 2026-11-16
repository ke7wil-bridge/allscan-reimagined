# Netoholics / AllScan Reimagined agent instructions

## Scope

These instructions apply to the entire repository. They are the standing safety and workflow rules for Codex and other coding agents working on Netoholics or AllScan Reimagined from this checkout. Read `NETOHOLICS-HANDOFF.md` before node-related work. More specific `AGENTS.md` files may add constraints for their subdirectories but must not weaken these safeguards.

## Start every task safely

- Read the user request, this file, `NETOHOLICS-HANDOFF.md`, and relevant repository documentation before changing anything.
- Run `git status --short --branch` and `git worktree list` first. Treat all existing edits and untracked files as user work.
- Never reset, clean, stash, discard, overwrite, reformat, or commit unrelated changes. Do not switch branches in a dirty shared checkout.
- Prefer a dedicated issue branch and worktree for implementation. Use a descriptive branch such as `fix/issue-184-three-node-reporting` and a worktree under `/home/josh/worktrees/`. Do not reuse or remove another task's worktree.
- Keep the change set narrow. If existing edits overlap the requested files, stop and report the conflict before editing.

## Systems and environment boundaries

| Name | Address | Hostname | Role |
| --- | --- | --- | --- |
| Kentucky | `44.30.157.21` | `nth-serv-p01` | Netoholics production VPS |
| Georgia | `45.76.255.39` | `node674980` | Legacy Netoholics node |

### WordPress environments

| MCP profile | Site | Role |
| --- | --- | --- |
| `netoholics_wordpress_dev` | `https://dev.netoholicsnetwork.net` | Development and experimentation |
| `netoholics_wordpress_prod` | `https://netoholicsnetwork.net` | Production website |

- Use the development profile by default for experiments, prototypes, and risky website work. Begin with its `connection_status` and verify `site_profile: development` and the exact development URL.
- Use the production profile only when the user explicitly identifies production or requests a production comparison or change. Begin with its `connection_status` and verify `site_profile: production` and the exact production URL.
- Never reuse object IDs, hashes, ETags, revisions, backups, or journal entries across sites. Moving tested work from development to production is a new task with a fresh production read, preview, authorization, backup, and verification.
- Use the focused WordPress connector instead of wp-admin, generic REST calls, database access, or ad hoc PHP when an available connector tool covers the task.
- Website credentials live only in protected user files under `~/.codex/`, never in this repository. Do not read, print, copy, commit, or log them.

- `kentucky` and `georgia` are SSH aliases configured and verified on Josh's Mac only. Do not assume those aliases exist on the Linux development machine.
- The normal development workspace is `/home/josh/repos/allscan-reimagined` on the Linux development machine reached through VS Code Remote SSH.
- Treat Kentucky as production. Treat Georgia as legacy but still operational unless the user explicitly states otherwise.
- Read-only diagnostics are allowed when needed for the task. Do not deploy, restart or stop services, edit live configuration, install packages, change firewall/network rules, alter databases, rotate keys, or run load/traffic tests without the user's explicit confirmation immediately before the production action.
- Administrative access does not remove the confirmation requirement. Before an approved live change, state the exact host, command or effect, expected impact, backup/rollback plan, and verification.
- Development-site writes still require a clear user request and the connector's guarded confirmation phrase. Production website writes additionally require explicit production confirmation immediately before execution.
- Never copy private keys between the Mac, development machine, repository, or nodes. Use only existing SSH configuration and credentials. Never print, paste, commit, or record passwords, tokens, private keys, node databases, or secret-bearing configuration.
- Repository documentation may name SSH aliases, public IP addresses, hostnames, and key-free verification results; it must not contain private key material or credentials.

## Git, GitHub, and worktrees

- The canonical remote is `https://github.com/ke7wil-bridge/allscan-reimagined.git`. Verify the current remote rather than assuming it has not changed.
- Fetching and inspecting remote refs is safe. Do not force-push, rewrite shared history, delete branches/tags/worktrees, merge to a protected or release branch, publish a release, or close an issue without explicit user authorization.
- Tie implementation work to its GitHub issue when one exists. Put acceptance evidence in the pull request or issue; do not claim deployment or acceptance from code completion alone.
- Keep production deployment separate from repository implementation. A merged change is not proof that a node was updated. Record the deployed commit/version and live verification only after they are observed.
- Do not add generated release archives, backups, runtime state, node-specific configuration, or credentials to Git.

## Validation and review

- Choose checks proportional to the files changed. For web code, the standard scripts include `pnpm lint`, `pnpm build`, `pnpm test:support`, and `pnpm test:support:browser`; run only the relevant checks and report any that were not run.
- Use existing repository self-tests for installer, bridge, runtime, or node work. Never run a self-test against production unless its behavior has been reviewed and the user has approved any possible service impact.
- Review the final diff and `git status` before handing off. Distinguish pre-existing changes from changes made in the current task.
- Do not weaken security, authorization, backup, rollback, or integrity checks merely to make a test pass.

## Handoff requirements

For every completed task, report:

1. files changed and why;
2. branch/worktree used;
3. validations run and results;
4. any live systems contacted and whether the activity was read-only;
5. deployment status (normally not deployed unless separately approved);
6. remaining risks, blockers, or user action.

Update `NETOHOLICS-HANDOFF.md` when durable environment facts, host roles, access assumptions, or workflow safeguards change. Do not use it as a running log of temporary command output.
