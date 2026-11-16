#!/usr/bin/env python3
"""ASR node resource flight recorder and evidence-based recovery guard."""
from __future__ import annotations
import argparse, gzip, json, os, re, subprocess, time
from pathlib import Path

STATE_DIR = Path(os.getenv("ASR_DIAG_STATE_DIR", "/var/lib/allscan-reimagined/diagnostics"))
RUNTIME_DIR = Path(os.getenv("ASR_DIAG_RUNTIME_DIR", "/run/allscan-reimagined"))
LOG = STATE_DIR / "flight-recorder.jsonl"
STATUS = RUNTIME_DIR / "node-diagnostics.json"
STATE = STATE_DIR / "recovery-state.json"
ROTATE_BYTES = 512 * 1024
TOTAL_CAP_BYTES = 4_500_000
KEEP_SECONDS = 48 * 3600
LOW_RAM_KB = 192 * 1024
CRITICAL_RAM_KB = 128 * 1024
SWAP_RISE_KB = 32 * 1024
COOLDOWN = 30 * 60
REPEAT_REQUIRED = 3
PROTECTED = re.compile(r"(?i)(asterisk|asl3|app_rpt)")
SECRET = re.compile(r"(?i)\b(password|passwd|token|cookie|authorization|secret|tgif)\b\s*[:=]\s*([^\s,;]+)")
RECOVERABLE = {"apache2.service", "connected-clients-daemon.service"}
# IAX2/app_rpt stall signature (issue #185). During the 2026-11-13 net,
# Asterisk froze for 10-25 s: dozens of channels resynced their jitter
# buffers in the same second, the iax2_transmit taskprocessor hit its
# high-water mark, peers hit "Max retries", and torn-down calls produced a
# flood of "Silently dropping frame" lines. Thresholds are per 5-minute window.
STALL_WINDOW = 300
STALL_JB_BURST = 10          # jitter-buffer resyncs logged within one second
STALL_TXQ_HITS = 3           # iax2_transmit high-water warnings
STALL_DROPPED_FRAMES = 1000  # frames for calls Asterisk already destroyed
STALL_MAX_RETRIES = 20       # IAX2 "Max retries exceeded"

def sanitize_text(value: str) -> str:
    return SECRET.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)[:240]

def atomic(path: Path, payload: dict, mode=0o640):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    os.chmod(tmp, mode); os.replace(tmp, path)

def meminfo(path=Path("/proc/meminfo")):
    vals = {}
    for line in path.read_text().splitlines():
        k, v = line.split(":", 1); vals[k] = int(v.strip().split()[0])
    return vals

def pss(pid: str):
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text(errors="ignore").splitlines():
            if line.startswith("Pss:"): return int(line.split()[1])
    except (OSError, ValueError): pass
    return 0

def processes():
    rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit(): continue
        try:
            name = (entry / "comm").read_text().strip()[:48]
            rows.append({"pid": int(entry.name), "name": name, "pssKb": pss(entry.name)})
        except OSError: pass
    return sorted(rows, key=lambda r: r["pssKb"], reverse=True)

def aggregate(rows):
    totals = {}
    for row in rows: totals[row["name"]] = totals.get(row["name"], 0) + row["pssKb"]
    return sorted(({"name": n, "pssKb": p} for n, p in totals.items()), key=lambda r: r["pssKb"], reverse=True)

def load_json(path: Path):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return {}

def rotate(now=None):
    now = int(now or time.time())
    if LOG.exists() and LOG.stat().st_size >= ROTATE_BYTES:
        target = STATE_DIR / f"flight-recorder-{now}.jsonl.gz"
        with LOG.open("rb") as src, gzip.open(target, "wb", compresslevel=9) as dst:
            while True:
                chunk = src.read(65536)
                if not chunk: break
                dst.write(chunk)
        os.chmod(target, 0o640); LOG.unlink(missing_ok=True)
    archives = sorted(STATE_DIR.glob("flight-recorder-*.jsonl.gz"), key=lambda p: p.stat().st_mtime)
    for path in list(archives):
        if now - int(path.stat().st_mtime) > KEEP_SECONDS:
            path.unlink(missing_ok=True); archives.remove(path)
    def total(): return (LOG.stat().st_size if LOG.exists() else 0) + sum(p.stat().st_size for p in archives if p.exists())
    while archives and total() > TOTAL_CAP_BYTES:
        archives.pop(0).unlink(missing_ok=True)

