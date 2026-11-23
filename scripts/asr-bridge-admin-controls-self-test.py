#!/usr/bin/env python3
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "asr-api.php").read_text(encoding="utf-8")
SERVER_API = (ROOT / "server/asr-api.php").read_text(encoding="utf-8")
APP = (ROOT / "src/App.tsx").read_text(encoding="utf-8")
LIVE = (ROOT / "src/lib/allscanLive.ts").read_text(encoding="utf-8")
IDENTITY = (ROOT / "src/lib/participantIdentity.ts").read_text(encoding="utf-8")
GLOBAL_BAN = (ROOT / "scripts/asr-global-ban.py").read_text(encoding="utf-8")
ASL = (ROOT / "scripts/asr-asl-ban.py").read_text(encoding="utf-8")
STANDALONE = (ROOT / "scripts/asr-standalone-admin.py").read_text(encoding="utf-8")
REAPPLY = (ROOT / "docker/layered-reapply.sh").read_text(encoding="utf-8")
NATIVE_REAPPLY = (ROOT / "scripts/asr-reapply.sh").read_text(encoding="utf-8")
BUILD_RELEASE = (ROOT / "build-release.sh").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
LIFECYCLE = (ROOT / "scripts/asr-bridge-lifecycle.py").read_text(encoding="utf-8")
DIRECT_DMR_ADMIN = (ROOT / "scripts/asr-dmr-standalone-admin-adapter.py").read_text(encoding="utf-8")
DIRECT_DMR_INSTALL = (ROOT / "scripts/asr-bridge-setup-dmr-install.py").read_text(encoding="utf-8")
DIRECT_DMR_RENDER = (ROOT / "scripts/asr-bridge-setup-dmr-render.py").read_text(encoding="utf-8")
ZELLO_PATH = Path(os.environ.get("ASR_ZELLO_RUNTIME_SOURCE", str(ROOT / "runtime/zello"))) / "asl_zello_bridge/zello.py"
ZELLO = ZELLO_PATH.read_text(encoding="utf-8") if ZELLO_PATH.exists() else ""
ZELLO_PATCH = (ROOT / "scripts/asr-zello-session-patch.py").read_text(encoding="utf-8")
if not ZELLO:
    print("Bridge admin Zello runtime enforcement: NOT TESTED (source unavailable)")
    if os.environ.get("ASR_REQUIRE_ZELLO_ENFORCEMENT_TEST") == "1":
        raise AssertionError("Zello runtime source unavailable; enforcement unverified")
CSS = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "src/styles/asr").glob("*.css")))

def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)

check(API == SERVER_API, "API copies diverged")
check("function asr_bridge_admin_capabilities" in API, "capability contract missing")
check("function asr_global_ban_policy_digest" in API, "standalone global-ban policy digest missing")
check("function asr_global_ban_admin_available" in API
      and "asr_global_ban_admin_available($mode)" in API
      and "if ($globalAdmin) $clientAdmin = array_merge($clientAdmin, ['banClient', 'unbanClient', 'listBans']);" in API,
      "ASR-persisted Ban is not offered independently of native bridge capability")
check("($cap['bridgeId'] ?? '') !== $id" in API and "($cap['healthy'] ?? false) !== true" in API, "standalone manifest identity/health checks missing")
check("is_link($path)" in API and "($stat['mode'] ?? 0) & 0022" in API and "($stat['uid'] ?? -1) !== 0" in API, "standalone manifest trust checks missing")
check("local-media-block-until-reconnect" in API, "local DMR media enforcement contract missing")
check("($cap['kickRequiresCurrentSession'] ?? false) === true" in API and "($cap['kickAllowsImmediateReconnect'] ?? false) === true" in API, "standalone Kick reconnect semantics missing")
check("($cap['banContract'] ?? '') === 'global-timed-v1'" in API and "hash_equals($policyDigest, $appliedDigest)" in API, "standalone global-ban synchronization proof missing")
check("($cap['enforcementEvidence'] ?? '') === 'relay-observed-v1'" in API,
      "standalone controls do not require backend-owned enforcement evidence")
