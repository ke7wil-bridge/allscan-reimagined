#!/usr/bin/env python3
"""Static regression checks for interactive installer choices."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile


SCRIPT_PATH = Path(__file__).resolve()
INSTALLER_CANDIDATES = [
    SCRIPT_PATH.parents[1] / "install.sh",
    SCRIPT_PATH.parents[2] / "install.sh",
]
INSTALLER = next(
    (candidate for candidate in INSTALLER_CANDIDATES if candidate.is_file()),
    INSTALLER_CANDIDATES[0],
)
CONFIGURE_CANDIDATES = [
    SCRIPT_PATH.parent / "asr-configure.sh",
    SCRIPT_PATH.parents[1] / "scripts/asr-configure.sh",
]
CONFIGURE = next(
    (candidate for candidate in CONFIGURE_CANDIDATES if candidate.is_file()),
    CONFIGURE_CANDIDATES[0],
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    installer = INSTALLER.read_text(encoding="utf-8")
    configure = CONFIGURE.read_text(encoding="utf-8")

    root_check = 'if [ "${EUID:-$(id -u)}" -ne 0 ]; then'
    root_message = (
        "ERROR: Root is required. Run sudo -i, confirm a root@...# prompt, "
        "then run this installer again."
    )
    require(root_check in installer, "installer does not stop immediately when run as non-root")
    require(root_message in installer, "installer root guidance is not plain and copy-ready")
    require("umask 022" in installer, "installer does not set a deterministic secure umask")
    require(
        'detect_callsign "$detected_node" || true' in configure,
        "clean installs without an ASTDB callsign must fall back instead of aborting",
    )
    require(
        '$_SERVER["SCRIPT_NAME"] = "/allscan/index.php";' in configure,
        "CLI stock detection must initialize the canonical allscan.db path",
    )
    require(
        '"urfReflector"' not in configure.split('if [ -s "$CONFIG_FILE" ] && [ "$FORCE" -eq 0 ]; then', 1)[1].split('exit 0', 1)[0],
        "maintenance configuration still reclassifies existing Standard bridges as URF",
    )

    # Exercise the real existing-config path with the Kentucky YSF shape. An
    # upgrade must preserve every bridge field and must not manufacture URF
    # identity from self-owned/managed metadata.
    with tempfile.TemporaryDirectory(prefix="asr-config-preserve-") as raw:
        config_dir = Path(raw) / "etc"
        config_dir.mkdir()
        config_path = config_dir / "config.json"
        fixture = {
            "node": "674982",
            "requireLogin": True,
            "bridges": [{
                "id": "ysf_net", "mode": "ysf", "node": "1002",
                "cardType": "standard", "backendMode": "managed",
                "bridgePermission": "self_owned",
                "ysfGatewayService": "netoholics-native-ysf-gateway.service",
                "mmdvmService": "netoholics-native-ysf-mmdvm.service",
                "analogBridgeService": "netoholics-native-ysf-analog.service",
                "emulatorService": "netoholics-native-ysf-vocoder.service",
            }],
        }
        original = json.dumps(fixture, indent=2) + "\n"
        config_path.write_text(original, encoding="utf-8")
        test_script = Path(raw) / "asr-configure.sh"
        test_source = configure.replace(
            'CONFIG_DIR="/etc/allscan-reimagined"', f'CONFIG_DIR="{config_dir}"', 1,
        ).replace(
            '[ "${EUID:-$(id -u)}" -eq 0 ] || { echo "Run this setup as root." >&2; exit 1; }',
            ': # Root guard covered separately; this fixture writes only below its temporary directory.',
            1,
        )
        test_script.write_text(test_source, encoding="utf-8")
        test_script.chmod(0o755)
        result = subprocess.run(
            [str(test_script), "--non-interactive"],
            text=True,
            capture_output=True,
            env={**os.environ, "STOCK_ALLSCAN_DIR": str(Path(raw) / "unused-stock")},
        )
        require(result.returncode == 0, f"existing-config preservation path failed: {result.stderr}")
        require(config_path.read_text(encoding="utf-8") == original, "upgrade changed existing bridge configuration")
    require(
        "apt-get install -y unzip" in installer,
        "installer must provision the upstream AllScan unzip dependency on clean nodes",
    )
    require(
        "ensure_tgif_python_runtime" in installer
        and "python3-socketio" in installer
        and "python3-websocket" in installer
        and "import socketio, websocket" in installer,
        "installer must provision and verify TGIF authentication dependencies on clean nodes",
    )
    require(
        "apt-get install -y libapache2-mod-php" in installer
        and "php_web_runtime_ok" in installer
        and "ensure_php_web_runtime\nensure_php_security_updates" in installer,
        "installer must verify and provision a web PHP runtime before exposing stock AllScan",
    )
    require(
        "php_has_critical_security_fix" in installer
        and "apt-get install -y --only-upgrade" in installer
        and "ensure_php_security_updates\nensure_tgif_python_runtime\nSTOCK_ALLSCAN_DIR=" in installer
        and "PHP security baseline satisfied" in installer,
        "installer must refresh vulnerable native PHP runtimes before exposing ASR",
    )
    require(
        'probe_name="asr-php-runtime-probe.$$.$RANDOM.php"' in installer
        and 'probe_name=".asr-php-runtime-probe.' not in installer,
        "PHP runtime probe must remain reachable through stock AllScan hidden-file hardening",
    )
    require(
        'if command -v node >/dev/null 2>&1; then' in installer,
        "target validation must not require Node.js on a clean ASL3 node",
    )
    reapply = (SCRIPT_PATH.parents[1] / "scripts" / "asr-reapply.sh").read_text(encoding="utf-8")
    require(
        "ReadWritePaths=/run/allscan-reimagined /var/lib/allscan-reimagined" in reapply,
        "TGIF session collector sandbox must permit its persistent state directory",
    )
    require(
        "allscan-reimagined-tgif-user-session status node" in reapply
        and "allscan-reimagined-tgif-user-session login node" in reapply,
        "web sudo policy must permit only the node-wide TGIF session scope",
    )
    require(
        installer.index(root_check) < installer.index('ASR_VERSION=')
        and installer.index("umask 022") < installer.index('ASR_VERSION='),
        "root and umask preflight must run before installer setup",
    )
    require(
        installer.index("sudo -i") < installer.index("payload is incomplete"),
        "root guidance does not precede the remaining installer preflight",
    )

    required_text = [
        "LOGIN AND PUBLIC MONITORING",
        "ASR and stock AllScan use separate access settings.",
        "Press Enter to keep each interface's current setting.",
        "Require login for ASR /asr/?",
        "Require login for stock /allscan/?",
        "anonymous read-only monitoring",
        'read -r answer < /dev/tty',
        "ASK_WAS_DEFAULT=1",
        "SHARED FAVORITES (REQUIRED)",
        "This required configuration is",
        "applied automatically.",
    ]
    for text in required_text:
        require(text in installer, f"installer prompt behavior is missing: {text}")

    require(
        installer.count("Require login for ASR /asr/?") == 2,
        "ASR login prompt must provide current-state-aware Y/n and y/N forms",
    )
    require(
        installer.count("Require login for stock /allscan/?") == 2,
        "stock login prompt must provide current-state-aware Y/n and y/N forms",
    )
    require(
        "read -r -t" not in installer,
        "installer prompts must not use a short input timeout",
    )
    require(
        "[ -t 0 ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then" in installer,
        "installer must not redirect configuration from /dev/tty without interactive stdin",
    )
    require(
        'requested_asr_login" != "$current_asr_login' in installer,
        "ASR login policy does not preserve an unchanged selection",
    )
    require(
        'requested_stock_permission" -ne "$current_stock_permission' in installer,
        "stock login policy does not preserve an unchanged selection",
    )
    require(
        "Use /etc/allscan/favorites.ini for both interfaces?" not in installer,
        "mandatory shared Favorites setup must not be presented as a choice",
    )
    require(
        "Canonical shared Favorites configuration was declined." not in installer,
        "mandatory shared Favorites setup must not have a declined path",
    )
    require(
        installer.count("scripts/asr-loopback-validate.py") >= 3
        and installer.count("validate_loopback_after_upgrade") == 4,
        "installer must stage, test, and use the loopback endpoint validator",
    )
    require(
        installer.count("--expect json") == 3
        and installer.count("--expect html") == 1,
        "installer endpoint validation must cover the PHP probe, both JSON APIs, and built HTML",
    )
    require(
        "curl -fsS http://127.0.0.1/asr" not in installer,
        "installer still uses redirect-unsafe raw HTTP endpoint validation",
    )

    dmr_condition = 'item.get("cardType") == "dmr_net"'
    dmr_validation = 'validate_command "configured DMR Net live service is active"'
    ysf_condition = 'item.get("cardType") == "ysf_net"'
    ysf_validation = 'validate_command "configured YSF Net live service is active"'
    require(
        installer.count(dmr_validation) == 1
        and installer.index(dmr_condition) < installer.index(dmr_validation),
        "DMR live-service validation is not conditional on a configured DMR Net bridge",
    )
    require(
        installer.count(ysf_validation) == 1
        and installer.index(ysf_condition) < installer.index(ysf_validation),
        "YSF live-service validation is not conditional on a configured YSF Net bridge",
    )
    require(
        'bridge_detected "$id" || return 0' in configure
        and '[ -s "$bridge_file" ] || echo "No bridges detected; bridge cards will be hidden."' in configure,
        "no-bridge configuration no longer completes with bridge cards hidden",
    )

    print("ASR installer prompts self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