def warning_for(sample, state):
    warnings = []
    if sample["memAvailableKb"] < LOW_RAM_KB: warnings.append("Available RAM is low.")
    previous = int(state.get("lastSwapUsedKb", sample["swapUsedKb"]))
    rising = sample["swapUsedKb"] - previous >= SWAP_RISE_KB
    state["swapRiseCount"] = int(state.get("swapRiseCount", 0)) + 1 if rising else 0
    state["lastSwapUsedKb"] = sample["swapUsedKb"]
    if state["swapRiseCount"] >= 2: warnings.append("Swap use is rising across repeated samples.")
    return warnings

def offender(rows):
    eligible = [r for r in aggregate(rows) if not PROTECTED.search(r["name"]) and r["pssKb"] >= 96 * 1024]
    return eligible[0] if eligible else None

def service_for(row):
    name = row.get("name", "")
    if re.match(r"^(apache2|httpd)$", name): return "apache2.service"
    if name == "connected-clients-daemon": return "connected-clients-daemon.service"
    return ""

def recover(sample, rows, state, runner=subprocess.run, sleeper=time.sleep):
    off = offender(rows); unit = service_for(off or {})
    counts = state.setdefault("counts", {})
    stressed = sample["memAvailableKb"] < CRITICAL_RAM_KB and bool(unit)
    for key in list(counts):
        if key != unit: counts[key] = 0
    if unit: counts[unit] = counts.get(unit, 0) + 1 if stressed else 0
    if not stressed or counts.get(unit, 0) < REPEAT_REQUIRED or unit not in RECOVERABLE: return None
    last = int(state.setdefault("lastRecovery", {}).get(unit, 0))
    if sample["epoch"] - last < COOLDOWN: return None
    result = runner(["systemctl", "try-restart", unit], capture_output=True, text=True, timeout=20)
    sleeper(2)
    active = runner(["systemctl", "is-active", "--quiet", unit], timeout=10).returncode == 0
    state["lastRecovery"][unit] = sample["epoch"]; counts[unit] = 0
    return {"unit": unit, "reason": "repeated measured PSS pressure", "beforePssKb": off["pssKb"],
            "restartExit": result.returncode, "serviceReady": active, "endToEndAudioConfirmed": False}

