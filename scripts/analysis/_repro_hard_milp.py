"""Reproduce run6 hard MILP instances offline; test mip_rel_gap sensitivity."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
sys.path.insert(0, str(ROOT / "src"))
import numpy as np
import pandas as pd
import energy_lab.rolling as rolling

price_log = ROOT / "outputs" / "local_mock_20261016_run6" / "price_series.jsonl"
entries = [json.loads(line) for line in price_log.open(encoding="utf-8")]

def find_round(slot):
    for e in entries:
        if e["api_current_start"] == slot:
            return e
    raise KeyError(slot)

def run_case(slot, soc, pv, load, gap, time_limit=2.0):
    e = find_round(slot)
    pts = pd.DataFrame(e["points"])
    pts["time"] = pd.to_datetime(pts["time"], utc=True)
    tick = pd.Timestamp(slot)
    times = pd.Series(pd.date_range(tick, periods=96, freq="15min"))
    lookup = pts.set_index("time").reindex(times.dt.floor("h"))
    assert lookup["import_price"].notna().all(), "missing price hours"
    original_milp = rolling.milp
    def patched(c, **kw):
        kw["options"] = {"time_limit": time_limit, "mip_rel_gap": gap}
        return original_milp(c, **kw)
    rolling.milp = patched
    try:
        t0 = time.monotonic()
        r = rolling.optimize_window(
            times, np.full(96, pv), np.full(96, load),
            lookup["import_price"].to_numpy(), lookup["export_price"].to_numpy(),
            soc, capacity_kwh=14.0, max_power_w=5000.0, soc_min=0.0, soc_max=100.0,
            terminal_price=None, degradation_cost_eur_kwh=0.02,
            solver="milp", step_hours=0.25, command_contract="sop",
            time_limit_seconds=time_limit)
        elapsed = time.monotonic() - t0
        return r.status, elapsed, r.mip_gap, r.suboptimal
    finally:
        rolling.milp = original_milp

# run6 hard instances: (slot, soc, pv, load) from audit.csv
cases = [
    ("2026-10-10T19:00:00Z", 17, 271, 812),   # site2 limit_reached
    ("2026-10-10T21:00:00Z", 12, 0, 174),     # site2 limit_reached
    ("2026-10-14T01:00:00Z", 48, 0, 1111),    # site2 limit_reached
]
for slot, soc, pv, load in cases:
    print(f"\n=== {slot} soc={soc} pv={pv} load={load} ===")
    for tl in (0.1, 0.25, 0.5, 1.0, 2.0):
        status, elapsed, mg, sub = run_case(slot, soc, pv, load, 1e-9, time_limit=tl)
        print(f"  time_limit={tl:<5} status={status:<14} elapsed={elapsed:.3f}s "
              f"mip_gap={mg} suboptimal={sub}")
