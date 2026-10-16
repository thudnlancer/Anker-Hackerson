#!/bin/bash
cd ~/solarbank-experiment
BASE=$(cat outputs/contest_capture_latest.path)
echo "== latest 2 rows site1 (price fields) =="
tail -2 $BASE/cache/site_1.nodeA.jsonl | python3 -c 'import json,sys
for l in sys.stdin:
    r=json.loads(l)
    print(r["device_time"], r["price_status"], r.get("import_price"), r.get("price_missing_reason"))'
echo "== price windows dates logged =="
python3 -c 'import json
seen=set()
for l in open("'$BASE'/price_windows.jsonl", encoding="utf-8"):
    r=json.loads(l)
    if "prices" in r:
        seen.add((r["prices"]["today"]["date"], r["received_at"][:16]))
for s in sorted(seen): print(s)'
echo "== price_missing diagnostics in latest heartbeat =="
cat $BASE/health/heartbeat_nodeA.json | python3 -c 'import json,sys; h=json.load(sys.stdin); print(json.dumps(h["station_diagnostics"], ensure_ascii=False), h["last_cycle_at"])'