check("['connect', 'disconnect', 'changeDestination']" in API, "bridge lifecycle capabilities missing")
check("'managementScope' => $globalAdmin ? 'asr-global' : 'bridge'" in API, "ASR-global management scope missing")
check("'clientAuthority' => $globalAdmin ? 'asr-global-ban' : 'none'" in API, "global-ban authority scope missing")
check("'banScopeLabel' => $globalAdmin ? 'GLOBAL' : ''" in API, "global ban scope label missing")
check("$clientAdmin = array_merge($clientAdmin, ['banClient', 'unbanClient', 'listBans']);" in API, "ASR Global Ban must not depend on protocol Kick capability")
check("$id === $mode . '_net'" in API
      and "$capabilities = asr_runtime_client_admin($mode, $mode);" in API,
      "historical mode_net cards cannot resolve their protocol capability adapter")
check("dstar" not in GLOBAL_BAN.lower(), "D-Star administration remains in the Global Ban helper")
check("write_zello_kick" in GLOBAL_BAN and 'enforcement="zello-mute-hold-v1"' in GLOBAL_BAN
      and "expiresSeconds=60" in GLOBAL_BAN,
      "ASR-managed one-minute Zello Kick mute is missing")
check("[A-Z0-9_.\\/-]" in API and "[A-Z0-9_./-]" in GLOBAL_BAN, "Zello underscore identity support missing")
check("_ke7wil_zello_is_kicked" in ZELLO_PATCH and "_ke7wil_zello_clear_kick" in ZELLO_PATCH,
      "Zello runtime Kick enforcement or stream-stop cleanup is missing")
check("Recent Zello Talkers" in APP and "managedConnectedCards" in APP, "Zello Manage Clients UI missing")
check("Mute this Zello talker on this bridge for one minute" in APP and "mode.toUpperCase() === 'ZELLO'" in APP,
      "Zello one-minute Kick UI/API route is missing")
check('payload.get("clients"' in DIRECT_DMR_ADMIN,
      "native DMR Kick does not read the node-wide TGIF client snapshot")
capability = re.search(r"\$bridgeControl\s*=.*?;", API, re.DOTALL)
check(capability is not None and "'restart'" not in capability.group(0)
      and "'recover'" not in capability.group(0),
      "unsupported bridge lifecycle capability exposed")
check("adminCapabilities?: BridgeAdminCapabilities" in LIVE, "frontend capability type missing")
check("const canChangeDestination = bridgeCapabilities.includes('changeDestination')" in APP, "destination loading is not capability-driven")
check("const canConnect = bridgeCapabilities.includes('connect')" in APP, "connect rendering is not capability-driven")
check("const canDisconnect = bridgeCapabilities.includes('disconnect')" in APP, "disconnect rendering is not capability-driven")
check("clientAdmin.includes('listBans')" in APP, "Manage Clients is not capability-driven")
check("Ban is global across ASR bridges" in APP, "global-ban scope warning missing")
check("Enforcement is applied by each supported backend" in APP, "partial global-ban enforcement warning missing")
check("Kick affects only the selected current bridge session" in APP, "Kick scope semantics are not explained")
check("does not remove the upstream TGIF subscriber" in APP
      and "DMR Kick stops local forwarding until reconnect" in APP,
      "DMR Kick does not disclose its local-media-only enforcement scope")
check("allscan-bridge-inline-client-actions" in APP and "allscan-bridge-inline-client-actions" in CSS, "inline client actions are missing")
check("kickBridgeConnectedClient" in APP and "kickZelloClient" in LIVE and "kickStandaloneClient" in LIVE, "bridge-aware Kick UI/API routing is missing")
check("standalone-client-kick" in LIVE and "bridgeId" in LIVE and "clientId" in LIVE
      and "kickStandaloneClient(bridgeId, callsign, clientId)" in APP
      and "--client-id" in API and "A current DMR client ID is required." in API
      and "DMR Kick was not verified by the active media relay." in LIVE,
      "standalone DMR Kick is not bound to an exact current client ID")
