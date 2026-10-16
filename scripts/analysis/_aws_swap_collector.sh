#!/bin/bash
# Swap the running capture to the enhanced collector (same cache dir => seamless continuity)
cd ~/solarbank-experiment
git fetch -q origin && git reset -q --hard origin/main
git log --oneline -1
source .venv311/bin/activate
python -m pytest tests/test_history_collector.py -q 2>&1 | tail -1

BASE=$(cat outputs/contest_capture_latest.path)
echo "capture base: $BASE"

# stop old collector + price dumper
pkill -f collect_online_history.py
pkill -f price_dump.py
sleep 2

# restart with price capture; keep the same price_windows.jsonl for continuity
nohup python -u scripts/online/collect_online_history.py \
  --config config/local/local_online_config.json \
  --cache-dir $BASE/cache \
  --health-dir $BASE/health \
  --node-id nodeA --iterations 0 --interval-seconds 3 \
  --include-prices --price-log $BASE/price_windows.jsonl \
  >> $BASE/collector.out.log 2>> $BASE/collector.err.log &
echo "new collector pid: $!"
sleep 12
echo "=== latest collector log ==="
tail -2 $BASE/collector.out.log | cut -c1-400
echo "=== manifest ==="
cat $BASE/cache/manifest.json
echo "=== last tick row (price columns) ==="
tail -1 $BASE/cache/site_1.nodeA.jsonl | python3 -c "import json,sys; r=json.loads(sys.stdin.read()); print({k: r.get(k) for k in ('site','device_time','soc','pv','load','import_price','export_price','price_status')})"
