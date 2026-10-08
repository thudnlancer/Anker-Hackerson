from __future__ import annotations

from pathlib import Path
from typing import Iterable
import json
import pandas as pd

from .model import Battery, dispatch


def _find(columns: Iterable[str], names: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    time_col = _find(df.columns, ("time", "timestamp", "datetime", "date", "local_hour_time"))
    pv_col = _find(df.columns, ("pv_power", "pv_power_w", "pv_power_avg_w", "solar_power", "solar_power_w", "photovoltaic_power"))
    load_col = _find(df.columns, ("home_load", "load_power", "load_power_w", "load_power_avg_w", "household_load"))
    buy_col = _find(df.columns, ("import_price_eur_kwh", "import_price", "buy_price"))
    sell_col = _find(df.columns, ("export_price_eur_kwh", "export_price", "sell_price"))
    missing = [name for name, col in (("time", time_col), ("pv", pv_col), ("load", load_col), ("import_price", buy_col), ("export_price", sell_col)) if not col]
    if missing:
        raise ValueError(f"{path}: missing required columns {missing}; got {list(df.columns)}")
    out = pd.DataFrame({"time": pd.to_datetime(df[time_col]), "pv": pd.to_numeric(df[pv_col]), "load": pd.to_numeric(df[load_col]), "buy": pd.to_numeric(df[buy_col]), "sell": pd.to_numeric(df[sell_col])})
    # Preserve weather predictors for forecasting experiments while keeping a stable core schema.
    weather_columns = (
        "temperature_c", "relative_humidity_percent", "dew_point_c", "apparent_temperature_c",
        "precipitation_mm", "rain_mm", "snowfall_mm", "pressure_msl_hpa", "surface_pressure_hpa",
        "cloud_cover_percent", "cloud_cover_low_percent", "cloud_cover_mid_percent", "cloud_cover_high_percent",
        "wind_speed_10m_ms", "wind_speed_100m_ms", "wind_gusts_10m_ms", "shortwave_radiation_wm2",
        "direct_radiation_wm2", "diffuse_radiation_wm2", "direct_normal_irradiance_wm2",
        "terrestrial_radiation_wm2", "sunshine_duration_s", "solar_elevation_deg", "solar_azimuth_deg", "is_day",
    )
    for column in weather_columns:
        if column in df.columns:
            out[column] = pd.to_numeric(df[column], errors="coerce")
    return out.sort_values("time").reset_index(drop=True)


def run(df: pd.DataFrame, initial_soc: float = 50.0) -> tuple[pd.DataFrame, dict]:
    battery = Battery(soc=initial_soc)
    rows = []
    for i, row in df.iterrows():
        future = df.loc[i + 1:i + 24, "buy"].tolist()
        flow, power, reason = dispatch(float(row.buy), float(row.sell), float(row.pv), float(row.load), battery.soc, future)
        before = battery.soc
        battery.step(flow, power)
        grid_import = max(0.0, float(row.load) - float(row.pv) + (power if flow == "charge" else -power))
        grid_export = max(0.0, float(row.pv) - float(row.load) - (power if flow == "charge" else 0.0) + (power if flow == "discharge" else 0.0))
        cost = grid_import / 1000 * float(row.buy) - grid_export / 1000 * float(row.sell)
        rows.append({"time": row.time, "flow": flow, "power_w": power, "soc_before": before, "soc_after": battery.soc, "grid_import_kwh": grid_import / 1000, "grid_export_kwh": grid_export / 1000, "cost": cost, "reason": reason})
    result = pd.DataFrame(rows)
    negative_export = df.sell < 0
    negative_export_kwh = (result.loc[negative_export, "grid_export_kwh"]).sum()
    negative_export_loss = (result.loc[negative_export, "grid_export_kwh"] * -df.loc[negative_export, "sell"] ).sum()
    summary = {"steps": len(result), "cost": float(result.cost.sum()), "final_soc": float(battery.soc), "grid_import_kwh": float(result.grid_import_kwh.sum()), "grid_export_kwh": float(result.grid_export_kwh.sum()), "negative_export_kwh": float(negative_export_kwh), "negative_export_loss_eur": float(negative_export_loss), "battery_throughput_kwh": float((result.power_w / 1000).sum())}
    return result, summary


def run_folder(folder: Path, output: Path) -> dict:
    files = sorted(folder.rglob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no CSV files under {folder}")
    summaries = []
    data_warnings: list[str] = []
    output.mkdir(parents=True, exist_ok=True)
    for path in files:
        df = load_csv(path)
        if (df.buy <= 0).any():
            data_warnings.append(f"{path.name}: import_price has {int((df.buy <= 0).sum())} non-positive rows; SOP says purchase prices are positive. Verify source/field semantics before interpreting cost or training a policy.")
        trace, summary = run(df)
        summary["file"] = str(path)
        summaries.append(summary)
        trace.to_csv(output / f"{path.stem}_trace.csv", index=False)
    aggregate = {"files": len(summaries), "total_cost": sum(x["cost"] for x in summaries), "mean_final_soc": sum(x["final_soc"] for x in summaries) / len(summaries), "data_warnings": data_warnings, "summaries": summaries}
    (output / "summary.json").write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    return aggregate