# Native log cursor: bounded per invocation; state survives minute-spaced runs.
# Explicit test state directory enables isolated regression without production writes.
LOG_READ_LIMIT = 262144
LOG_HISTORY_LIMIT = 12000
def read_incremental_log(path, now):
    import hashlib, fcntl
    configured = os.getenv("ASR_ASTERISK_CURSOR_DIR")
    # Custom log paths in existing offline tests retain their stateless behavior.
    default = Path(os.getenv("ASR_ASTERISK_LOG_PATH", "/var/log/asterisk/messages.log"))
    if configured is None and path != default:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - LOG_READ_LIMIT))
            if fh.tell(): fh.readline()
            return fh.read().decode("utf-8", "replace").splitlines(), {}
    directory = Path(configured) if configured else STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(path).encode()).hexdigest()[:16]
    state_path = directory / ("asterisk-cursor-" + key + ".json")
    lock_path = directory / ("asterisk-cursor-" + key + ".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_json(state_path)
        with path.open("rb") as fh:
            stat = os.fstat(fh.fileno())
            identity = [stat.st_dev, stat.st_ino]
            offset = int(state.get("offset", 0))
            gap = ""
            if state.get("identity") != identity:
                if state.get("identity"): gap = "log replaced/rotated; unread records may be missing"
                offset = max(0, stat.st_size - LOG_READ_LIMIT) if not state else 0
            elif offset > stat.st_size:
                gap = "log truncated; unread records may be missing"
                offset = 0
            elif state.get("ctimeNs") != stat.st_ctime_ns and stat.st_size == offset and offset:
                # Same-sized rewrite is not distinguishable from a normal append
                # by size alone; treat it conservatively as a potential gap.
                gap = "log changed at checkpoint; possible copy-truncate rewrite"
                offset = 0
            if offset and not state.get("identity"):
                # First-run tail starts in the middle of an arbitrary record.
                fh.seek(offset - 1)
                if fh.read(1) != bytes([10]):
                    fh.readline()
                    offset = fh.tell()
            fh.seek(offset)
            data = fh.read(LOG_READ_LIMIT)
            # Advance only past complete newline-terminated records.
            complete = data.rfind(b"\n") + 1
            if complete == 0 and len(data) == LOG_READ_LIMIT:
                # Pathological oversized line: advance with an explicit gap.
                complete = len(data)
                gap = "oversized log record skipped"
            lines = data[:complete].decode("utf-8", "replace").splitlines()
            next_offset = offset + complete
            backlog = max(0, stat.st_size - next_offset)
            # Retain bounded five-minute signal history across invocations.
            previous = state.get("history", [])
            history = [row for row in previous if isinstance(row, list) and len(row) == 2 and
                       isinstance(row[0], (int, float)) and now - STALL_WINDOW <= row[0] <= now]
            import datetime
            tz = os.getenv("ASR_ASTERISK_LOG_TZ", "local").lower()
            stamp_re = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+\]")
            for line in lines:
                if not any(x in line for x in ("Resyncing the jb", "iax2_transmit' queue reached",
                    "Silently dropping frame without existent call number", "Max retries exceeded to host",
                    "Log queue threshold", "Exceptionally long voice queue length")): continue
                m = stamp_re.match(line)
                if not m: continue
                try:
                    dt = datetime.datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S")
                    if tz == "utc": dt = dt.replace(tzinfo=datetime.timezone.utc)
                    epoch = dt.timestamp()
                except ValueError: continue
                if 0 <= now - epoch <= STALL_WINDOW: history.append([epoch, line[:350]])
            if len(history) > LOG_HISTORY_LIMIT:
                history = history[-LOG_HISTORY_LIMIT:]
                gap = "rolling event history capped; some warning counts may be incomplete"
            atomic(state_path, {"identity": identity, "offset": next_offset, "history": history,
                                "ctimeNs": stat.st_ctime_ns}, 0o600)
            return [row[1] for row in history], {"backlogBytes": backlog, "monitoringGap": gap,
                                                   "bytesRead": len(data), "cursorOffset": next_offset}

