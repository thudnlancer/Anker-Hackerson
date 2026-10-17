"""Offline test field: replay optimizer strategies over built epoch datasets.

For every epoch x site x strategy x forecast mode, simulates the 15-minute
rolling loop: build a causal forecast window, solve with the registered
strategy, execute only the first step against realized power, account grid
energy at realized prices, and roll forward.  The arbiter is settled cost vs a
pure self-consumption baseline simulated with identical battery physics.

    python scripts/offline/run_epoch_replay.py --epochs-dir data/epochs \
        --strategies milp gradient --forecast-modes persistence perfect \
        --output outputs/epoch_replay_<tag>

Battery physics mirror the online contract: charge efficiency 0.95, discharge
1.0, device SOC range 0..100 %, max power 5 kW.  Forecasts are causal:
"persistence" holds current pv/load flat; "perfect" uses realized future
values (upper bound, clearly labeled); "v16" predicts load with the frozen
V16 forecaster (18 realized 15-min samples -> 12 ticks, refreshed per
--v16-refresh-ticks, flat extension beyond the 3h prediction block) while PV
stays persistence.  V16 mode requires the heavy deps and git-ignored weights
(run under .venv-forecast).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.strategies import STRATEGIES, StrategyRequest, get_strategy

DT_H = 0.25
CAPACITY_KWH = 14.0
MAX_POWER_W = 5000.0
ETA_CHARGE = 0.95
HORIZON_STEPS = 96


def code_revision() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, timeout=10,
                             cwd=Path(__file__).resolve().parents[2])
        return out.stdout.decode().strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def self_use_step(pv_w: float, load_w: float, energy_kwh: float) -> tuple[float, float]:
    """Self-consumption physics: (battery power W +charge, new energy kWh)."""
    pc_w = min(max(0.0, pv_w - load_w), MAX_POWER_W,
               max(0.0, CAPACITY_KWH - energy_kwh) / (ETA_CHARGE * DT_H) * 1000)
    pd_w = min(max(0.0, load_w - pv_w), MAX_POWER_W,
               max(0.0, energy_kwh) * 1000 / DT_H)
    new_energy = min(CAPACITY_KWH, max(0.0, energy_kwh + (ETA_CHARGE * pc_w - pd_w) * DT_H / 1000))
    return pc_w - pd_w, new_energy


def execute_step(flow: str, power_w: float, energy_kwh: float) -> tuple[float, float]:
    """Apply a planned charge/discharge action with device clipping (applied W)."""
    if flow == "charge":
        applied = min(power_w, MAX_POWER_W,
                      max(0.0, CAPACITY_KWH - energy_kwh) / (ETA_CHARGE * DT_H) * 1000)
        return applied, min(CAPACITY_KWH, energy_kwh + ETA_CHARGE * applied * DT_H / 1000)
    if flow == "discharge":
        applied = min(power_w, MAX_POWER_W, max(0.0, energy_kwh) * 1000 / DT_H)
        return applied, max(0.0, energy_kwh - applied * DT_H / 1000)
    raise ValueError(f"execute_step only handles charge/discharge, got {flow!r}")


def grid_cost(pv_w, load_w, batt_w, buy, sell) -> float:
    grid_w = load_w - pv_w + batt_w
    return (max(grid_w, 0.0) * buy - max(-grid_w, 0.0) * sell) * DT_H / 1000


class V16LoadCache:
    """Rolling cache around the V16 load forecaster (15-min granularity).

    V16 consumes the last 18 realized 15-min load samples and predicts the
    next 12.  Forecasts refresh every ``refresh_ticks`` or when the cached
    block runs out; the current tick stays the realized value and window
    steps beyond the 12-tick prediction hold the last predicted value
    (documented flat extension).  PV is not V16's job: callers pair this
    with persistence PV.
    """

    def __init__(self, forecaster, refresh_ticks: int = 4):
        self.forecaster = forecaster
        self.refresh_ticks = max(1, int(refresh_ticks))
        self._made_at = -10**9
        self._preds = np.zeros(0)
        self.calls = 0

    def window(self, load: np.ndarray, t: int, h: int) -> np.ndarray:
        stale = (t - self._made_at >= self.refresh_ticks
                 or t + 1 > self._made_at + len(self._preds))
        if stale:
            history = load[max(0, t - 17):t + 1]
            if len(history) < 18:
                history = np.concatenate([np.full(18 - len(history), load[0]), history])
            self._preds = np.clip(np.asarray(
                self.forecaster.forecast_w(np.clip(history, 0.0, None), steps=12),
                dtype=float), 0.0, None)
            self._made_at = t
            self.calls += 1
        out = np.empty(h)
        out[0] = load[t]  # current tick is realized, not predicted
        for j in range(1, h):
            idx = t + j - self._made_at - 1
            out[j] = self._preds[min(idx, len(self._preds) - 1)]
        return out


def _export_evidence(evidence: dict, request, plan, actual_pv_w: float,
                     actual_load_w: float, soc_now: float, forecast_mode: str) -> bool:
    """Emit one DecisionIn record for an optimal full-horizon replay solve.

    Reuses the pure shadow exporter, so records reconcile against the same
    pinned MILP source hash.  Non-exportable solves (non-optimal, gradient
    heuristics, hash drift) count as skipped, never fabricated.
    """
    from types import SimpleNamespace
    from energy_lab.explainability_evidence import (
        EvidenceExportError, build_explainability_record)
    first = plan.plan.iloc[0]
    flow = str(first.flow)
    mapping = SimpleNamespace(
        representable=True,
        flow=flow if flow in ("charge", "discharge") else "self_consumption",
        target_w=float(first.charge_w if flow == "charge"
                       else first.discharge_w if flow == "discharge" else 0.0))
    try:
        record = build_explainability_record(
            identity=evidence["identity"], observed_at=evidence["observed_at"],
            state={"solar_power": actual_pv_w, "home_load": actual_load_w, "soc": soc_now},
            times=request.times, pv_w=request.pv_w, load_w=request.load_w,
            import_price=request.import_price, export_price=request.export_price,
            result=plan, mapping=mapping,
            forecast_mode=f"epoch_replay_{forecast_mode}",
            capacity_kwh=request.capacity_kwh, max_power_w=request.max_power_w,
            soc_min=request.soc_min, soc_max=request.soc_max, input_kind="mixed")
    except EvidenceExportError:
        return False
    evidence["sink"](record)
    return True


def replay_site(frame: pd.DataFrame, strategy_name: str, forecast_mode: str,
                solver_time_limit: float, load_forecaster=None,
                v16_refresh_ticks: int = 4, evidence=None) -> tuple[pd.DataFrame, dict]:
    n = len(frame)
    times = pd.Series(pd.to_datetime(frame["time"]))
    pv = frame["pv_w"].to_numpy(dtype=float)
    load = frame["load_w"].to_numpy(dtype=float)
    buy = frame["import_price"].to_numpy(dtype=float)
    sell = frame["export_price"].to_numpy(dtype=float)
    optimizer = get_strategy(strategy_name)
    v16_cache = None
    if forecast_mode == "v16":
        if load_forecaster is None:
            raise ValueError("forecast mode 'v16' requires a load_forecaster")
        v16_cache = V16LoadCache(load_forecaster, v16_refresh_ticks)

    energy = float(frame["soc_percent"].iloc[0]) / 100 * CAPACITY_KWH
    base_energy = energy
    rows = []
    cost = base_cost = 0.0
    nonoptimal = 0
    for t in range(n):
        h = min(HORIZON_STEPS, n - t)
        if forecast_mode == "persistence":
            f_pv = np.full(h, pv[t])
            f_load = np.full(h, load[t])
        elif forecast_mode == "perfect":
            f_pv = pv[t:t + h].copy()
            f_load = load[t:t + h].copy()
        elif forecast_mode == "v16":
            f_pv = np.full(h, pv[t])  # V16 is load-only; PV stays persistence
            f_load = v16_cache.window(load, t, h)
        else:
            raise ValueError(f"unknown forecast mode {forecast_mode}")
        soc_now = min(100.0, max(0.0, energy / CAPACITY_KWH * 100))  # float drift guard
        request = StrategyRequest(times=times.iloc[t:t + h].reset_index(drop=True),
                                  pv_w=f_pv, load_w=f_load,
                                  import_price=buy[t:t + h], export_price=sell[t:t + h],
                                  soc_percent=soc_now,
                                  step_hours=DT_H, time_limit_seconds=solver_time_limit)
        plan = optimizer.solve(request)
        nonoptimal += int(plan.status != "optimal")
        if evidence is not None and t % evidence["every"] == 0 and h == HORIZON_STEPS:
            key = "exported" if _export_evidence(
                evidence, request, plan, pv[t], load[t], soc_now, forecast_mode) else "skipped"
            evidence[key] += 1
        first = plan.plan.iloc[0]
        flow = str(first.flow)
        power = float(first.charge_w if flow == "charge"
                      else first.discharge_w if flow == "discharge" else 0.0)
        if flow in ("idle", "hold"):
            batt_w, energy = self_use_step(pv[t], load[t], energy)
        else:
            batt_signed, energy = execute_step(flow, power, energy)
            batt_w = batt_signed if flow == "charge" else -batt_signed
        base_w, base_energy = self_use_step(pv[t], load[t], base_energy)
        cost += grid_cost(pv[t], load[t], batt_w, buy[t], sell[t])
        base_cost += grid_cost(pv[t], load[t], base_w, buy[t], sell[t])
        rows.append({"time": frame["time"].iloc[t], "flow": flow, "battery_w": batt_w,
                     "soc_end": energy / CAPACITY_KWH * 100,
                     "solver_status": plan.status,
                     "cost_eur": grid_cost(pv[t], load[t], batt_w, buy[t], sell[t]),
                     "baseline_cost_eur": grid_cost(pv[t], load[t], base_w, buy[t], sell[t])})
    trace = pd.DataFrame(rows)
    summary = {"strategy": strategy_name, "forecast_mode": forecast_mode,
               "ticks": n, "cost_eur": round(cost, 6), "baseline_cost_eur": round(base_cost, 6),
               "saving_eur": round(base_cost - cost, 6),
               "saving_percent": (round((base_cost - cost) / abs(base_cost) * 100, 4)
                                  if abs(base_cost) > 1e-9 else None),
               "nonoptimal_solves": nonoptimal,
               "final_soc_percent": round(energy / CAPACITY_KWH * 100, 2),
               "baseline_final_soc_percent": round(base_energy / CAPACITY_KWH * 100, 2)}
    if v16_cache is not None:
        summary["v16_forecast_calls"] = v16_cache.calls
    return trace, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--epochs-dir", type=Path, default=Path("data/epochs"))
    parser.add_argument("--epochs", nargs="*", default=None,
                        help="epoch directory names; default: all epoch_* dirs")
    parser.add_argument("--strategies", nargs="+", default=["milp"],
                        choices=sorted(STRATEGIES))
    parser.add_argument("--forecast-modes", nargs="+", default=["persistence"],
                        choices=("persistence", "perfect", "v16"))
    parser.add_argument("--v16-refresh-ticks", type=int, default=4,
                        help="re-run the V16 load forecaster every this many 15-min "
                             "ticks (default 4 = hourly); needs torch/chronos deps and "
                             "the git-ignored weights (run under .venv-forecast)")
    parser.add_argument("--evidence-run-id", default=None,
                        help="also export explainability DecisionIn JSONL "
                             "(evidence-decisions.jsonl in --output) under this run_id; "
                             "only optimal full-horizon MILP solves are exportable")
    parser.add_argument("--evidence-every", type=int, default=4,
                        help="export every Nth tick (default 4 = hourly)")
    parser.add_argument("--solver-time-limit", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    epoch_dirs = sorted(args.epochs_dir.glob("epoch_*"))
    if args.epochs:
        epoch_dirs = [args.epochs_dir / name for name in args.epochs]
    if not epoch_dirs or not all(d.is_dir() for d in epoch_dirs):
        parser.error("no epoch datasets found")

    args.output.mkdir(parents=True, exist_ok=False)
    forecaster = None
    if "v16" in args.forecast_modes:
        from energy_lab.load_forecast_v16 import LoadForecastV16
        forecaster = LoadForecastV16()
        if not forecaster.available():
            parser.error("forecast mode 'v16' needs the torch/chronos deps and the "
                         "git-ignored weights; run under .venv-forecast (see "
                         "models/load_forecast_v16/README.md)")
    all_summaries = []
    started = perf_counter()
    evidence_stream = None
    observed_at = datetime.now(timezone.utc).isoformat()
    if args.evidence_run_id:
        evidence_stream = (args.output / "evidence-decisions.jsonl").open("x", encoding="utf-8")

        def sink(record):
            evidence_stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False)
                                  + "\n")
    try:
        for epoch_dir in epoch_dirs:
            for site_csv in sorted(epoch_dir.glob("site_*.csv")):
                frame = pd.read_csv(site_csv)
                for strategy in args.strategies:
                    for mode in args.forecast_modes:
                        evidence = None
                        if evidence_stream is not None:
                            evidence = {"every": max(1, args.evidence_every),
                                        "identity": {"run_id": args.evidence_run_id,
                                                     "site_id": f"{epoch_dir.name}:{site_csv.stem}:{strategy}:{mode}",
                                                     "input_kind": "mixed"},
                                        "observed_at": observed_at, "sink": sink,
                                        "exported": 0, "skipped": 0}
                        trace, summary = replay_site(frame, strategy, mode,
                                                     args.solver_time_limit,
                                                     load_forecaster=forecaster,
                                                     v16_refresh_ticks=args.v16_refresh_ticks,
                                                     evidence=evidence)
                        if evidence is not None:
                            summary["evidence_exported"] = evidence["exported"]
                            summary["evidence_skipped"] = evidence["skipped"]
                        tag = f"{epoch_dir.name}_{site_csv.stem}_{strategy}_{mode}"
                        trace.to_csv(args.output / f"trace_{tag}.csv", index=False)
                        summary.update({"epoch": epoch_dir.name, "site": site_csv.stem})
                        all_summaries.append(summary)
                        print(json.dumps({k: summary[k] for k in
                                          ("epoch", "site", "strategy", "forecast_mode",
                                           "saving_eur", "saving_percent", "nonoptimal_solves")},
                                         ensure_ascii=False), flush=True)
    finally:
        if evidence_stream is not None:
            evidence_stream.close()
    report = {"code_sha256": code_revision(), "writes_performed": 0,
              "official_score_claim": False,
              "arbiter": "settled cost vs self-consumption baseline, identical physics",
              "elapsed_seconds": round(perf_counter() - started, 2),
              "summaries": all_summaries}
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    print(f"wrote {len(all_summaries)} summaries -> {args.output / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
