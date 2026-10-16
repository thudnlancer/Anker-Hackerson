#!/bin/bash
# Wrap the collector in a restart loop: the collector exits non-zero after 12
# consecutive errors by design; supervision (crash-loop restart) lives here.
cd ~/solarbank-experiment
source .venv311/bin/activate
BASE=$(cat outputs/contest_capture_latest.path)

pkill -f collect_online_history.py
sleep 2

cat > $BASE/supervise.sh <<EOF
#!/bin/bash
cd ~/solarbank-experiment
source .venv311/bin/activate
while true; do
  python -u scripts/online/collect_online_history.py \
    --config config/local/local_online_config.json \
    --cache-dir $BASE/cache \
    --health-dir $BASE/health \
    --node-id nodeA --iterations 0 --interval-seconds 3 \
    --include-prices --price-log $BASE/price_windows.jsonl \
    >> $BASE/collector.out.log 2>> $BASE/collector.err.log
  echo "\$(date -u +%FT%TZ) collector exited rc=\$?; restarting in 5s" >> $BASE/supervisor.log
  sleep 5
done
EOF
chmod +x $BASE/supervise.sh
nohup bash $BASE/supervise.sh > /dev/null 2>&1 &
echo "supervisor pid: $!"
sleep 10
tail -1 $BASE/collector.out.log | cut -c1-300
ps aux | grep -c "[c]ollect_online_history"
