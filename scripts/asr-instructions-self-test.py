#!/usr/bin/env python3
"""Static regression checks for the consolidated ASR help and Settings links."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS = ROOT / "compat/allscan-v1.01/asr-instructions/index.php"
SETTINGS = ROOT / "compat/allscan-v1.01/asr-settings/index.php"
COMMON = ROOT / "compat/allscan-v1.01/include/common.php"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    instructions = INSTRUCTIONS.read_text(encoding="utf-8")
    settings = SETTINGS.read_text(encoding="utf-8")
    common = COMMON.read_text(encoding="utf-8")

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
        "lookup-map",
        "updates",
        "rollback",
        "diagnostics",
    }
    require(required_topics.issubset(section_ids), "one or more required help topics are missing")

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
        'data-settings-section="rollback"' in settings,
        "rollback section was removed",
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
