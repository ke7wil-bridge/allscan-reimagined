#!/usr/bin/env python3
"""Offline regression for Asterisk queue early warning (no live Asterisk)."""
import importlib.util, tempfile, datetime
from pathlib import Path
p = Path(__file__).with_name("asr-node-diagnostics.py")
spec = importlib.util.spec_from_file_location("asr_diag", p)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
now = datetime.datetime(2026, 11, 6, 23, 54, tzinfo=datetime.timezone.utc).timestamp()
with tempfile.TemporaryDirectory() as td:
    log = Path(td)/"messages.log"
    def line(second, channel="Announcer/IAXLink-000012b5", frames=97):
        return f"[2026-11-06 23:53:{second:02d}.023] WARNING[12] channel.c: Exceptionally long voice queue length ({frames} voice / 99 total) queuing to {channel};1\n"
    log.write_text("".join(line(s) for s in (1, 8, 15, 22)))
    found = mod.asterisk_queue_health(log, now)
    assert found["recentWarnings"] == 4 and len(found["persistentChannels"]) == 1, found
    assert found["persistentChannels"][0]["maxVoiceFrames"] == 97
    log.write_text(line(1))
    assert not mod.asterisk_queue_health(log, now)["persistentChannels"]
    assert not mod.asterisk_queue_health(log, now+3600)["persistentChannels"]
    assert not mod.asterisk_queue_health(Path(td)/"missing", now)["available"]
    class Result:
        returncode = 0
        stdout = ("ignored partial line\n" + "".join(line(s) for s in (1,8,15,22))).encode()
    calls = []
    def fake(argv, **kwargs):
        calls.append((argv, kwargs)); return Result()
    import os
    os.environ["ASR_ASTERISK_LOG_CONTAINER"] = "allstarlink3"
    found = mod.asterisk_queue_health(Path(td)/"missing", now, fake)
    assert len(found["persistentChannels"]) == 1 and len(calls) == 1
    assert calls[0][0][:3] == ["/usr/bin/docker", "exec", "allstarlink3"]
    os.environ["ASR_ASTERISK_LOG_CONTAINER"] = "../../unsafe"
    assert not mod.asterisk_queue_health(Path(td)/"missing", now, fake)["available"]
    assert len(calls) == 1
    os.environ.pop("ASR_ASTERISK_LOG_CONTAINER")
    # A warning immediately before a simulated app_rpt hang must be visible.
    log.write_text("".join(line(s) for s in (1, 8, 15, 22)))
    before_hang = mod.asterisk_queue_health(log, now)
    assert before_hang["persistentChannels"][0]["channel"] == "Announcer/IAXLink-000012b5"
    # Regression: container installer must enable the read-only log path.
    installer = Path(__file__).with_name("asr-container-host-install.py").read_text()
    assert "Environment=ASR_ASTERISK_LOG_CONTAINER={profile.asterisk_container}" in installer
    # The diagnostic never issues a hangup or restart command.
    detector = Path(__file__).with_name("asr-node-diagnostics.py").read_text()
    assert '"exec", container, "tail"' in detector
    assert "channel request hangup" not in detector
print("Asterisk queue early warning: PASS")
