import sys
from pathlib import Path
sys.path.insert(0, "src")
from energy_lab.config import load_runtime_config
c = load_runtime_config(Path("config/local/local_online_config.json"))
print("solver_seconds=", c["solver_seconds"], "poll=", c["poll_seconds"], "stations=", list(c["stations"]))
print("output_directory=", c.get("output_directory"))