def read_container_incremental(container, now, runner):
    """Read-only bounded container cursor, using POSIX tools and a host checkpoint."""
    import fcntl, hashlib, datetime
    directory = Path(os.getenv("ASR_ASTERISK_CURSOR_DIR", str(STATE_DIR)))
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(("container:" + container).encode()).hexdigest()[:16]
    state_path = directory / ("asterisk-cursor-" + key + ".json")
    with (directory / ("asterisk-cursor-" + key + ".lock")).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_json(state_path)
        source = "/var/log/asterisk/messages.log"
        stat_result = runner(["/usr/bin/docker", "exec", container, "stat", "-c", "%d:%i:%s", source],
                             capture_output=True, timeout=4, check=False)
        if stat_result.returncode: raise OSError("container log stat failed")
        try:
            device, inode, size = map(int, stat_result.stdout.decode().strip().split(":"))
        except (ValueError, UnicodeError) as exc:
            raise OSError("invalid container log stat") from exc
        identity = [device, inode]
        offset = int(state.get("offset", 0))
        gap = ""
        if state.get("identity") != identity:
            if state.get("identity"): gap = "container log rotated; unread records may be missing"
            offset = max(0, size - LOG_READ_LIMIT) if not state else 0
        elif offset > size:
            gap = "container log truncated; unread records may be missing"
            offset = 0
        # tail's +N counts bytes from 1, head caps output; no shell interpolation.
        command = 'tail -c +"$1" "$2" | head -c "$3"'
        result = runner(["/usr/bin/docker", "exec", container, "sh", "-c", command,
                         "asr-log-reader", str(offset + 1), source, str(LOG_READ_LIMIT)],
                        capture_output=True, timeout=4, check=False)
        if result.returncode: raise OSError("container log read failed")
        data = result.stdout[:LOG_READ_LIMIT]
        complete = data.rfind(bytes([10])) + 1
        if not complete and len(data) == LOG_READ_LIMIT:
            complete = len(data)
            gap = "oversized container log record skipped"
        next_offset = offset + complete
        history = [row for row in state.get("history", []) if isinstance(row, list)
                   and len(row) == 2 and isinstance(row[0], (int, float))
                   and 0 <= now - row[0] <= STALL_WINDOW]
        stamp_re = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+\]")
        for line in data[:complete].decode("utf-8", "replace").splitlines():
            if not any(x in line for x in ("Resyncing the jb", "iax2_transmit' queue reached",
                "Silently dropping frame without existent call number", "Max retries exceeded to host",
                "Log queue threshold", "Exceptionally long voice queue length")): continue
            match = stamp_re.match(line)
            if not match: continue
            try:
                moment = datetime.datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S")
                timestamp = moment.replace(tzinfo=datetime.timezone.utc).timestamp()
            except ValueError: continue
            if 0 <= now - timestamp <= STALL_WINDOW: history.append([timestamp, line[:350]])
        if len(history) > LOG_HISTORY_LIMIT:
            history = history[-LOG_HISTORY_LIMIT:]
            gap = "container rolling event history capped"
        atomic(state_path, {"identity": identity, "offset": next_offset, "history": history}, 0o600)
        return [row[1] for row in history], {"mode": "container-incremental", "bytesRead": len(data),
                "cursorOffset": next_offset, "backlogBytes": max(0, size-next_offset), "monitoringGap": gap}

