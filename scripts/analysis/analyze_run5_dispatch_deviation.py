"""run5 (b): per-round dispatch vs device self-use, price-free (kWh only).

Key signal: for discharge commands, `target_w` is the grid EXPORT target
(target_w = pv + discharge - load when positive).  target_w ~= 0 means the
discharge only covers the load deficit (self-use); target_w >> 0 means the
battery is being drained to the grid (arbitrage export).

Self-use reference (device left in self_consumption mode):
  surplus (pv>load)  -> charge min(max_power, surplus), export the rest
  deficit (load>pv)  -> discharge min(max_power, deficit), import the rest
(SOC limits are applied via observed_soc.)
"""
import csv
from collections import Counter
from pathlib import Path

BASE = Path("outputs/local_mock_20261016_run5")
MAX_POWER_W = 5000.0
CAPACITY_KWH = 14.0
STEP_H = 0.5
EFF = 0.95
EXPORT_EPS = 50.0  # target_w above this = deliberate grid export


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> None:
    rows = list(csv.DictReader(open(BASE / "audit.csv", encoding="utf-8")))
    for site in ("1", "2", "3"):
        srows = [r for r in rows if r["site"] == site]

        cnt = Counter()
        kwh = Counter()

        # self-use reference totals over the whole window (what the device
        # would do if left alone, SOC-aware, tracked through the rounds)
        for r in srows:
            pv = fnum(r["observed_pv"]); ld = fnum(r["observed_load"])
            soc = fnum(r["observed_soc"])
            if pv is None or ld is None or soc is None:
                continue
            net = pv - ld
            cf = r["command_flow"]
            pw = fnum(r["planned_power_w"]) or 0.0
            tw = fnum(r["target_w"]) or 0.0

            # self-use reference (SOC-aware)
            headroom_w = (100.0 - soc) / 100.0 * CAPACITY_KWH * 1000.0 / STEP_H
            avail_w = soc / 100.0 * CAPACITY_KWH * EFF * 1000.0 / STEP_H
            if net > 0:
                ref_charge = min(MAX_POWER_W, net, headroom_w)
                ref_discharge = 0.0
                ref_export = net - ref_charge
                ref_import = 0.0
            elif net < 0:
                ref_charge = 0.0
                ref_discharge = min(MAX_POWER_W, -net, avail_w)
                ref_export = 0.0
                ref_import = -net - ref_discharge
            else:
                ref_charge = ref_discharge = ref_export = ref_import = 0.0

            # our actual command (battery + grid effect)
            if cf == "charge":
                our_charge = pw
                our_discharge = 0.0
                our_export = max(0.0, net - pw)  # surplus left over -> export
                our_import = max(0.0, pw - net)  # charge beyond surplus -> import
                cnt["charge"] += 1
                if pw > net + 1.0:
                    cnt["charge_from_grid"] += 1
                    kwh["charge_from_grid"] += (pw - net) * STEP_H / 1000.0
            elif cf == "discharge":
                our_charge = 0.0
                our_discharge = pw
                our_export = tw  # grid export target
                our_import = max(0.0, -net - pw)
                cnt["discharge"] += 1
                if tw > EXPORT_EPS:
                    cnt["discharge_to_export"] += 1
                    kwh["discharge_to_export"] += tw * STEP_H / 1000.0
                else:
                    cnt["discharge_self_use"] += 1
            else:  # self_consumption / idle / hold -> device self-use
                cnt["idle_or_hold"] += 1
                our_charge = ref_charge
                our_discharge = ref_discharge
                our_export = ref_export
                our_import = ref_import

            # grid deviation (our - self-use); + = more import, - = more export
            dev = (our_import - our_export) - (ref_import - ref_export)
            kwh["dev"] += dev * STEP_H / 1000.0
            kwh["ref_export"] += ref_export * STEP_H / 1000.0
            kwh["ref_import"] += ref_import * STEP_H / 1000.0
            kwh["our_export"] += our_export * STEP_H / 1000.0
            kwh["our_import"] += our_import * STEP_H / 1000.0

        print(f"=== site {site} ===")
        print(f"  rounds: {dict(cnt)}")
        print(f"  discharge_to_export (arbitrage): {kwh['discharge_to_export']:.2f} kWh  n={cnt['discharge_to_export']}")
        print(f"  charge_from_grid (import-to-charge): {kwh['charge_from_grid']:.2f} kWh  n={cnt['charge_from_grid']}")
        print(f"  ---- grid flows (kWh) ----")
        print(f"    self-use would export: {kwh['ref_export']:.2f}")
        print(f"    self-use would import: {kwh['ref_import']:.2f}")
        print(f"    our export:            {kwh['our_export']:.2f}")
        print(f"    our import:            {kwh['our_import']:.2f}")
        print(f"    extra export vs self-use: {kwh['our_export']-kwh['ref_export']:+.2f}")
        print(f"    extra import vs self-use: {kwh['our_import']-kwh['ref_import']:+.2f}")
        print(f"    net grid deviation (+=more import): {kwh['dev']:+.2f}")


if __name__ == "__main__":
    main()
