#!/bin/bash
cd ~/solarbank-experiment
git fetch -q origin && git reset -q --hard origin/main
git log --oneline -1
source .venv311/bin/activate
python -m pytest tests/test_history_collector.py tests/test_collector_health.py -q 2>&1 | tail -1

BASE=$(cat outputs/contest_capture_latest.path)

# stop old supervisor loop + collector + any stale monitor
pkill -f supervise.sh; pkill -f collect_online_history.py; pkill -f collector_monitor.py
sleep 2

# restart the collector supervisor (new code picks up stall alerting automatically)
nohup bash $BASE/supervise.sh > /dev/null 2>&1 &
echo "supervisor pid: $!"

# start the monitor daemon: records stall/node incidents into incidents.jsonl
nohup python -u scripts/online/collector_monitor.py \
  --health-dir $BASE/health --nodes nodeA \
  --interval-seconds 30 --iterations 0 \
  >> $BASE/monitor.out.log 2>> $BASE/monitor.err.log &
echo "monitor pid: $!"

sleep 12
echo "=== heartbeat stall block ==="
python3 -c 'import json; h=json.load(open("'$BASE'/health/heartbeat_nodeA.json")); print(json.dumps(h["device_time_stall"], ensure_ascii=False))'
echo "=== monitor status ==="
python3 -c 'import json; r=json.load(open("'$BASE'/health/monitor_status.json")); print(r["cluster"], r["nodes"]["nodeA"]["state"], json.dumps(r["nodes"]["nodeA"].get("device_time_stall"), ensure_ascii=False))'
echo "=== processes ==="
ps aux | grep -E "[c]ollect_online_history|[c]ollector_monitor|[s]upervise.sh" | awk '{print $2, $11, $12, $13}' | head -5
