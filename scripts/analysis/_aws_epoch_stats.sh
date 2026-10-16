#!/bin/bash
cd ~/solarbank-experiment
BASE=$(cat outputs/contest_capture_latest.path)
python3 - <<EOF
import json
from datetime import datetime

for site in ("1", "2", "3"):
    ts = [datetime.fromisoformat(json.loads(l)["device_time"])
          for l in open(f"$BASE/cache/site_{site}.nodeA.jsonl", encoding="utf-8")]
    jumps = [(ts[i-1], ts[i]) for i in range(1, len(ts)) if ts[i] <= ts[i-1]]
    print(f"site {site}: {len(ts)} ticks, backward jumps: {len(jumps)}")
    for prev, cur in jumps:
        print("   reset:", prev, "->", cur)
    # segment lengths in raw order
    bounds = [0] + [i for i in range(1, len(ts)) if ts[i] <= ts[i-1]] + [len(ts)]
    for a, b in zip(bounds, bounds[1:]):
        seg = ts[a:b]
        print(f"   segment {seg[0]} .. {seg[-1]}  ticks={b-a}  span={(seg[-1]-seg[0]).total_seconds()/3600:.1f}h")
EOF
