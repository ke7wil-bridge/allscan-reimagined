#!/usr/bin/env python3
"""Static regression checks for interactive installer choices."""

from __future__ import annotations

from pathlib import Path


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
        installer.count("scripts/asr-loopback-validate.py") >= 5,
        "installer must stage, test, and use the loopback endpoint validator",
    )
    require(
        installer.count("--expect json") == 2
        and installer.count("--expect html") == 1,
        "installer endpoint validation must cover both JSON APIs and built HTML",
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
