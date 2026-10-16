#!/bin/bash
set -e
mkdir -p ~/solarbank-experiment/config/local
base64 -d /tmp/cfg.b64 > ~/solarbank-experiment/config/local/local_online_config.json
rm /tmp/cfg.b64
cd ~/solarbank-experiment
cat > /tmp/verify_cfg.py <<'EOF'
import sys
sys.path.insert(0, "src")
from energy_lab.config import load_runtime_config
c = load_runtime_config("config/local/local_online_config.json")
print("solver_seconds=", c["solver_seconds"], "poll=", c["poll_seconds"], "stations=", list(c["stations"]))
print("output_directory=", c["output_directory"])
EOF
.venv311/bin/python /tmp/verify_cfg.py
