# Bridge Live Timing Qualification — 2026-10-04

LOCKED known-good live behavior after RF qualification.

- Zello outbound Relay: USRP false/unkey requires 150 ms confirmation. A following keyed voice frame cancels the pending unkey. This absorbs the observed ~0.1 s false cycle without frontend masking.
- YSF Net inbound: network source evidence is Source/TX even when the RF/user callsign matches the node's configured callsign. Do not classify a same-callsign inbound network source as local Relay.
- Net Bridge status precedence: direct inbound/source evidence must display Source/TX without a preliminary Relay flash. Direct protocol timing owns key/unkey; slower collectors enrich metadata only.
- YSF direct TX/EOT feed remains authoritative for live timing. Stale/out-of-order frontend responses must not overwrite newer state.
- Live RF qualification result: Zello, YSF Net Bridge, Source/TX, Relay, EOT/Idle, rapid transitions and bridge timing PASS.

Do not change these timing/direction semantics without repeating live RF qualification.