def asterisk_queue_health(log_path=None, now=None, runner=subprocess.run):
    """Read-only early warning for persistent Asterisk media queues.

    Read only the end of the log (not its entire multi-day history). Never
    execute Asterisk commands or take corrective actions from this detector.
    """
    import datetime
    now = time.time() if now is None else now
    log_path = Path(os.getenv("ASR_ASTERISK_LOG_PATH", "/var/log/asterisk/messages.log")) if log_path is None else Path(log_path)
    # Native ASL3 hosts log in host-local time; the Asterisk container logs UTC.
    # ASR_ASTERISK_LOG_TZ=utc|local overrides the per-source default.
    log_tz = os.getenv("ASR_ASTERISK_LOG_TZ", "").strip().lower()
    source_tz = "local"
    try:
        lines, monitoring = read_incremental_log(log_path, now)
    except OSError:
        # On container deployments the log is inside the Asterisk container.
        # Read only its last 256 KiB, bounded by a hard timeout. Never write to
        # the container or restart Asterisk from the diagnostics sampler.
        container = os.getenv("ASR_ASTERISK_LOG_CONTAINER", "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", container):
            return {"available": False, "persistentChannels": [], "recentWarnings": 0}
        try:
            lines, monitoring = read_container_incremental(container, now, runner)
            source_tz = "utc"
        except (OSError, subprocess.TimeoutExpired):
            # Compatibility fallback for older container images without stat/sh.
            try:
                result = runner(["/usr/bin/docker", "exec", container, "tail", "-c", "262144",
                                 "/var/log/asterisk/messages.log"], capture_output=True, timeout=4, check=False)
                if result.returncode:
                    return {"available": False, "persistentChannels": [], "recentWarnings": 0}
                lines = result.stdout.decode("utf-8", "replace").splitlines()[1:]
                source_tz = "utc"
                monitoring = {"mode": "container-tail", "coverageLimited": True, "bytesRead": len(result.stdout)}
            except (OSError, subprocess.TimeoutExpired):
                return {"available": False, "persistentChannels": [], "recentWarnings": 0}
    use_utc = (log_tz or source_tz) == "utc"
    def parse(stamp):
        moment = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        if use_utc: moment = moment.replace(tzinfo=datetime.timezone.utc)
        return moment.timestamp()  # naive => host-local time
    recent = {}
    stamp_re = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+\]")
    queue_re = re.compile(r"Exceptionally long voice queue length \((\d+) voice / \d+ total\) queuing to ([-\w/]+);\d+")
    stall = {"jbResyncMaxPerSecond": 0, "transmitQueueHighWater": 0, "droppedFrames": 0,
             "maxRetries": 0, "loggerDiscards": 0, "first": None, "last": None}
    jb_per_second = {}
    for line in lines:
        stamp = stamp_re.match(line)
        if not stamp: continue
        try: timestamp = parse(stamp[1])
        except ValueError: continue
        if not 0 <= now - timestamp <= STALL_WINDOW: continue
        signal = None
        if "Resyncing the jb" in line:
            jb_per_second[stamp[1]] = jb_per_second.get(stamp[1], 0) + 1; signal = True
        elif "Taskprocessor 'iax2_transmit' queue reached" in line:
            stall["transmitQueueHighWater"] += 1; signal = True
        elif "Silently dropping frame without existent call number" in line:
            stall["droppedFrames"] += 1; signal = True
        elif "Max retries exceeded to host" in line:
            stall["maxRetries"] += 1; signal = True
        elif "Log queue threshold" in line and "Discarding new messages" in line:
            stall["loggerDiscards"] += 1; signal = True
        if signal:
            stall["first"] = timestamp if stall["first"] is None else min(stall["first"], timestamp)
            stall["last"] = timestamp if stall["last"] is None else max(stall["last"], timestamp)
            continue
        match = queue_re.search(line)
        if not match: continue
        channel = match[2]
        entry = recent.setdefault(channel, {"count": 0, "first": timestamp, "last": timestamp, "maxVoiceFrames": 0})
        entry["count"] += 1
        entry["first"] = min(entry["first"], timestamp)
        entry["last"] = max(entry["last"], timestamp)
        entry["maxVoiceFrames"] = max(entry["maxVoiceFrames"], int(match[1]))
    stall["jbResyncMaxPerSecond"] = max(jb_per_second.values(), default=0)
    reasons = []
    if stall["jbResyncMaxPerSecond"] >= STALL_JB_BURST: reasons.append("jitter-buffer resync burst")
    if stall["transmitQueueHighWater"] >= STALL_TXQ_HITS: reasons.append("iax2_transmit queue at high-water")
    if stall["droppedFrames"] >= STALL_DROPPED_FRAMES: reasons.append("frames for torn-down calls")
    if stall["maxRetries"] >= STALL_MAX_RETRIES: reasons.append("IAX2 max retries")
    if stall["loggerDiscards"]: reasons.append("Asterisk logger discarding")
    # One noisy signal alone can be a single bad peer; require two, or the
    # logger itself falling behind, before calling it a whole-process stall.
    stall["suspected"] = len(reasons) >= 2 or bool(stall["loggerDiscards"])
    stall["reasons"] = reasons
    persistent = [{"channel": name, **value} for name, value in recent.items()
                  if value["count"] >= 3 and value["last"] - value["first"] >= 10]
    return {"available": True, "persistentChannels": persistent[:12],
            "recentWarnings": sum(v["count"] for v in recent.values()), "stall": stall,
            "monitoring": monitoring}

