#!/usr/bin/env python3
"""Read-only fixture helper for the accepted stock AllScan topology count.

The count is the unique union of LinkedNodes and visible direct rows. This
helper never discovers, edits, or restarts stock AllScan; it only reads an
explicit JSON file (or stdin) and writes a count to stdout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


MAX_INPUT_BYTES = 4 * 1024 * 1024
NODE_FIELDS = (
    "node",
    "Node",
    "nodeNumber",
    "node_number",
    "NodeNumber",
    "id",
)


def normalize_node(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str):
        candidate = value.strip()
        if re.fullmatch(r"[0-9]+", candidate):
            return str(int(candidate))
        return None
    if isinstance(value, dict):
        for field in NODE_FIELDS:
            if field in value:
                return normalize_node(value[field])
    return None


def collection_entries(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part for part in re.split(r"[\s,]+", value.strip()) if part)
    if isinstance(value, (list, tuple, set)):
        return value
    if isinstance(value, dict):
        if any(field in value for field in NODE_FIELDS):
            return (value,)
        entries: list[Any] = []
        for key, child in value.items():
            entries.append(child if normalize_node(child) is not None else key)
        return entries
    return (value,)


def row_is_visible(row: Any) -> bool:
    if not isinstance(row, dict):
        return True
    if row.get("hidden") is True or row.get("stale") is True:
        return False
    if "visible" in row:
        return row.get("visible") is True
    return True


def accepted_topology(payload: dict[str, Any]) -> list[str]:
    nodes: set[str] = set()
    linked = payload.get("LinkedNodes", payload.get("linkedNodes"))
    for entry in collection_entries(linked):
        node = normalize_node(entry)
        if node is not None:
            nodes.add(node)

    if "visibleDirectRows" in payload:
        direct = payload["visibleDirectRows"]
    else:
        direct = payload.get("directRows", ())
    for row in collection_entries(direct):
        if not row_is_visible(row):
            continue
        node = normalize_node(row)
        if node is not None:
            nodes.add(node)
    return sorted(nodes, key=lambda item: (int(item), item))


def count_payload(payload: dict[str, Any]) -> dict[str, Any]:
    nodes = accepted_topology(payload)
    return {
        "ok": True,
        "connectedCount": len(nodes),
        "label": "total linked",
        "nodes": nodes,
    }


def read_payload(source: str) -> dict[str, Any]:
    if source == "-":
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        with Path(source).open("rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds the 4 MiB safety limit")
    decoded = json.loads(raw.decode("utf-8", errors="strict"))
    if not isinstance(decoded, dict):
        raise ValueError("input must be a JSON object")
    return decoded


def self_test() -> None:
    fixture = {
        "LinkedNodes": [2000, "2300", {"node": "3000"}, "02300"],
        "directRows": [
            {"node": 2300, "visible": True},
            {"nodeNumber": "4000"},
            {"node": 5000, "visible": False},
            {"node": 6000, "stale": True},
            {"node": "not-a-node", "visible": True},
        ],
    }
    result = count_payload(fixture)
    assert result == {
        "ok": True,
        "connectedCount": 4,
        "label": "total linked",
        "nodes": ["2000", "2300", "3000", "4000"],
    }

    mapping_fixture = {
        "linkedNodes": {"641890": {"Node": "641890"}, "674982": {}},
        "visibleDirectRows": "674982, 63916 641890",
    }
    assert accepted_topology(mapping_fixture) == ["63916", "641890", "674982"]

    # Exercise an unlimited list rather than silently capping client rows.
    unlimited_fixture = {
        "LinkedNodes": list(range(2000, 3500)),
        "directRows": [
            {"node": node, "visible": True} for node in range(3000, 4000)
        ],
    }
    unlimited = count_payload(unlimited_fixture)
    assert unlimited["connectedCount"] == 2000
    assert unlimited["nodes"][0] == "2000"
    assert unlimited["nodes"][-1] == "3999"
    print("stock AllScan topology count self-test: ok")


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", metavar="PATH", help="Explicit JSON fixture path, or - for stdin"
    )
    parser.add_argument("--json", action="store_true", help="Write detailed JSON")
    parser.add_argument(
        "--self-test", action="store_true", help="Run fixture-driven tests"
    )
    args = parser.parse_args(arguments)
    if args.self_test:
        if args.input:
            parser.error("--self-test cannot be combined with --input")
        self_test()
        return 0
    if not args.input:
        parser.error("--input is required unless --self-test is used")
    try:
        result = count_payload(read_payload(args.input))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    else:
        print(result["connectedCount"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
