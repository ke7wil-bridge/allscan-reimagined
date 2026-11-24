#!/usr/bin/env python3
"""Narrow root helper for Texas link controls; never accepts shell text."""

from __future__ import annotations

import re
import subprocess
import sys


LOCAL_NODE = "674984"


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print("invalid arguments", file=sys.stderr)
        return 2
    _, action, local, remote, permanence = argv
    if (action not in {"connect", "disconnect"} or local != LOCAL_NODE
            or not re.fullmatch(r"[0-9]{3,10}", remote) or remote == local
            or permanence not in {"temporary", "permanent"}
            or (action == "disconnect" and permanence != "temporary")):
        print("control request rejected", file=sys.stderr)
        return 2
    ilink = "13" if action == "connect" and permanence == "permanent" else (
        "3" if action == "connect" else "11")
    result = subprocess.run(
        ["/usr/sbin/asterisk", "-rx", f"rpt cmd {local} ilink {ilink} {remote}"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    output = (result.stdout or result.stderr).strip()[:300]
    if output:
        print(output)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
