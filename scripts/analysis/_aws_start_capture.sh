#!/bin/bash
# Immediate contest capture: existing HA/weather collector + minimal price window dumper.
# The price API only serves today/tomorrow, so prices must be captured live.
cd ~/solarbank-experiment
source .venv311/bin/activate
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BASE=outputs/contest_capture_$STAMP
mkdir -p $BASE
echo $BASE > outputs/contest_capture_latest.path
echo "capture base: $BASE"

# 1) existing read-only collector (HA states + weather), 3s wall interval to never miss a 15-min device tick
nohup python -u scripts/online/collect_online_history.py \
  --config config/local/local_online_config.json \
  --cache-dir $BASE/cache \
  --health-dir $BASE/health \
  --node-id nodeA --iterations 0 --interval-seconds 3 \
  > $BASE/collector.out.log 2> $BASE/collector.err.log &
echo "collector pid: $!"

# 2) minimal price window dumper: append the full price window whenever content changes
cat > $BASE/price_dump.py <<'EOF'
import json, sys, time
from pathlib import Path
sys.path.insert(0, ".")
sys.path.insert(0, "src")
from scripts.online.run_shadow_dry_run import fetch_snapshot

out = Path(sys.argv[1])
last = None
n = 0
while True:
    try:
        snap = fetch_snapshot(Path("config/local/local_online_config.json"), include_prices=True)
        prices = snap.get("prices")
        blob = json.dumps(prices, sort_keys=True)
        if prices and blob != last:
            n += 1
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"received_at": snap["started_at"], "prices": prices},
                                    ensure_ascii=False) + "\n")
            last = blob
        err = None
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
    if err:
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "error": err}, ensure_ascii=False) + "\n")
    time.sleep(15)
EOF
nohup python -u $BASE/price_dump.py $BASE/price_windows.jsonl >> $BASE/collector.out.log 2>> $BASE/collector.err.log &
echo "price dumper pid: $!"
sleep 12
echo "=== collector log ==="
tail -3 $BASE/collector.out.log
echo "=== price log ==="
tail -2 $BASE/price_windows.jsonl 2>/dev/null | cut -c1-200
