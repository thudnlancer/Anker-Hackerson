#!/bin/bash
cd ~/solarbank-experiment
git fetch -q origin && git reset -q --hard origin/main
source .venv311/bin/activate
BASE=$(cat outputs/contest_capture_latest.path)
python scripts/analysis/validate_capture.py $BASE
echo "exit=$?"