check("reconcile_standalone_bans" in GLOBAL_BAN and "reconcile-all" in STANDALONE, "global Ban is not reconciled to standalone adapters")
check("Native ASR Global Ban enforcement helper is unavailable." in GLOBAL_BAN, "native bridge must fail closed when policy helper is missing")
check("disconnect-until-reconnect" in STANDALONE and "appliedPolicyDigest" in STANDALONE, "standalone acknowledgment contracts are incomplete")
check("allscan-reimagined-standalone-admin" in REAPPLY, "standalone administration helper is not installed")
check("asr-standalone-admin.py" in NATIVE_REAPPLY and "asr-global-ban.py" in NATIVE_REAPPLY
      and "asr-asl-ban.py" in NATIVE_REAPPLY,
      "native installer does not install bridge administration helpers")
check("allscan-reimagined-standalone-admin kick" in NATIVE_REAPPLY,
      "native sudo policy does not authorize capability-gated standalone Kick")
check("allscan-reimagined-asl-ban list" in NATIVE_REAPPLY
      and "allscan-reimagined-asl-ban ban *" in NATIVE_REAPPLY,
      "native sudo policy does not authorize ASL Ban enforcement")
check('local_callsign = str(payload.get("callsign")' in GLOBAL_BAN,
      "the node's own callsign can still be globally banned")
check("asr-standalone-admin.py" in BUILD_RELEASE and "asr-global-ban.py" in BUILD_RELEASE
      and "asr-asl-ban.py" in BUILD_RELEASE,
      "native release archive omits bridge administration helpers")
check("validate_admin_capabilities" in LIFECYCLE and "requires its administration capability manifest" in LIFECYCLE, "bridge installation does not require administration parity")
check('"kickClient": ready and hold is not None' in DIRECT_DMR_ADMIN
      and '"kickContract": "disconnect-hold-v1"' in DIRECT_DMR_ADMIN
      and '"kickAllowsImmediateReconnect": False' in DIRECT_DMR_ADMIN
      and '"kickRequiresCurrentSession": True' in DIRECT_DMR_ADMIN,
      "direct DMR does not publish the exact current-session Kick contract")
check("os.chmod(runtime, 0o755)" in DIRECT_DMR_ADMIN,
      "direct DMR capability directory is not traversable by the web/API user")
check("($txEvent['direction'] ?? '') === 'incoming'" in API
      and "strtolower((string) ($txEvent['source_protocol'] ?? '')) === $mode" in API
      and "'last_source_user' => $lastSourceUser" in API,
      "standard bridge Last Talker is not gated by inbound protocol evidence")
check('"banClient": ready' in DIRECT_DMR_ADMIN and '"unbanClient": ready' in DIRECT_DMR_ADMIN
      and '"banContract": "global-timed-v1"' in DIRECT_DMR_ADMIN
      and '"appliedPolicyDigest": digest' in DIRECT_DMR_ADMIN,
      "direct DMR does not publish the synchronized global Ban contract")
check("kicked_ids" in DIRECT_DMR_ADMIN and "banned_ids" in DIRECT_DMR_ADMIN
      and "banned_callsigns" in DIRECT_DMR_ADMIN and "banned_prefixes" in DIRECT_DMR_ADMIN,
      "direct DMR Kick/Ban controls are not connected to enforcement state")
check("dmr-admin-enforcement.json" in DIRECT_DMR_ADMIN
      and "client identity is ambiguous" in DIRECT_DMR_ADMIN
      and 'payload.get("clients")' in DIRECT_DMR_ADMIN,
      "direct DMR does not require relay evidence and an exact current identity")
check("asr-dmr-standalone-admin-adapter.py" in DIRECT_DMR_INSTALL
      and "adminService" in DIRECT_DMR_INSTALL
      and "/run/asr-standalone-admin" in DIRECT_DMR_RENDER,
      "direct DMR administration adapter is not lifecycle-managed")
