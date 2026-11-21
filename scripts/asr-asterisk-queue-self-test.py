#!/usr/bin/env python3
"""Offline regression for Asterisk queue early warning (no live Asterisk)."""
import importlib.util, tempfile, datetime, os, time
from pathlib import Path
p = Path(__file__).with_name("asr-node-diagnostics.py")
spec = importlib.util.spec_from_file_location("asr_diag", p)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
now = datetime.datetime(2026, 11, 6, 23, 54, tzinfo=datetime.timezone.utc).timestamp()
# The original cases use UTC-stamped lines (container Asterisk). Pin that
# explicitly so the test no longer silently depends on the host timezone.
os.environ["ASR_ASTERISK_LOG_TZ"] = "utc"
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
    # The cursor probes stat first; this legacy mock cannot provide stat data,
    # so it must fall back to the bounded tail reader without losing warnings.
    assert len(found["persistentChannels"]) == 1 and 1 <= len(calls) <= 2, (found, calls)
    assert calls[-1][0][3] == "tail"
    assert all(call[0][3] in ("stat", "tail") for call in calls)
    assert found["monitoring"]["coverageLimited"] is True
    os.environ["ASR_ASTERISK_LOG_CONTAINER"] = "../../unsafe"
    assert not mod.asterisk_queue_health(Path(td)/"missing", now, fake)["available"]
    assert 1 <= len(calls) <= 2
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
os.environ.pop("ASR_ASTERISK_LOG_TZ", None)

# Issue #185: native ASL3 hosts log in host-local time. Before the fix every
# local-time line on a US Central host looked 6 h in the future and was
# dropped, so the flight recorder reported recentWarnings=0 through the
# 2026-11-13 net stalls. Re-run with the production timezone.
os.environ["TZ"] = "America/Chicago"; time.tzset()
try:
    net = datetime.datetime(2026, 11, 13, 18, 25, 30).timestamp()  # 18:25:30 CST
    with tempfile.TemporaryDirectory() as td:
        log = Path(td)/"messages.log"
        queue = "".join(
            f"[2026-11-13 18:25:{s:02d}.100] WARNING[2968324][C-00000276] channel.c: Exceptionally long voice queue length (97 voice / 97 total) queuing to Announcer/IAXLink-00000960;1\n"
            for s in (8, 9, 10, 12, 14, 20))
        log.write_text(queue)
        local = mod.asterisk_queue_health(log, net)
        assert local["recentWarnings"] == 6 and len(local["persistentChannels"]) == 1, local
        assert not local["stall"]["suspected"]

        # Recorded stall signature from the 18:24-18:25 net window.
        stall = []
        stall += [f"[2026-11-13 18:25:01.{i:03d}] WARNING[3740421][C-00000233] chan_iax2.c: Resyncing the jb. last_delay 0, this delay -9999, threshold 2000, new offset -1\n" for i in range(27)]
        stall += [f"[2026-11-13 18:24:{s:02d}.579] WARNING[3714980][C-0000027b] taskprocessor.c: Taskprocessor 'iax2_transmit' queue reached 500 scheduled tasks (high water mark: 500) again.\n" for s in (25, 46, 59)]
        stall += [f"[2026-11-13 18:25:02.{i:03d}] WARNING[3740406] chan_iax2.c: Silently dropping frame without existent call number: 4321\n" for i in range(1200)]
        stall += ["[2026-11-13 18:24:47.661] WARNING[3740420] logger: Log queue threshold (1000) exceeded.  Discarding new messages.\n"]
        log.write_text("".join(stall))
        found = mod.asterisk_queue_health(log, net)
        st = found["stall"]
        assert st["suspected"], st
        assert st["jbResyncMaxPerSecond"] == 27 and st["transmitQueueHighWater"] == 3, st
        assert st["droppedFrames"] == 1200 and st["loggerDiscards"] == 1, st
        assert "jitter-buffer resync burst" in st["reasons"]

        # The same lines an hour later are outside the 5-minute window.
        assert not mod.asterisk_queue_health(log, net + 3600)["stall"]["suspected"]

        # One unreachable peer alone (max retries only) is not a whole-process stall.
        log.write_text("".join(f"[2026-11-13 18:25:{s:02d}.000] WARNING[1] chan_iax2.c: Max retries exceeded to host 192.0.2.10 on IAX2/192.0.2.10:4569-1 (type = 7, subclass = 0, ts=1, seqno=1)\n" for s in range(25)))
        lone = mod.asterisk_queue_health(log, net)["stall"]
        assert lone["maxRetries"] == 25 and not lone["suspected"], lone

        # Container logs stay UTC even on a Central-time host.
        class UtcResult:
            returncode = 0
            stdout = ("partial\n" + "".join(
                f"[2026-11-14 00:25:{s:02d}.100] WARNING[1] channel.c: Exceptionally long voice queue length (97 voice / 97 total) queuing to Announcer/IAXLink-1;1\n"
                for s in (8, 12, 20))).encode()
        os.environ["ASR_ASTERISK_LOG_CONTAINER"] = "allstarlink3"
        boxed = mod.asterisk_queue_health(Path(td)/"missing", net, lambda argv, **kw: UtcResult())
        assert boxed["recentWarnings"] == 3, boxed
        os.environ.pop("ASR_ASTERISK_LOG_CONTAINER")
finally:
    os.environ.pop("TZ", None); time.tzset()

# The stall detector stays read-only: no Asterisk CLI, hangup or restart.
assert "asterisk -rx" not in detector and '"-rx"' not in detector
print("Asterisk queue early warning: PASS")
