#!/bin/bash
set -e
cd ~/solarbank-experiment
# 1. stop the stale dry-run
kill 758703 2>/dev/null || true
sleep 2
kill -9 758703 2>/dev/null || true
ps -p 758703 -o pid= 2>/dev/null && echo "STILL RUNNING" || echo "process stopped"
# 2. clean dry-run artifacts
rm -f policy.pid online_dry_run_latest.json logs/live.log logs/run_live_audit.csv logs/runtime_meta.json logs/start_evidence.txt
ls logs/ 2>/dev/null; echo "--- artifacts cleaned ---"
# 3. switch to main and update
git fetch origin
git checkout -f main
git merge --ff-only origin/main
git log --oneline -2