def sample_once(allow_recovery=True):
    STATE_DIR.mkdir(parents=True, exist_ok=True); os.chmod(STATE_DIR, 0o750)
    # ASTAPI (www-data) publishes its shared feed and talker history here with
    # atomic create-and-rename writes. Preserve the group-writable sticky mode
    # established by the installer instead of revoking it every minute.
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True); os.chmod(RUNTIME_DIR, 0o1775)
    m = meminfo(); epoch = int(time.time()); rows = processes(); grouped = aggregate(rows)
    swap_used = max(0, m.get("SwapTotal", 0) - m.get("SwapFree", 0)); load = os.getloadavg()
    sample = {"epoch": epoch, "memAvailableKb": m.get("MemAvailable", 0), "swapUsedKb": swap_used,
              "load1": round(load[0], 2), "load5": round(load[1], 2), "load15": round(load[2], 2),
              "processPssKb": {r["name"]: r["pssKb"] for r in grouped[:20]}}
    queue_health = asterisk_queue_health()
    sample["asteriskQueueHealth"] = queue_health
    state = load_json(STATE); warnings = warning_for(sample, state)
    if queue_health.get("monitoring", {}).get("monitoringGap"):
        warnings.append("Asterisk diagnostic monitoring gap: " + queue_health["monitoring"]["monitoringGap"])
    if queue_health["persistentChannels"]:
        warnings.append("Asterisk has a persistent unconsumed audio queue; investigate IAXLink and app_rpt before TX hangs.")
    if queue_health.get("stall", {}).get("suspected"):
        warnings.append("Asterisk IAX2 stall signature in the last 5 minutes (" +
                        ", ".join(queue_health["stall"]["reasons"]) + "); links may be dropping.")
    recovery = recover(sample, rows, state) if allow_recovery else None
    atomic(STATE, state, 0o600)
    record = {"sample": sample, "warnings": warnings, "snapshot": rows[:30] if warnings else [], "recovery": recovery}
    with LOG.open("a", encoding="utf-8") as fh: fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    os.chmod(LOG, 0o640); rotate(epoch)
    atomic(STATUS, {"ok": True, "retentionHours": 48, "sample": sample, "warnings": warnings,
                    "recovery": recovery, "evidencePolicy": "Process attribution requires recorded PSS evidence."}, 0o644)
    return record

def self_test():
    import tempfile
    assert PROTECTED.search("asterisk") and not service_for({"name":"asterisk"})
    assert service_for({"name":"apache2"}) == "apache2.service"
    assert aggregate([{"name":"apache2","pssKb":60},{"name":"apache2","pssKb":70}])[0]["pssKb"] == 130
    st = {}; assert warning_for({"memAvailableKb":LOW_RAM_KB-1,"swapUsedKb":0}, st) == ["Available RAM is low."]
    st = {"lastSwapUsedKb":0}; warning_for({"memAvailableKb":LOW_RAM_KB+1,"swapUsedKb":SWAP_RISE_KB+1}, st)
    assert "Swap use is rising" in warning_for({"memAvailableKb":LOW_RAM_KB+1,"swapUsedKb":2*SWAP_RISE_KB+2}, st)[0]
    assert "secret" not in sanitize_text("token=secret")
    assert offender([{"name":"asterisk","pssKb":999999}]) is None
    calls=[]
    class R: returncode=0
    def fake(cmd, **kwargs): calls.append(cmd); return R()
    state={"counts":{"apache2.service":2},"lastRecovery":{}}
    rec=recover({"epoch":COOLDOWN+1,"memAvailableKb":CRITICAL_RAM_KB-1}, [{"name":"apache2","pssKb":100*1024}], state, fake, lambda _:None)
    assert rec and rec["serviceReady"] and rec["endToEndAudioConfirmed"] is False and all("asterisk" not in " ".join(c) for c in calls)
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); old_state, old_log = globals()["STATE_DIR"], globals()["LOG"]
        globals()["STATE_DIR"], globals()["LOG"] = root, root/"flight-recorder.jsonl"
        globals()["LOG"].write_bytes(b"x"*(ROTATE_BYTES+1)); rotate(int(time.time()))
        assert list(root.glob("*.gz")) and sum(p.stat().st_size for p in root.iterdir()) <= TOTAL_CAP_BYTES
        globals()["STATE_DIR"], globals()["LOG"] = old_state, old_log
    print("node diagnostics self-test: PASS")

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--no-recovery", action="store_true")
    args=parser.parse_args()
    if args.self_test: self_test(); return
    sample_once(not args.no_recovery)
if __name__ == "__main__": main()
