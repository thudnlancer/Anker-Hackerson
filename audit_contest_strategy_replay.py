"""Independently verify saved 168-hour physical, command and financial traces."""
from pathlib import Path
import argparse
import hashlib
import json

import numpy as np
import pandas as pd


def close(a, b):
    np.testing.assert_allclose(a, b, atol=1e-7, rtol=1e-9)


def audit(root):
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    params = manifest["parameters"]
    for source, digest in manifest["data_sha256"].items():
        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == digest
    count, max_residual, nonoptimal, fallback = 0, 0., 0, 0
    legacy_inferred_command_rows = 0
    expected_aggregates = {}
    k_total = 0.
    for site in summary["sites"]:
        trace = pd.read_csv(root / f"site_{site['site']}_trace.csv")
        raw = pd.read_csv(Path(params["data"]) / f"{site['site']}.csv")
        assert site["start_index"] == int(len(raw) * .8) + params["offset"]
        source = raw.iloc[site["start_index"]:site["start_index"]+168]
        assert len(source) == 168 and len(trace) == 168 * len(site["arms"])
        assert set(trace.arm) == set(site["arms"])
        assert not trace.duplicated(["arm", "time"]).any()
        load = source.load_power_avg_w.to_numpy()
        pv = source.pv_power_avg_w.to_numpy()
        buy = source.import_price_eur_kwh.to_numpy()
        sell = source.export_price_eur_kwh.to_numpy()
        k = float(np.sum(load / 1000 * buy))
        settlement = float(buy.mean())
        close(site["k_eur"], k)
        close(site["settlement_price_eur_kwh"], settlement)
        k_total += k
        recomputed = {}
        for arm, part in trace.groupby("arm", sort=False):
            assert len(part) == 168
            if "command_flow" not in part:
                legacy_inferred_command_rows += len(part)
                part = part.copy()
                part["command_flow"] = np.where(part.command_mode.eq("self_consumption"), "hold",
                                                 np.where(part.command_mode.eq("zero_flow"), "charge", part.command_mode))
            assert pd.to_datetime(part.time).to_list() == pd.to_datetime(source.local_hour_time).to_list()
            assert part.horizon_hours.to_list() == [min(24, 168-i) for i in range(168)]
            close(part[["pv_w", "load_w", "buy", "sell"]], np.c_[pv, load, buy, sell])
            assert np.isfinite(part[["power_w", "soc_before", "soc_after", "cost_eur"]]).all().all()
            close(part.soc_before.iloc[0], 50.)
            close(part.soc_before.iloc[1:], part.soc_after.iloc[:-1])
            assert part.soc_after.between(-1e-7, 100+1e-7).all()
            assert part.power_w.between(-1e-7, 5000+1e-7).all()
            assert part.flow.isin(["idle", "hold", "charge", "discharge"]).all()
            assert part.command_mode.isin(["self_consumption", "zero_flow", "charge", "discharge"]).all()
            assert part.command_flow.isin(["charge", "discharge", "hold", "self_consumption"]).all()
            assert part.command_target_w.between(-1e-7, 5000+1e-7).all()
            charge = np.where(part.flow == "charge", part.power_w, 0.)
            discharge = np.where(part.flow == "discharge", part.power_w, 0.)
            mode = part.command_mode.to_numpy()
            target = part.command_target_w.to_numpy()
            requested_charge = np.where(mode == "self_consumption", np.maximum(pv-load, 0.),
                                        np.where(mode == "charge", target, 0.))
            requested_discharge = np.where(mode == "self_consumption", np.maximum(load-pv, 0.),
                                           np.where(mode == "discharge", np.maximum(target-pv+load, 0.), 0.))
            close(charge, np.minimum(np.minimum(requested_charge, 5000.), (100-part.soc_before)*140/.95))
            close(discharge, np.minimum(np.minimum(requested_discharge, 5000.), part.soc_before*140))
            residual = (part.soc_after-part.soc_before)*.14 - (.95*charge-discharge)/1000
            close(residual, 0.)
            max_residual = max(max_residual, float(np.abs(residual).max()))
            net = (load+charge-pv-discharge)/1000
            imports, exports = np.maximum(net, 0.), np.maximum(-net, 0.)
            close(part.grid_import_kwh, imports)
            close(part.grid_export_kwh, exports)
            cost = imports*buy-exports*sell
            close(part.cost_eur, cost)
            close(part.throughput_kwh, (charge+discharge)/1000)
            close(part.negative_export_loss_eur, exports*np.maximum(-sell, 0.))
            if arm == "self_consumption":
                assert (mode == "self_consumption").all()
                # New artifacts record the explicit mode switch; legacy ones
                # still carry the pre-rename "hold" command label (R6).
                assert part.command_flow.isin(["self_consumption", "hold"]).all()
            else:
                assert (part.fallback == part.solver_status.ne("optimal")).all()
                assert part.loc[part.fallback, "command_mode"].eq("self_consumption").all()
                assert part.loc[part.fallback, "command_flow"].isin(["self_consumption", "hold"]).all()
                assert part.loc[(~part.fallback) & (part.command_mode == "zero_flow"), "command_flow"].eq("charge").all()
            inventory = float(part.soc_after.iloc[-1])*.14*settlement
            recomputed[arm] = {"operating_cost_eur": float(cost.sum()),
                               "inventory_value_eur": inventory,
                               "settled_cost_eur": float(cost.sum())-inventory,
                               "throughput_kwh": float(part.throughput_kwh.sum()),
                               "internal_wear_eur": float(part.throughput_kwh.sum())*params["wear"],
                               "fallback_steps": int(part.fallback.sum()),
                               "nonoptimal_solves": int(part.nonoptimal_solves.sum())}
            close(site["arms"][arm]["final_soc"], part.soc_after.iloc[-1])
            fallback += int(part.fallback.sum())
            nonoptimal += int(part.nonoptimal_solves.sum())
            count += len(part)
        base = recomputed["self_consumption"]
        for arm, item in recomputed.items():
            item["operating_saving_eur"] = base["operating_cost_eur"]-item["operating_cost_eur"]
            item["settled_saving_eur"] = base["settled_cost_eur"]-item["settled_cost_eur"]
            item["settled_saving_after_internal_wear_eur"] = (
                item["settled_saving_eur"]-item["internal_wear_eur"]+base["internal_wear_eur"])
            for key, value in item.items():
                close(site["arms"][arm][key], value)
                totals = expected_aggregates.setdefault(arm, {})
                totals[key] = totals.get(key, 0.) + value
            close(site["arms"][arm]["saving_settled_percent_estimate"], item["settled_saving_eur"]/k*100)
    close(summary["aggregate"]["k_eur"], k_total)
    for arm, item in expected_aggregates.items():
        for key, value in item.items():
            close(summary["aggregate"]["arms"][arm][key], value)
        close(summary["aggregate"]["arms"][arm]["saving_settled_percent_estimate"], item["settled_saving_eur"]/k_total*100)
    result = {"passed": True, "audited_rows": count, "sites": len(summary["sites"]),
              "legacy_inferred_command_rows": legacy_inferred_command_rows,
              "max_energy_residual_kwh": max_residual, "nonoptimal_solves": nonoptimal,
              "fallback_steps": fallback, "all_solves_optimal": nonoptimal == 0,
              "scope": "input_hashes_command_execution_energy_grid_cost_inventory_K_and_weighted_percentage",
              "not_certified": "official_ledger_live_readback_forecast_accuracy_or_real_time_performance"}
    (root / "audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outputs", type=Path, nargs="+")
    for path in parser.parse_args().outputs:
        print(path, json.dumps(audit(path)))
