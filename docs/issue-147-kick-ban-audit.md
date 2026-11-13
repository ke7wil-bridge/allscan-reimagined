# Issue 147 Kick/Ban/Unban audit

This audit is for ASR Beta 8.4.3. “Supported” below means the repository can
truthfully advertise the operation after the changes on
`fix/issue-147-kick-ban-audit`. TGIF's node-wide authenticated subscriber
roster is observation data, not an ASR-controlled connection list.

## Root-cause matrix

| Path | Supported contract | Enforcement and identity | Beta 8.4.3 failure mode | API/UI result after this change |
|---|---|---|---|---|
| Direct DMR | Kick, Ban, Unban when the managed relay evidence heartbeat is fresh | The repo-managed TGIF relay blocks inbound `DMRD` media by 24-bit source ID. Callsign is resolved to exactly one current DMR ID. Kick ends after a 10-second quiet gap; it does **not** disconnect the TGIF subscriber. | Adapter read only `urf_dmr` although the observed roster used `clients`; it edited the snapshot and called the write VERIFIED. Callsign-only policy depended on an ID lookup not guaranteed on every install. Repeated writes could overwrite control fields. | Capability is fail-closed without `dmr-local-media-v1` relay evidence. VERIFIED Kick requires the relay to echo the exact nonce after dropping media. Roster is never mutated. Ambiguous/stale identity is rejected. UI contract says local media block, not upstream disconnect. |
| URF DMR/TGIF | Local-media Kick and ID-backed Ban/Unban only with the same fresh relay evidence | TGIF relay ingress before local MMDVM/URFD forwarding; current roster callsign plus UI-supplied DMR ID | `removed=1` meant a roster match/control write. The helper removed a collector row and returned VERIFIED before observing a dropped packet. Node-wide TGIF presence could remain, correctly, while local media was blocked. | DMR capability probe requires relay-owned evidence. Kick writes a unique token and returns VERIFIED only after the relay reports a matching dropped source. Collector snapshots remain read-only. No upstream disconnect is claimed. |
| URF YSF | Kick/Ban/Unban on the patched URFD runtime | URFD `CClient` callsign/protocol and session IP; `AdminDisconnect`, YSF session suppression, blacklist | API trusted mere readability of a generic capability file. | API validates a fresh regular capability file, the YSF suppression flag, and all admin events. Helper still requires an exact removal result. |
| URF P25 | Same as URF YSF | URFD normalized client callsign/protocol and session IP; P25 suppression and blacklist | Same weak capability gate; numeric-source-to-callsign quality remains dependent on the reflector's client identity. | Advertised only when the patched runtime explicitly reports P25 suppression. Exact acceptance must use a known test identity. |
| URF NXDN | Same as URF YSF | URFD client callsign/protocol and session IP; NXDN suppression and blacklist | Same weak capability gate and identity-quality limitation. | Advertised only when the patched runtime explicitly reports NXDN suppression. |
| URF M17 | Same as URF YSF | Native M17 callsign, protocol and session IP; M17 suppression and blacklist | Same weak capability gate. | Advertised only when the patched runtime explicitly reports M17 suppression. |
| Standalone YSF/P25/NXDN/M17 | Unsupported by this repository unless an adapter supplies backend-owned enforcement evidence | Referenced `asr-native-udp-proxy`; implementation/service is not present in this tree | Fresh manifests and acknowledgments could claim full support without a repo-auditable disconnect or media observation. Installer/lifecycle references were stronger than the shipped implementation. | Hidden unless the adapter publishes `enforcementEvidence: relay-observed-v1`. Reconciliation skips unproved adapters instead of reporting global success. |
| Zello | Ban enforcement exists only in the separately installed Zello bridge patch; Kick is unsupported | Normalized Zello username at audio callback; shared blacklist mount | API advertised Ban/Unban from the existence of `/var/www/html/asr`, and the recent-talker UI offered Ban without checking capability. That is not proof the Zello patch/mount is active. | Per-Zello controls are hidden until a real runtime capability is added. Recent talkers honor `banClient`. No Zello Kick is shown. |

## Contract and persistence notes

- Global rules and timed metadata remain the logical authority. A matching
  policy digest is necessary but no longer sufficient for standalone
  capability: the enforcement process must own the evidence.
- Direct/URF DMR bans are effective only where an exact callsign-to-DMR-ID
  mapping or relay callsign lookup exists. Numeric IDs are authoritative for
  packet enforcement. An upstream roster row is neither proof of forwarding
  nor proof of rejection.
- The DMR Kick token remains in control state, but a packet after more than ten
  seconds of silence is treated as a new session. Repeating Kick issues a new
  token and therefore requires a new observed drop.
- URFD session suppression is in-memory; the persisted blacklist is the
  restart authority. Unban clears both the rule and active suppression.
- Self-ban behavior was not changed. Existing protected service/probe identity
  checks remain; whether an administrator may ban their own ordinary identity
  is a product decision.
- Global enforcement is intentionally heterogeneous. Unsupported adapters are
  skipped/hidden; they must not turn an acknowledgment into a claim of full
  coverage.

## Netoholics acceptance plan (requires Josh's explicit approval)

1. Review and deploy through the normal ASR release/install path, not by
   editing `/root/netoholics-native-bridge-migration/dmr/tgif-standalone-relay.py`.
   Confirm the installed relay checksum comes from the reviewed release and
   the ASR-managed service owns it.
2. Before any mutation, capture read-only copies of bridge configuration,
   capability manifests, DMR control/evidence files, audit tail, and service
   unit `ExecStart`. Confirm `dmr-local-media-v1` heartbeat is fresh and the
   capability label says local media block.
3. Use a dedicated test DMR ID/callsign, not KF8DRJ or a production user.
   Confirm its TGIF row and local audio first. Kick once during a continuous
   transmission: local bridge audio must stop, evidence must show the exact
   token/ID, and audit must say VERIFIED. The TGIF subscriber roster may remain.
4. Repeat Kick during the same stream. It must generate a different token and
   receive a second observed-drop acknowledgment. Stop transmitting for more
   than ten seconds, key again, and confirm media is allowed as a new session.
5. Apply a 15-minute Ban to the test identity. Confirm numeric ID mapping,
   current media rejection, rejection after a quiet-gap/reconnect attempt, the
   persisted logical rule/timer, and matching policy digest after a controlled
   ASR-managed service restart.
6. Unban. Confirm the rule, timed record, numeric restriction, and active
   suppression are removed, then verify media passes. Repeat after restart.
7. For YSF, P25, NXDN and M17, test only protocols whose fresh capability
   explicitly reports its suppression primitive. Verify remove, reconnect,
   timed Ban, restart reconciliation, and Unban with dedicated identities.
   Unsupported standalone controls must remain absent.
8. Confirm Zello shows recent talkers but no Kick and no Ban action unless a
   future audited Zello runtime capability is present. Preserve audit and
   evidence artifacts for review; roll back through ASR if any step fails.
