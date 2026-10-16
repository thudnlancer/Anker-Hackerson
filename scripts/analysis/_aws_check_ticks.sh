#!/bin/bash
cd ~/solarbank-experiment
BASE=$(cat outputs/contest_capture_latest.path)
echo "== latest row =="
tail -1 $BASE/cache/site_1.nodeA.jsonl | python3 -c 'import json,sys; r=json.loads(sys.stdin.read()); print(r["device_time"], "received", r["received_at"])'
echo "== last 8 device_times site1 =="
tail -8 $BASE/cache/site_1.nodeA.jsonl | python3 -c 'import json,sys; [print(json.loads(l)["device_time"]) for l in sys.stdin]'
echo "== row count per site =="
wc -l $BASE/cache/site_*.jsonl
