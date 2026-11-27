#!/usr/bin/env python3
"""#319: lossless exact Zello-name policy storage; no production access."""
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("policy", ROOT / "scripts/asr-global-ban.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    policy.CONFIG = str(root / "missing-config.json")
    policy.BLACKLIST = str(root / "global-bans.list")
    policy.TIMED_BANS = str(root / "asr-timed-bans.json")
    policy.ZELLO_EXACT_BANS = str(root / "asr-zello-exact-bans.json")
    policy.write_rules(["TAURUS", "N0*"])
    policy.write_timed_bans({})
    radio_before = Path(policy.BLACKLIST).read_bytes(), Path(policy.TIMED_BANS).read_bytes()
    assert policy.read_zello_exact_bans() == {}
    rules = policy.change_zello_exact_ban({}, "ban", "Taurus 78", "15m", 1000)
    rules = policy.change_zello_exact_ban(rules, "ban", "Taurus 79", "permanent", 1000)
    rules = policy.change_zello_exact_ban(rules, "ban", "@NET OP", "1h", 1000)
    policy.write_zello_exact_bans(rules)
    assert policy.read_zello_exact_bans() == rules
    assert set(rules) == {"TAURUS 78", "TAURUS 79", "@NET OP"}
    assert "TAURUS" not in rules
    assert set(policy.active_zello_exact_bans(rules, 1900)) == {"TAURUS 79", "@NET OP"}
    rules = policy.change_zello_exact_ban(rules, "unban", "taurus 79")
    policy.write_zello_exact_bans(rules)
    assert "TAURUS 79" not in policy.read_zello_exact_bans()
    assert radio_before == (Path(policy.BLACKLIST).read_bytes(), Path(policy.TIMED_BANS).read_bytes())
    for target in ("TAURUS", "N0*", "", "A\nB", "A;B", "A" * 49):
        try:
            policy.change_zello_exact_ban(rules, "ban", target)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid or legacy radio-rule identity accepted: " + repr(target))
    Path(policy.ZELLO_EXACT_BANS).write_text(json.dumps({"schema": 1, "rules": {"TAURUS 78": {"createdAt": 1000, "expiresAt": -1}}}))
    try:
        policy.read_zello_exact_bans()
    except RuntimeError:
        pass
    else:
        raise AssertionError("invalid policy silently accepted")
print("Zello exact-name ban storage isolation, expiry and unban: ok")
