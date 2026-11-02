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
    state = load_json(STATE); warnings = warning_for(sample, state)
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
