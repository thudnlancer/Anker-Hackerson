"""Conservative PV allocation from replay traces; never claims curtailed energy."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math


def account_rows(rows: list[dict], *, arm: str = "zero_flow", step_hours: float = 1.0,
                 max_charge_kw: float | None = None,
                 time_basis: str = "timezone_aware") -> list[dict]:
    if time_basis not in {"timezone_aware", "dataset_local_hour"}:
        raise ValueError("unknown trace time basis")
    if not math.isfinite(step_hours) or step_hours <= 0:
        raise ValueError("step_hours must be positive and finite")
    if max_charge_kw is not None and (not math.isfinite(max_charge_kw) or max_charge_kw <= 0):
        raise ValueError("max_charge_kw must be positive and finite")
    buckets = defaultdict(list)
    for row in rows:
        if row.get("arm") != arm:
            continue
        stamp = datetime.fromisoformat(str(row["time"]).replace("Z", "+00:00"))
        aware = stamp.tzinfo is not None and stamp.utcoffset() is not None
        if time_basis == "timezone_aware" and not aware:
            raise ValueError("trace time requires an explicit timezone")
        if time_basis == "dataset_local_hour" and aware:
            raise ValueError("dataset local-hour traces must not mix timezone-aware timestamps")
        buckets[(str(row["site"]), stamp.date().isoformat())].append(row)
    reports = []
    for (site, day), group in sorted(buckets.items()):
        totals = {key: 0.0 for key in ("pv_generated_kwh", "direct_self_use_kwh",
            "pv_to_battery_kwh", "pv_to_grid_kwh", "grid_export_kwh",
            "negative_export_loss_eur")}
        bottlenecks = {"battery_full_steps": 0, "charge_limit_steps": None if max_charge_kw is None else 0,
                       "negative_price_export_steps": 0}
        for row in group:
            pv_w, load_w = float(row["pv_w"]), float(row["load_w"])
            power_w, sell = float(row["power_w"]), float(row["sell"])
            soc = float(row["soc_after"])
            flow = row["flow"]
            export = float(row["grid_export_kwh"])
            loss = float(row["negative_export_loss_eur"])
            if not all(math.isfinite(x) for x in (pv_w, load_w, power_w, sell, soc, export, loss)):
                raise ValueError("trace contains non-finite values")
            if min(pv_w, load_w, power_w, export, loss) < -1e-9 or not 0 <= soc <= 100:
                raise ValueError("trace contains invalid physical values")
            if flow not in {"charge", "discharge", "idle", "hold"}:
                raise ValueError("trace contains unknown battery flow")
            expected_loss = export * max(0.0, -sell)
            if abs(loss - expected_loss) > 1e-6:
                raise ValueError("negative export loss does not reconcile with trace")
            pv = pv_w / 1000 * step_hours
            direct = min(pv, load_w / 1000 * step_hours)
            surplus = pv - direct
            charge = power_w / 1000 * step_hours if flow == "charge" else 0.0
            pv_to_battery = min(surplus, charge)
            pv_to_grid = max(0.0, surplus - pv_to_battery)
            # A trace may export discharged battery energy too; do not label that PV.
            expected_export = max(0.0, (pv_w + (power_w if flow == "discharge" else 0.0)
                - load_w - (power_w if flow == "charge" else 0.0)) / 1000 * step_hours)
            if abs(export - expected_export) > 1e-6 or pv_to_grid > export + 1e-6:
                raise ValueError("PV allocation exceeds measured grid export")
            totals["pv_generated_kwh"] += pv
            totals["direct_self_use_kwh"] += direct
            totals["pv_to_battery_kwh"] += pv_to_battery
            totals["pv_to_grid_kwh"] += pv_to_grid
            totals["grid_export_kwh"] += export
            totals["negative_export_loss_eur"] += loss
            if surplus > 1e-6 and export > 1e-6:
                bottlenecks["battery_full_steps"] += soc >= 99
                bottlenecks["negative_price_export_steps"] += sell < 0
                if max_charge_kw is not None and flow == "charge":
                    bottlenecks["charge_limit_steps"] += power_w / 1000 >= 0.95 * max_charge_kw
        reports.append({"site_id": site, "device_date": day, "arm": arm,
            "steps": len(group), "step_hours": step_hours,
            "allocation_method": "estimate_pv_first_to_load_then_battery_then_grid",
            "source_kind": "replay_trace", "curtailment_status": "not_measured",
            "totals": totals, "bottlenecks": bottlenecks,
            "caveat": "光伏去向是按功率平衡构造的估算；电网外送与负价外送损失取回放 trace，并非真实设备实测或官方结算。未提供潜在出力，不能核算弃光；未提供额定充电功率时不统计功率瓶颈。"
                + (" 时间口径为 dataset_local_hour：仅按 CSV 原始设备本地日期分组，时区未知，不补猜 UTC、不用于在线截止约束或跨时区对齐。"
                   if time_basis == "dataset_local_hour" else " 时间口径为带时区的 trace 原始日期。")})
    return reports
