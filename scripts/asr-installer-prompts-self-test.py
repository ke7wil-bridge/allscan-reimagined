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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    installer = INSTALLER.read_text(encoding="utf-8")

    required_text = [
        "LOGIN AND PUBLIC MONITORING",
        "ASR and stock AllScan use separate access settings.",
        "Press Enter to keep each interface's current setting.",
        "Require login for ASR /asr/?",
        "Require login for stock /allscan/?",
        "anonymous read-only monitoring",
        'read -r answer < /dev/tty',
        "ASK_WAS_DEFAULT=1",
        "Use /etc/allscan/favorites.ini for both interfaces?",
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

    print("ASR installer prompts self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