check("Find connected client" in APP and "banClientFilter" in APP, "client search missing")
check("BANNED" in APP and "Unban" in APP, "ban-state workflow missing")
check("read_rules() != expected" in GLOBAL_BAN and "verified=True" in GLOBAL_BAN, "Global Ban write verification missing")
check("unmappedGlobalRules" in ASL and "unmappedAllStarCallsigns" in ASL
      and "desired_ast.update(mapped_ast_nodes)" in ASL
      and "desired_ast.update(desired_ast_calls)" not in ASL
      and 'asl.get("applied") or {}' in GLOBAL_BAN,
      "Global Ban can still claim AllStar enforcement without a callsign-to-node mapping")
check("allscan-ban-admin-tools" in CSS and "@media (max-width:600px)" in CSS, "responsive admin styling missing")
check(".allscan-ban-admin-scope" in CSS and "background:var(--theme-panel)" in CSS and "border:1px solid var(--theme-border)" in CSS, "admin instruction styling is not theme-driven")
check("font-size:15px; line-height:1.15" in CSS, "bridge title typography is too small")
check("font-size:13px; line-height:1.25" in CSS, "bridge row typography is too small")
check("@media (max-width:360px)" in CSS and "flex-wrap:wrap" in CSS, "narrow-phone bridge header protection missing")
check("dropClientChannel" in LIVE, "generic AllStar Drop Client was removed")
check("allscan-drop-client-help" in APP and "named IAX or Web client channel" in APP, "AllStar Drop Client scope regressed")
check("document.body.style.overflow = 'hidden'" in APP and "event.key === 'Escape'" in APP, "Manage Clients modal close/scroll handling missing")
check("PROTECTED_IDENTITIES" in GLOBAL_BAN and "assert_ban_allowed(rule)" in GLOBAL_BAN, "protected service identities are not enforced at the mutation layer")
check("filteredStations" in GLOBAL_BAN and "protected_identities()" in GLOBAL_BAN, "configured service identities are not protected by global Ban")
check("isBannableConnection" in IDENTITY and "identity.callsign || identity.nodeNumber" in IDENTITY, "named service connections are not structurally excluded from management")
check("sortedConnectionRows.filter((row) => row.state !== 'message' && isBannableConnection(row) && !isProtectedBanConnection(row)" in APP, "service connections remain in Manage Kicks & Bans")
check("&& isBannableConnection(row) && !isProtectedBanConnection(row) ? (" in APP, "service connection action menu remains visible")
check("append_audit_event" in GLOBAL_BAN and "--actor" in API, "administrator audit attribution is missing")
check("Recent bridge actions" in APP and "allscan-management-history" in CSS, "bridge administration audit history is not visible")
check("Manage Kicks & Bans" in APP and "managementTab === 'clients'" in APP and "managementTab === 'bans'" in APP, "unified connection management is missing")
check("$zelloActiveFresh" in API and "abs(time() - $zelloUpdated) <= 30" in API,
      "stale Zello producer state can hold TX and downstream RELAY indefinitely")
check("changeAslBan('ban', banDialog.aslKind" in APP
      and "identity?.network === 'allstar'" in APP
      and "'node'" in APP
      and "AllStar / EchoLink native bans" in APP,
      "AllStar/EchoLink Ban still routes through the digital Global Ban ledger")
check("pending[\"echolink\"]" in (ROOT / "scripts/asr-asl-ban.py").read_text(encoding="utf-8")
      and "payload.get(\"applied\", {}).get(\"pending\", {})" in GLOBAL_BAN,
      "pending EchoLink reloads still invalidate an applied AllStar deny")
check('>Manage Clients</button>' not in APP and '>Manage bans</button>' not in APP, "duplicate management buttons remain")
check('allscan-connection-node-cell' in APP and '.allscan-connection-node-cell' in CSS and '.allscan-connection-more:hover' in CSS, "compact row menu beside the node number is missing")
check("formatGlobalBanRemaining" in APP and "formatGlobalBanExpiration" in APP, "ban expiration display is missing")

print("Bridge Admin Controls self-test: ok")
