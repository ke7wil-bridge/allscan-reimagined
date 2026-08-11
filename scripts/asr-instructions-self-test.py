#!/usr/bin/env python3
"""Static regression checks for the consolidated ASR help and Settings links."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS = ROOT / "compat/allscan-v1.01/asr-instructions/index.php"
ADMIN_CSS = ROOT / "compat/allscan-v1.01/css/asr-admin.css"
SETTINGS = ROOT / "compat/allscan-v1.01/asr-settings/index.php"
COMMON = ROOT / "compat/allscan-v1.01/include/common.php"
APP = ROOT / "src/App.tsx"
API = ROOT / "asr-api.php"
if not API.is_file():
    API = ROOT / "server/asr-api.php"
YSF_HELPER = ROOT / "scripts/asr-ysf-bridge-control.py"
REAPPLY = ROOT / "scripts/asr-reapply.sh"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    instructions = INSTRUCTIONS.read_text(encoding="utf-8")
    admin_css = ADMIN_CSS.read_text(encoding="utf-8")
    settings = SETTINGS.read_text(encoding="utf-8")
    common = COMMON.read_text(encoding="utf-8")
    app_is_source = APP.is_file()
    if app_is_source:
        app = APP.read_text(encoding="utf-8")
    else:
        built_assets = sorted((ROOT / "web/assets").glob("index-*.js"))
        require(len(built_assets) == 1, "packaged ASR JavaScript asset is missing or ambiguous")
        app = built_assets[0].read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")
    ysf_helper = YSF_HELPER.read_text(encoding="utf-8")
    reapply = REAPPLY.read_text(encoding="utf-8")

    targets = re.findall(r'<a\s+href="#([a-z0-9-]+)"', instructions)
    section_ids = re.findall(r'<section\s+id="([a-z0-9-]+)"', instructions)
    require(targets, "instructions topic links are missing")
    require(len(targets) == len(set(targets)), "instructions topic links are duplicated")
    require(len(section_ids) == len(set(section_ids)), "instructions section IDs are duplicated")
    require(set(targets) == set(section_ids), "instructions links and section IDs do not match")

    required_topics = {
        "getting-started",
        "dashboard-controls",
        "appearance-access",
        "bridge-cards",
        "bridge-setup",
        "dmr-net-bridge",
        "ysf-net-bridge",
        "next-digital-bridges",
        "lookup-map",
        "updates",
        "rollback",
        "diagnostics",
    }
    require(required_topics.issubset(section_ids), "one or more required help topics are missing")
    require(
        instructions.index("Green — Idle")
        < instructions.index("Amber — Relay")
        < instructions.index("Red — TX Active"),
        "instructions status legend does not use the Green, Amber, Red order",
    )
    expected_status_colors = {
        "is-idle": "#3a8c4a",
        "is-relay": "#b38b24",
        "is-source": "#d16a6a",
    }
    for status_class, color in expected_status_colors.items():
        require(
            re.search(
                rf"\.asr-instructions-status-grid \.{status_class}\s*\{{[^}}]*"
                rf"border-top-color:{re.escape(color)};",
                admin_css,
                re.DOTALL,
            )
            is not None,
            f"instructions {status_class} color does not match the ASR legend",
        )

    require(
        settings.count('name="maintainFriendlyNames"') == 1,
        "Friendly Names checkbox must appear exactly once",
    )
    bridge_start = settings.index('data-settings-section="bridges"')
    bridge_end = settings.index("</fieldset>", bridge_start)
    friendly_position = settings.index('name="maintainFriendlyNames"')
    require(
        bridge_start < friendly_position < bridge_end,
        "Friendly Names checkbox is not inside Bridge Cards",
    )
    require(
        'data-settings-section="friendly-names"' not in settings,
        "standalone Friendly Names section still exists",
    )
    require(
        "asr-instructions/#bridge-cards" in settings
        and "asr-instructions/#bridge-setup" in settings,
        "Settings does not link to detailed bridge help",
    )
    require(
        "define('ASR_MAX_BRIDGES', 16);" in settings,
        "Settings does not support sixteen bridge cards",
    )
    require(
        "if($count > ASR_MAX_BRIDGES)" in settings
        and "Remove extra bridge cards before saving. No settings were saved." in settings,
        "Settings does not clearly reject saves over the bridge-card limit",
    )
    require(
        "min(ASR_MAX_BRIDGES" not in settings,
        "Settings still silently truncates bridge-card data",
    )
    require(
        "D-Star is not supported by ASR. Delete this bridge card before saving." in settings,
        "Settings does not reject unsupported legacy D-Star cards clearly",
    )
    require(
        "add_bridge dstar" not in (ROOT / "scripts/asr-configure.sh").read_text(encoding="utf-8")
        and "'dstar' => ['D-Star Bridge'" not in api
        and "dstar-clients.json" not in common
        and "Linked D-Star Gateways" not in app,
        "Active D-Star support remains in ASR",
    )
    require(
        'name="bridgeFixedRecovery[]"' in settings
        and "fixedBridgeRecovery" in settings
        and "Automatically restore this fixed bridge link if it drops" in settings
        and "Net Bridges are never managed here" in settings,
        "Settings does not provide bounded Standard Bridge recovery controls",
    )
    require(
        "Fixed Bridge Recovery" in instructions
        and "native permanent link" in instructions
        and "Net Bridges are excluded" in instructions,
        "Help does not explain Standard Bridge recovery boundaries",
    )
    require(
        "User=asterisk" in reapply
        and "Group=asterisk" in reapply
        and "SupplementaryGroups=$WEB_GROUP" in reapply,
        "Fixed-bridge recovery does not run as the restricted Asterisk account",
    )
    for requirement in (
        "YSF Net Bridge",
        'name="bridgeMode[]"',
        'value="ysf"',
        'name="bridgeYsfGatewayConfig[]"',
        'name="bridgeMmdvmConfig[]"',
        'name="bridgeYsfGatewayService[]"',
        'name="bridgeMmdvmService[]"',
        'name="bridgeAllowTune[]"',
        'name="bridgeYsfCustomReflectors[]"',
        "exact reflector name or five-digit ID",
        "Import YSFHosts.txt",
        "https://hostfiles.refcheck.radio/",
        "YSF Plain Text",
        "Re-import after RefCheck adds a reflector",
        "systemctl start allscan-reimagined-reapply.service",
    ):
        require(requirement in settings, f"YSF Net Bridge Settings support is missing: {requirement}")
    for requirement in (
        "Choose the digital mode from Bridge ID",
        'value="p25"',
        'value="nxdn"',
        'value="m17"',
        'name="bridgePermission[]"',
        'name="bridgeApprovedDestinations[]"',
        'name="bridgeGatewayConfig[]"',
        'name="bridgeMmdvmMqttName[]"',
        'name="bridgeM17Callsign[]"',
        "A directory listing is not bridge permission",
        "authenticated local MQTT",
        "Changing the Bridge ID dropdown can intentionally regenerate",
        "$nextSecrets['bridgeClientPasswords'][$newSecretId]",
        "$rawYsfGatewayService",
        "$rawDigitalMmdvmService",
    ):
        require(requirement in settings, f"next-mode bridge Settings support is missing: {requirement}")
    for requirement in (
        "ASR_P25_BRIDGE_CONTROL_HELPER",
        "ASR_NXDN_BRIDGE_CONTROL_HELPER",
        "ASR_M17_BRIDGE_CONTROL_HELPER",
        "function asr_next_mode_connect",
        "function asr_next_mode_disconnect",
    ):
        require(requirement in api, f"next-mode bridge API support is missing: {requirement}")
    if app_is_source:
        require(
            "card.cardType === 'm17_net' ? 22_000 : 7_000" in app,
            "M17 dashboard confirmation does not allow its bounded ACK and AllStar-link window",
        )
    for requirement in (
        "allscan-reimagined-p25-bridge-control",
        "allscan-reimagined-nxdn-bridge-control",
        "allscan-reimagined-m17-bridge-control",
        "allscan-reimagined-m17-usrp-connector",
        "allscan-reimagined-m17-bridge@.service",
        "bridge-mqtt-secrets.json",
    ):
        require(requirement in reapply, f"next-mode install wiring is missing: {requirement}")
    for requirement in (
        "asr-bridge-drag-handle",
        "asr-bridge-move-up",
        "asr-bridge-move-down",
        "function moveBridgeRow",
        "function updateBridgeOrderControls",
        "table.addEventListener('dragstart'",
        "table.addEventListener('dragover'",
        'aria-live="polite"',
        "Save Reimagined Settings to keep the new order.",
    ):
        require(requirement in settings, f"bridge ordering support is missing: {requirement}")
    for requirement in (
        ".asr-bridge-panel-actions",
        ".asr-bridge-drag-handle",
        ".asr-bridge-settings-row.is-dragging",
        "grid-template-columns:repeat(4, minmax(0, 1fr));",
        ".asr-visually-hidden",
        ".asr-ysf-custom-reflectors",
    ):
        require(requirement in admin_css, f"bridge ordering styling is missing: {requirement}")
    app_requirements = [
        "Reflector name or ID",
    ]
    if app_is_source:
        app_requirements.extend([
            "maxLength={80}",
            "[card.id]: event.target.value",
            "result.currentDestination",
        ])
    for requirement in app_requirements:
        require(requirement in app, f"name-or-ID YSF reflector UI is missing: {requirement}")
    require("ysf-net-destinations" not in app, "YSF reflector dropdown suggestions are still present")
    for requirement in (
        "function asr_ysf_net_resolve_destination",
        "More than one reflector uses that name",
        "currentDestinationLabel",
        "No valid YSF reflector list is installed",
        "Import a current YSFHosts.txt list",
    ):
        require(requirement in api, f"YSF name resolution is missing: {requirement}")
    for requirement in (
        "CUSTOM_HOSTS_DIR",
        "def validate_custom_reflectors",
        "def merged_hosts_content",
        "def import_hosts",
        "def catalog_status",
        "--import-hosts",
        "--catalog-status",
        "--sync-custom-hosts",
    ):
        require(requirement in ysf_helper, f"persistent custom YSF support is missing: {requirement}")
    require(
        "allscan-reimagined-ysf-bridge-control --sync-custom-hosts" in reapply
        and "/var/lib/allscan-reimagined/ysf-hosts" in reapply,
        "reapply does not synchronize the managed custom YSF catalog",
    )
    require(
        "--refresh-public-hosts" not in ysf_helper
        and "hostfiles.refcheck.radio/YSFHosts.txt" not in ysf_helper
        and "cat > /etc/systemd/system/allscan-reimagined-ysf-hosts-refresh" not in reapply
        and "ExecStart=/usr/local/sbin/allscan-reimagined-ysf-bridge-control --refresh" not in reapply,
        "obsolete automatic YSF hostfile refresh wiring is still present",
    )
    for requirement in (
        "Install a reflector list.",
        "The list is a snapshot.",
        "A rejected upload never erases the previous valid list.",
        "YSF Plain Text",
    ):
        require(requirement in instructions, f"YSF reflector import help is missing: {requirement}")
    require(
        re.search(r"\.asr-reimagined-settings-form\s*\{[^}]*font-size:15px;", admin_css, re.DOTALL)
        is not None,
        "Reimagined Settings base font increase is missing",
    )
    require(
        re.search(r"\.asr-bridge-settings-row label span\s*\{[^}]*font-size:13px;", admin_css, re.DOTALL)
        is not None,
        "bridge setup label font increase is missing",
    )
    require(
        'data-settings-section="rollback"' in settings,
        "rollback section was removed",
    )
    rollback_requirements = [
        "Keep this page open after starting the rollback.",
        "Wait for the <strong>Rollback Completed</strong> confirmation",
        'id="asrRollbackProgress"',
        'id="asrRollbackProgressTitle">ROLLBACK IN PROGRESS — DO NOT LEAVE THIS PAGE',
        'id="asrRollbackProgressMessage"',
        "setRollbackProgress('running', 'ROLLBACK IN PROGRESS — DO NOT LEAVE THIS PAGE'",
        'id="asrRollbackCompleteDialog"',
        'role="alertdialog"',
        'id="asrRollbackCompleteTitle">Rollback Completed',
        'id="asrRollbackCompleteOk"',
        "showRollbackCompleteDialog();",
        "window.addEventListener('beforeunload'",
        "window.location.assign(asrBase + '/');",
    ]
    for requirement in rollback_requirements:
        require(requirement in settings, f"rollback completion guidance is missing: {requirement}")
    require(
        "window.setTimeout(function () { window.location.assign(asrBase + '/'); }, 1200);"
        not in settings,
        "rollback still redirects before the completion confirmation",
    )
    require(
        ".asr-rollback-complete-card" in admin_css
        and ".asr-rollback-complete-button" in admin_css,
        "rollback completion dialog styling is missing",
    )
    require(
        re.search(r"\.asr-rollback-progress\s*\{[^}]*position:sticky;[^}]*border:3px solid #ff6b6b;[^}]*font-size:17px;", admin_css, re.DOTALL)
        is not None
        and '.asr-rollback-progress[data-state="succeeded"]' in admin_css,
        "prominent rollback progress styling is missing",
    )
    require(
        "function asrRebaseLegacyWebPath" in common
        and "$runtime['headerLogo'] = asrRebaseLegacyWebPath" in common,
        "shared admin logo rebasing is missing",
    )
    require(
        common.count("Help & Instructions") == 2,
        "shared admin menus do not use the Help & Instructions label",
    )

    print("ASR instructions and Settings self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
