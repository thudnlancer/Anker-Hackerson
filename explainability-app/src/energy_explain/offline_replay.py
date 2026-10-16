"""Read-only W1 hourly trace adapter; local hours are never converted to instants."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
import hashlib
from pathlib import Path


ARMS = ("self_consumption", "persistence", "irradiance")
REQUIRED = {"site", "time", "arm", "pv_w", "load_w",
            "buy", "sell", "flow", "power_w", "soc_before", "soc_after",
            "grid_import_kwh", "grid_export_kwh", "cost_eur", "negative_export_loss_eur",
            "command_mode", "command_flow", "command_target_w", "planned_flow",
            "planned_power_w"}
NUMBERS = ("pv_w", "load_w", "buy", "sell", "power_w", "soc_before", "soc_after",
           "grid_import_kwh", "grid_export_kwh", "cost_eur", "negative_export_loss_eur",
           "command_target_w", "planned_power_w")


class OfflineReplay:
    def __init__(self, trace: str | Path, expected_sha256: str):
        self.trace = Path(trace).resolve()
        raw = self.trace.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if not expected_sha256 or self.sha256 != expected_sha256.lower():
            raise ValueError("offline trace SHA-256 mismatch")
        with self.trace.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not REQUIRED.issubset(reader.fieldnames or []):
                raise ValueError("offline trace is missing required columns")
            rows = list(reader)
        if len(rows) != 504 or {r["site"] for r in rows} != {"1"}:
            raise ValueError("expected one W1 site and 504 trace rows")
        self.rows: dict[str, list[dict]] = {arm: [] for arm in ARMS}
        for row in rows:
            arm = row["arm"]
            if arm not in self.rows:
                raise ValueError("unexpected replay arm")
            when = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            mapped = {key: float(row[key]) for key in NUMBERS}
            mapped.update(site_id="1", local_hour=row["time"], time_basis="dataset_local_hour",
                          arm=arm, solver_status=(row.get("solver_status") or "").strip() or None, flow=row["flow"],
                          command_mode=row["command_mode"], command_flow=row["command_flow"],
                          planned_flow=row["planned_flow"], attribution=(row.get("attribution") or "").strip() or None,
                          fallback=self._bool(row.get("fallback")),
                          optimizer_attributed=self._bool(row.get("optimizer_attributed")))
            mapped["pv_kw"] = mapped.pop("pv_w") / 1000
            mapped["load_kw"] = mapped.pop("load_w") / 1000
            mapped["power_kw"] = mapped.pop("power_w") / 1000
            mapped["command_target_kw"] = mapped.pop("command_target_w") / 1000
            mapped["planned_power_kw"] = mapped.pop("planned_power_w") / 1000
            mapped["index"] = len(self.rows[arm])
            self.rows[arm].append((when, mapped))
        hours = [when for when, _ in self.rows["irradiance"]]
        if len(hours) != 168 or any(len(v) != 168 for v in self.rows.values()):
            raise ValueError("expected 168 hours in each arm")
        if any(hours[i] != hours[0] + timedelta(hours=i) for i in range(168)):
            raise ValueError("local-hour series is not consecutive")
        if any([when for when, _ in series] != hours for series in self.rows.values()):
            raise ValueError("replay arm local hours differ")
        self.start_local_hour = hours[0].strftime("%Y-%m-%d %H:%M:%S")
        self.end_local_hour = hours[-1].strftime("%Y-%m-%d %H:%M:%S")
        self.negative_price_rows = sum(row["sell"] < 0 for _, row in self.rows["irradiance"])

    @staticmethod
    def _bool(value: str | None) -> bool | None:
        # Missing diagnostics are unknown, never inferred from solver_status.
        if value is None or not value.strip():
            return None
        if value not in {"True", "False"}:
            raise ValueError("invalid boolean trace field")
        return value == "True"

    def summary(self) -> dict:
        return {"site_id": "1", "arms": list(ARMS), "hours_per_arm": 168,
                "time_basis": "dataset_local_hour", "start_local_hour": self.start_local_hour,
                "end_local_hour": self.end_local_hour, "source_sha256": self.sha256,
                "source_kind": "offline_strategy_replay", "official_score_claim": False,
                "negative_price_rows": self.negative_price_rows,
                "weather_source": "future_observed_shortwave_proxy_not_archived_API_forecast",
                "scope": "single_site_historical_household_practice_not_online_decision_or_official_settlement"}

    def detail(self, arm: str, index: int) -> dict:
        if arm not in self.rows or not 0 <= index < 168:
            raise KeyError("offline replay step not found")
        row = self.rows[arm][index][1]
        refs = lambda *fields: [f"trace:site_1_trace.csv#/{arm}/{index}/{field}" for field in fields
                                if field not in {"solver_status", "fallback", "optimizer_attributed", "attribution"}
                                or row.get(field) is not None]
        display = lambda value: "未知" if value is None else str(value)
        facts = [
            {"text": f"原始本地小时 {row['local_hour']}；没有设备时区或在线 deadline 语义。",
             "evidence_refs": refs("time")},
            {"text": f"回测臂 {arm}：计划 {row['planned_flow']} {row['planned_power_kw']:.3f} kW；"
                     f"指令 {row['command_mode']} / {row['command_flow']} {row['command_target_kw']:.3f} kW；"
                     f"离线物理流 {row['flow']} {row['power_kw']:.3f} kW。三者不可混称。",
             "evidence_refs": refs("planned_flow", "planned_power_w", "command_mode", "command_flow",
                                   "command_target_w", "flow", "power_w")},
            {"text": f"SOC {row['soc_before']:.2f}% → {row['soc_after']:.2f}%；"
                     f"PV {row['pv_kw']:.3f} kW，负荷 {row['load_kw']:.3f} kW。",
             "evidence_refs": refs("soc_before", "soc_after", "pv_w", "load_w")},
            {"text": f"回测成本 {row['cost_eur']:.4f} EUR；求解状态 {display(row['solver_status'])}；"
                     f"优化器归因 {display(row['optimizer_attributed'])}；"
                     f"归因标签 {display(row['attribution'])}；回退 {display(row['fallback'])}。"
                     "缺失字段为未知，不从求解状态推断归因或回退。"
                     "这不是官方结算或现场执行确认。",
             "evidence_refs": refs("cost_eur", "solver_status", "optimizer_attributed", "attribution", "fallback")},
        ]
        return {"record": row, "facts": facts, "source_sha256": self.sha256,
                "source_kind": "offline_strategy_replay", "time_basis": "dataset_local_hour",
                "capabilities": {"counterfactual": False, "plan_feasibility": False},
                "execution_status": "not_observed", "settlement": None,
                "scope": "W1 单站历史家庭数据离线练习回放；天气为未来实测短波代理，非归档 API 预报。"
                         "无设备时区、无现场指令回读、非在线决策、非官方结算；零流量为离线语义，非硬件验证。"}
