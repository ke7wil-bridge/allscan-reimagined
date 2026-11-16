#!/usr/bin/env python3
"""Isolated incremental native Asterisk log regression."""
import importlib.util, os, tempfile, datetime
from pathlib import Path
spec=importlib.util.spec_from_file_location("diag", Path(__file__).with_name("asr-node-diagnostics.py"))
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
now=datetime.datetime(2026,11,13,18,25,30,tzinfo=datetime.timezone.utc).timestamp()
def row(sec,kind):
    return f"[2026-11-13 18:25:{sec:02d}.100] WARNING[1] {kind}\n"
jb="chan_iax2.c: Resyncing the jb"
tx="taskprocessor.c: Taskprocessor 'iax2_transmit' queue reached 500 scheduled tasks"
with tempfile.TemporaryDirectory() as td:
    os.environ["ASR_ASTERISK_CURSOR_DIR"]=td
    os.environ["ASR_ASTERISK_LOG_TZ"]="utc"
    log=Path(td)/"messages.log"
    log.write_text("".join(row(1,jb) for _ in range(12)))
    first=mod.asterisk_queue_health(log,now)
    assert first["stall"]["jbResyncMaxPerSecond"]==12,first
    assert not first["stall"]["suspected"]
    repeat=mod.asterisk_queue_health(log,now)
    assert repeat["stall"]["jbResyncMaxPerSecond"]==12
    assert repeat["monitoring"]["bytesRead"]==0
    with log.open("a") as f: f.write("".join(row(2,tx) for _ in range(3)))
    combined=mod.asterisk_queue_health(log,now)
    assert combined["stall"]["suspected"] and combined["stall"]["transmitQueueHighWater"]==3,combined
    with log.open("a") as f: f.write("".join(row(3,"noise") for _ in range(15000)))
    burst=mod.asterisk_queue_health(log,now)
    assert burst["monitoring"]["bytesRead"]<=mod.LOG_READ_LIMIT
    assert burst["monitoring"]["backlogBytes"]>0
    assert mod.asterisk_queue_health(log,now)["monitoring"]["backlogBytes"]<burst["monitoring"]["backlogBytes"]
    rotated=Path(td)/"messages.log.1";log.rename(rotated);log.write_text(row(4,tx))
    changed=mod.asterisk_queue_health(log,now)
    assert "rotated" in changed["monitoring"]["monitoringGap"],changed
    with log.open("w") as f: f.write(row(5,tx))
    # Truncation is detected if new size is smaller than saved cursor.
    with log.open("a") as f: f.write(row(6,tx)*3)
    mod.asterisk_queue_health(log,now)
    log.write_text(row(7,tx))
    assert "truncated" in mod.asterisk_queue_health(log,now)["monitoring"]["monitoringGap"]
    assert mod.asterisk_queue_health(log,now+3600)["stall"]["suspected"] is False
    # An incomplete line must not be consumed until its newline arrives.
    partial=Path(td)/"partial.log"
    partial.write_text(row(1,jb).rstrip(chr(10)))
    a=mod.asterisk_queue_health(partial,now)
    assert a["stall"]["jbResyncMaxPerSecond"]==0,a
    with partial.open("a") as f: f.write(chr(10))
    b=mod.asterisk_queue_health(partial,now)
    assert b["stall"]["jbResyncMaxPerSecond"]==1,b
    # Equal-sized rewrite must not be silently ignored.
    original_size=partial.stat().st_size
    partial.write_text(row(2,jb.replace("Resyncing","Rewriting")))
    assert partial.stat().st_size==original_size
    rewritten=mod.asterisk_queue_health(partial,now)
    assert rewritten["monitoring"]["monitoringGap"],rewritten
    os.environ.pop("ASR_ASTERISK_CURSOR_DIR");os.environ.pop("ASR_ASTERISK_LOG_TZ")
print("incremental Asterisk cursor: PASS")
