# Scoped ASR Plus dashboard tokens — issue #252

This is a development package. Do not install it on Kentucky, Georgia, Phoenix,
or Texas without a separate reviewed deployment request and live-action
approval.

## Security model

Bearer tokens have three independent limits: expiration, hub scope, and
permission scope. New tokens default to read-only. The bearer value is shown
once; the store contains only its SHA-256 digest. Revocation takes effect on
the next request. Authentication successes and failures are appended to a
root-controlled JSONL audit without recording the bearer value.

Permissions are:

- `status:read`
- `control:connect`
- `control:disconnect`
- `control:ban`
- `control:unban`

Known hub scopes are `kentucky` (674982), `phoenix` (641894), `georgia`
(674980), and `texas` (674984). The token API does not expose ban-list/result
queue operations; those remain human-session functions pending queue-integrity
hardening. A token cannot modify configuration, restart services, update
software, or access a shell.

## Operator lifecycle

After an approved installation, a root operator creates a Texas read-only
review token with:

```text
sudo /usr/local/sbin/asrplus-token create \
  --label "KJ5IRQ Texas review" \
  --nodes texas \
  --expires-in 86400
```

To add narrowly selected controls, explicitly enumerate them:

```text
sudo /usr/local/sbin/asrplus-token create \
  --label "KJ5IRQ Texas link control" \
  --nodes texas \
  --permissions status:read,control:connect,control:disconnect \
  --expires-in 3600
```

Copy the returned bearer value directly into the approved secret manager. Do
not paste it into chat, an issue, a shell history, repository files, or logs.
The token ID is non-secret and may be used for inventory and revocation:

```text
sudo /usr/local/sbin/asrplus-token list
sudo /usr/local/sbin/asrplus-token revoke TOKEN_ID
```

## API use

Tokens are accepted only over HTTPS and require an explicit `hub`. Status is:

```text
GET /asrplus/api.php?hub=texas
Authorization: Bearer [secret]
```

Control requests are JSON POSTs. For example, a token with
`control:disconnect` may submit:

```json
{"hub":"texas","action":"disconnect","node":"12345","permanent":false}
```

The response reports acceptance/queueing, not proof that a radio link changed.
Operational verification must use fresh status evidence and the command result.

## Files and installation boundary

- `scripts/asrplus-token.py`: root token lifecycle and fixed authentication
  entry point.
- `ops/asrplus/api.php`: session-or-bearer API with node filtering and
  per-operation authorization.
- `ops/asrplus/asrplus-token.sudoers`: permits Apache to call only
  `asrplus-token authenticate`; create/list/revoke are not permitted.
- `scripts/asrplus-token-self-test.py`: positive and negative security tests.

An approved installer must create `/var/lib/asrplus` mode `0700`, install the
token helper root-owned mode `0755`, install the store root-owned mode `0600`,
install sudoers mode `0440` after `visudo -cf`, and preserve/rotate the audit at
`/var/log/asrplus-token-audit.jsonl` mode `0640`. It must also verify that
Apache receives `Authorization` and that HTTPS termination sets the server-side
HTTPS state; the implementation intentionally does not trust an arbitrary
client-supplied `X-Forwarded-Proto` header.

## Required deployment validation

Before any live enablement, repeat the automated tests and prove all of these
against a non-production fixture: missing/altered/expired/revoked tokens fail;
cross-hub tokens fail; read-only tokens cannot control; each granted control
works only on its authorized hub; HTTP is rejected; secrets never appear in
the store/audit; revocation is immediate; and cookie-session behavior remains
unchanged. Texas control remains disabled until the #253 queue-integrity
hardening is deployed in required mode and the #254 operator agreement is
complete. API tokens remain central on Kentucky; remote hubs receive only
their own command-authentication key and never store dashboard bearer tokens.
