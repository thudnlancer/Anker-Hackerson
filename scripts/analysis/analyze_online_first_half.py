import pandas as pd
import numpy as np

path = "outputs/local_mock_20261016_run4/audit.csv"

# Round-169 rows carry an extra trailing empty column; skip malformed rows then
# keep only the valid first half (round_generation <= 168).
df = pd.read_csv(path, on_bad_lines="skip")
df = df[df["round_generation"] <= 168].copy()
print("rows:", len(df), "rounds:", df["round_generation"].nunique(),
      "sites:", sorted(df["site"].unique()))

# numeric coercion
for c in ["observed_soc", "observed_pv", "observed_load", "planned_power_w",
          "target_w", "realized_soc", "soc_divergence_percent", "mip_gap",
          "solve_elapsed_seconds", "round_elapsed_seconds"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")

print("\n=== status distribution ===")
print(df["status"].value_counts(dropna=False))

print("\n=== planned_flow distribution (all rows) ===")
print(df["planned_flow"].value_counts(dropna=False))

print("\n=== planned_flow by status==dispatched (written rows) ===")
disp = df[df["status"] == "dispatched"]
print(disp["planned_flow"].value_counts(dropna=False))

print("\n=== solver_status distribution ===")
print(df["solver_status"].value_counts(dropna=False))

print("\n=== accepted_incumbent (dispatched rows) ===")
print(disp["accepted_incumbent"].value_counts(dropna=False))

print("\n=== writes_performed / acknowledged ===")
print("writes_performed sum:", df["writes_performed"].sum())
print("write_requests_attempted sum:", df["write_requests_attempted"].sum())
print("write_requests_acknowledged sum:", df["write_requests_acknowledged"].sum())
print("requires_reconciliation True:", df["requires_reconciliation"].sum())

print("\n=== per-site SOC (observed) ===")
g = df.groupby("site")["observed_soc"]
print(g.agg(["first", "last", "min", "max"]))

print("\n=== planned_power_w stats (dispatched, by flow) ===")
for flow, sub in disp.groupby("planned_flow"):
    print(f"{flow}: n={len(sub)} min={sub['planned_power_w'].min():.0f} "
          f"max={sub['planned_power_w'].max():.0f} mean={sub['planned_power_w'].mean():.0f} "
          f"|power|>=4900: {(sub['planned_power_w'].abs()>=4900).sum()}")

print("\n=== flow by hour of device_time (dispatched) ===")
disp = disp.copy()
disp["hour"] = pd.to_datetime(disp["device_time"]).dt.hour
piv = disp.pivot_table(index="hour", columns="planned_flow", values="site", aggfunc="count", fill_value=0)
print(piv.to_string())

print("\n=== SOC bounds check ===")
print("observed_soc <0:", (df["observed_soc"] < 0).sum(),
      ">100:", (df["observed_soc"] > 100).sum())

print("\n=== round_elapsed_seconds stats ===")
print(df["round_elapsed_seconds"].describe())

print("\n=== solve_elapsed_seconds stats ===")
print(df["solve_elapsed_seconds"].describe())
