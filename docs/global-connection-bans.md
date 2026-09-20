# Global Connection Bans (feature branch)

The Connection Status table keeps its six columns. Eligible AllStar, EchoLink and
named IAX rows show a compact three-dot menu in the Mode cell. Ban opens the same
dialog for active and manually entered identities; the Ban Management dialog
lists global callsign rules, ASR-owned network rules and read-only external
Asterisk restrictions. Digital bridge client and recent-talker Ban buttons open the same dialog.
Their existing Kick controls remain available.

## Enforcement

- ASR's existing URF-backed global blacklist remains the authority for exact
  digital callsign bans and durations. The global ban controller calls
  `asr-asl-ban sync` after list, ban, unban and timed expiry.
- `asr-asl-ban.py` uses the existing AMI connection to maintain native Asterisk
  `denylist/<local-node>/<node-or-call>` entries. The stock `radio-secure`
  dialplan checks node numbers; `allstar-public` checks authenticated callsigns.
- EchoLink restrictions use the native `deny` directive in `echolink.conf`,
  managed through AMI GetConfig/UpdateConfig and module reload. Manual tokens
  present before an ASR ban remain external. A failed write/reload is returned
  as a pending error and retried during subsequent reconciliation.
- An ASR-owned ledger in the persistent URF configuration mount stores scoped
  node/callsign rules, expirations, reasons, actors, an action history and which native restrictions
  ASR created. Reconciliation expires scoped bans and calculates effective
  restrictions from the union of scoped rules and exact global callsigns.
- Prefix rules and identities that cannot be represented as callsigns are
  reported as unmapped for AllStar/EchoLink. Digital usernames without a
  confirmed callsign remain digital-only rules. A global callsign rule blocks
  authenticated AllStar callsign connections. To block a normal AllStar IAX
  node, choose the node number as well. The combined option creates both.
- Matching live IAX and EchoLink channels are removed after the native ban
  is installed. The originating Connection Status row also uses its existing
  Disconnect/Drop Client action as a fallback. ASR reports partial failures.

## Safety and deployment

Only authenticated administrators can call the new same-origin POST endpoint.
The PHP endpoint validates every argument and invokes a narrow sudo helper.
The helper also validates targets and rejects local/internal nodes and known
service identities. Native restrictions written through the Linux menu are not
deleted by ASR's unban/expiry. All state lives on existing persistent mounts.

Docker installs the helper through `docker/layered-reapply.sh`. The existing
15-second global expiry loop also reconciles scoped AllStar/EchoLink rules.
No live Asterisk configuration or database state is changed by building this
branch. Verify AMI UpdateConfig/reload and connection rejection on a controlled
test identity before declaring live enforcement verified.

## Checks

- `python3 scripts/asr-asl-ban-self-test.py` covers overlapping rules,
  expiry, preservation of manual entries, targeted channel removal and input
  validation with a fake AMI.
- `npm run build` and `npm run lint` check the frontend.
- PHP syntax can be checked with `docker exec -i
  allscan-reimagined-layered-allscan-1 php -l < server/asr-api.php`.
- `python3 scripts/asr-bridge-admin-controls-self-test.py` and
  `python3 scripts/asr-dstar-zello-client-management-self-test.py`
  cover existing bridge administration.
