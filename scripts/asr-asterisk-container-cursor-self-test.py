#!/usr/bin/env python3
"""Container cursor regression with simulated read-only docker commands."""
import importlib.util, os, tempfile, datetime
from pathlib import Path
spec=importlib.util.spec_from_file_location("diag",Path(__file__).with_name("asr-node-diagnostics.py"))
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
now=datetime.datetime(2026,11,13,18,25,30,tzinfo=datetime.timezone.utc).timestamp()
def line(sec, msg):
    return f"[2026-11-13 18:25:{sec:02d}.100] WARNING[1] {msg}"+chr(10)
with tempfile.TemporaryDirectory() as td:
    os.environ["ASR_ASTERISK_CURSOR_DIR"]=td
    os.environ["ASR_ASTERISK_LOG_CONTAINER"]="allstarlink3"
    blob=bytearray((line(1,"chan_iax2.c: Resyncing the jb")*12).encode())
    calls=[]
    rotated=None
    inode=123
    class Result:
        def __init__(self, data): self.returncode=0;self.stdout=data
    def runner(argv,**kw):
        calls.append(argv)
        assert kw["timeout"]==4 and argv[:3]==["/usr/bin/docker","exec","allstarlink3"]
        if argv[3]=="stat":
            if argv[-1].endswith(".1"):
                return Result(f"1:123:{len(rotated)}".encode()) if rotated is not None else type("Fail",(),{"returncode":1,"stdout":b""})()
            return Result(f"1:{inode}:{len(blob)}".encode())
        assert argv[3:5]==["sh","-c"],argv
        offset=int(argv[-3])-1;limit=int(argv[-1])
        source=rotated if argv[-2].endswith(".1") else blob
        return Result(bytes(source[offset:offset+limit]))
    missing=Path(td)/"no-host-log"
    first=mod.asterisk_queue_health(missing,now,runner)
    assert first["stall"]["jbResyncMaxPerSecond"]==12,first
    second=mod.asterisk_queue_health(missing,now,runner)
    assert second["monitoring"]["bytesRead"]==0 and second["stall"]["jbResyncMaxPerSecond"]==12,second
    blob.extend((line(2,"taskprocessor.c: Taskprocessor 'iax2_transmit' queue reached 500 scheduled tasks")*3).encode())
    third=mod.asterisk_queue_health(missing,now,runner)
    assert third["stall"]["suspected"] and third["monitoring"]["mode"]=="container-incremental",third
    # Unread warnings in a renamed container log must survive rotation.
    blob.extend((line(3,"taskprocessor.c: Taskprocessor 'iax2_transmit' queue reached 500 scheduled tasks")*2).encode())
    rotated=bytes(blob)
    inode=124
    blob=bytearray(line(4,"chan_iax2.c: Resyncing the jb").encode())
    recovered=mod.asterisk_queue_health(missing,now,runner)
    assert recovered["stall"]["transmitQueueHighWater"]==5,recovered
    assert recovered["monitoring"]["monitoringGap"]=="",recovered
    assert any(c[-1].endswith(".1") for c in calls if c[3]=="stat")
    assert all("restart" not in " ".join(c) for c in calls)
    os.environ.pop("ASR_ASTERISK_CURSOR_DIR");os.environ.pop("ASR_ASTERISK_LOG_CONTAINER")
print("container incremental cursor: PASS")
