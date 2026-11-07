#!/usr/bin/env python3
"""Pure alert lifecycle reducer for ASR diagnostics (#126). No email or services."""
from __future__ import annotations
import hashlib
import re

SEVERITIES = {"info", "warning", "critical"}
SAFE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


def update(previous: dict, observations: list[dict], now: int, threshold: int = 2) -> dict:
    if threshold < 1 or now < 0:
        raise ValueError("invalid alert parameters")
    prior = previous.get("alerts", {}) if isinstance(previous, dict) else {}
    if not isinstance(prior, dict):
        prior = {}
    observed = {}
    for item in observations:
        key = item.get("id")
        severity = item.get("severity")
        if not isinstance(key, str) or not SAFE_ID.fullmatch(key) or severity not in SEVERITIES:
            raise ValueError("invalid alert identity or severity")
        if key in observed:
            raise ValueError("duplicate alert identity")
        observed[key] = {"id": key, "severity": severity, "component": str(item.get("component", ""))[:80],
                         "message": str(item.get("message", ""))[:160]}
    result = {}
    events = []
    for key in sorted(set(prior) | set(observed)):
        old = prior.get(key, {})
        if not isinstance(old, dict):
            old = {}
        if key in observed:
            entry = observed[key]
            count = int(old.get("consecutive", 0)) + 1
            active = count >= threshold
            first = int(old.get("firstSeenEpoch", now))
            result[key] = {**entry, "firstSeenEpoch": first, "lastSeenEpoch": now,
                           "consecutive": count, "active": active}
            if active and not old.get("active", False):
                events.append({"id": key, "event": "activated", "epoch": now})
            elif active and old.get("active", False) and old.get("severity") != entry["severity"]:
                events.append({"id": key, "event": "severity-changed", "epoch": now})
        elif old.get("active"):
            events.append({"id": key, "event": "resolved", "epoch": now})
    return {"schema": 1, "updatedEpoch": now, "alerts": result, "events": events,
            "active": [result[key] for key in sorted(result) if result[key]["active"]]}


def resource_observations(warnings: list[str], sample: dict) -> list[dict]:
    mapping = {
        "Available RAM is low.": ("resource.low_ram", "warning"),
        "Swap use is rising across repeated samples.": ("resource.swap_rising", "warning"),
    }
    result = []
    for message in warnings:
        if message not in mapping:
            continue
        key, severity = mapping[message]
        if key == "resource.low_ram" and int(sample.get("memAvailableKb", 0)) < 128 * 1024:
            severity = "critical"
        result.append({"id": key, "severity": severity, "component": "node-resources", "message": message})
    return result
