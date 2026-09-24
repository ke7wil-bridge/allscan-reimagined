#!/usr/bin/env python3
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location("status", Path(__file__).with_name("asr_bridge_status.py"))
status=importlib.util.module_from_spec(spec); spec.loader.exec_module(status)
s=status.initial_activity_state(); now=1790200000
lines=[
 "M: 2026-09-24 00:39:24.643 D-Star, TX state = ON",
 "I: 2026-09-24 00:39:24.643 D-Star, Begin TX: src=3224939 rpt=322493901 dst=9 slot=2 cc=0 metadata=3224939",
 "M: 2026-09-24 00:39:24.643 D-Star, No call or id found, using ini value: KE7WIL  ",
 "M: 2026-09-24 00:39:33.543 D-Star, TX state = OFF",
]
for line in lines: status.apply_activity_line(s,line,"dstar",now)
assert s["role"]=="idle" and s["last_user"]=="-"
assert s["recent_users"]==[]
assert s["tx_events"]==[]
# Existing network-header format remains supported.
t=status.initial_activity_state()
status.apply_activity_line(t,"M: 2026-09-24 00:40:00.000 D-Star, received network header from N0CALL / ABCD to CQCQCQ via XRF641 A","dstar",now)
assert t["current_user"]=="N0CALL" and t["reflector"]=="XRF641" and t["module"]=="A"
print("D-Star activity self-test: ok")
