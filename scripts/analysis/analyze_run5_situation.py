"""run5 current-situation analysis: where money went + battery behavior, no prices needed."""
import csv
import json
import statistics
from collections import Counter
from pathlib import Path

BASE = Path("outputs/local_mock_20261016_run5")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> None:
    d = json.loads((BASE / "dashboard.json").read_text(encoding="utf-8"))
    run = [r for r in d["results"]["runs"] if r.get("ended_at", "").startswith("2026-10-16T14:07")][0]

    print("=== 1. 每站电网交易 vs 基线（钱去哪了）===")
    print(f"{'site':>4} {'基线进口':>10} {'设备进口':>10} {'Δ进口':>8} {'基线出口':>10} {'设备出口':>10} {'Δ出口':>8} {'净Δ':>8}")
    for s in run["sites"]:
        m = s["metrics"]
        di = m["device_grid_import_cost"] - m["baseline_grid_import_cost"]
        de = m["device_grid_export_revenue"] - m["baseline_grid_export_revenue"]
        net = de - di
        print(f"{s['site_id']:>4} {m['baseline_grid_import_cost']:>10.3f} {m['device_grid_import_cost']:>10.3f} "
              f"{di:>+8.3f} {m['baseline_grid_export_revenue']:>10.3f} {m['device_grid_export_revenue']:>10.3f} "
              f"{de:>+8.3f} {net:>+8.3f}")

    rows = list(csv.DictReader(open(BASE / "audit.csv", encoding="utf-8")))

    print("\n=== 2. SOC 轨迹（observed_soc）===")
    for site in ("1", "2", "3"):
        srows = [r for r in rows if r["site"] == site]
        socs = [fnum(r["observed_soc"]) for r in srows]
        socs = [s for s in socs if s is not None]
        print(f"  site {site}: 初始={socs[0]:.0f} 末={socs[-1]:.0f} min={min(socs):.0f} max={max(socs):.0f} "
              f"均={statistics.mean(socs):.1f}  std={statistics.pstdev(socs):.1f}")

    print("\n=== 3. 决策流分布（command_flow / planned_flow）===")
    for site in ("1", "2", "3"):
        srows = [r for r in rows if r["site"] == site]
        cmd = Counter(r["command_flow"] for r in srows)
        plan = Counter(r["planned_flow"] for r in srows)
        print(f"  site {site}: command_flow={dict(cmd)}")
        print(f"            planned_flow={dict(plan)}")

    print("\n=== 4. 充放电分类：自用 vs 套利（command_flow × PV/load 关系）===")
    for site in ("1", "2", "3"):
        srows = [r for r in rows if r["site"] == site]
        cats = Counter()
        for r in srows:
            pv = fnum(r["observed_pv"]) or 0
            ld = fnum(r["observed_load"]) or 0
            cf = r["command_flow"]
            if cf == "charge":
                cats["charge(余量>0自用)"] += 1 if pv > ld else 0
                cats["charge(缺口>0套利购电)"] += 1 if pv <= ld else 0
            elif cf == "discharge":
                cats["discharge(缺口>0自用)"] += 1 if ld > pv else 0
                cats["discharge(余量>0套利外送)"] += 1 if ld <= pv else 0
            elif cf == "self_consumption":
                cats["self_consumption(空闲自用)"] += 1
        print(f"  site {site}: {dict(cats)}")

    print("\n=== 5. 充/放电按时段（看套利时机是否合理）===")
    for site in ("1", "2", "3"):
        srows = [r for r in rows if r["site"] == site]
        # hour-of-day -> count of charge / discharge
        charge_h = Counter()
        disch_h = Counter()
        for r in srows:
            dt = r["device_time"]
            hr = int(dt[11:13]) if len(dt) >= 13 else -1
            cf = r["command_flow"]
            if cf == "charge":
                charge_h[hr] += 1
            elif cf == "discharge":
                disch_h[hr] += 1
        print(f"  site {site}:")
        print(f"    charge   by hour: {dict(sorted(charge_h.items()))}")
        print(f"    discharge by hour: {dict(sorted(disch_h.items()))}")


if __name__ == "__main__":
    main()
