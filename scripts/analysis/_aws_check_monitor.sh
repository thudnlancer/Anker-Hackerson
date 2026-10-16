#!/bin/bash
cd ~/solarbank-experiment
BASE=$(cat outputs/contest_capture_latest.path)
python3 - <<EOF
import json
r = json.load(open("$BASE/health/monitor_status.json"))
n = r["nodes"]["nodeA"]
print(r["cluster"], n["state"], "stall:", json.dumps(n.get("device_time_stall"), ensure_ascii=False))
EOF
