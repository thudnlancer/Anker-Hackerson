"""Independent physical accounting checks and a causal self-use baseline."""
import numpy as np
import pandas as pd

from .decision import self_consumption
from .model import Battery
from .strategy_backtest import _policy_step


def self_use_trace(raw, site, split, *, soc_min=10., initial_soc=50.):
    battery = Battery(soc=initial_soc)
    rows = []
    for row in raw.iloc[split:].itertuples(index=False):
        flow, power = self_consumption(row.pv, row.load)
        result = _policy_step(battery, flow, power, row, row.buy, row.sell, soc_min)
        rows.append({"site": str(site), "time": row.time, "policy": "self_consumption",
                     "weather_mode": "persistence", **result})
    return pd.DataFrame(rows)


def audit_trace(trace, raw, *, soc_min=10., initial_soc=50., dt=1.):
    """Recompute conservation/costs from inputs, independent of summaries."""
    assert not trace.duplicated(["site", "time", "policy"]).any()
    checked = 0
    max_energy = max_grid = max_cost = 0.
    for (_, _), group in trace.groupby(["site", "policy"], sort=False):
        g = group.sort_values("time")
        source = raw.set_index("time").loc[pd.to_datetime(g.time)]
        p = g.power_w.to_numpy()
        before, after = g.soc_before.to_numpy(), g.soc_after.to_numpy()
        imp, exp = g.grid_import_kwh.to_numpy(), g.grid_export_kwh.to_numpy()
        assert np.isfinite(np.c_[p, before, after, imp, exp, g.cost_eur, g.throughput_kwh, g.negative_export_loss_eur]).all()
        assert g.flow.isin(["charge", "discharge", "hold"]).all()
        assert (p >= -1e-7).all() and (p <= 5000+1e-7).all()
        assert (after >= soc_min-1e-7).all() and (after <= 100+1e-7).all()
        assert np.isclose(before[0], initial_soc)
        np.testing.assert_allclose(before[1:], after[:-1], atol=1e-8, rtol=0)
        assert pd.to_datetime(g.time).diff().dropna().eq(pd.Timedelta(hours=dt)).all()
        c = np.where(g.flow.eq("charge"), p, 0.)*dt/1000
        d = np.where(g.flow.eq("discharge"), p, 0.)*dt/1000
        assert np.all(np.abs(p[g.flow.eq("hold")]) < 1e-8)
        assert min(imp.min(), exp.min()) >= -1e-8 and np.minimum(imp, exp).max() < 1e-8
        energy = (after-before)*.14 - .95*c+d
        grid = source.pv.to_numpy()*dt/1000 + d+imp-source.load.to_numpy()*dt/1000-c-exp
        cost = g.cost_eur.to_numpy()-(imp*source.buy.to_numpy()-exp*source.sell.to_numpy())
        np.testing.assert_allclose(g.throughput_kwh, c+d, atol=1e-8, rtol=0)
        np.testing.assert_allclose(g.negative_export_loss_eur, exp*np.maximum(0,-source.sell.to_numpy()), atol=1e-8, rtol=0)
        max_energy = max(max_energy, float(np.abs(energy).max()))
        max_grid = max(max_grid, float(np.abs(grid).max()))
        max_cost = max(max_cost, float(np.abs(cost).max()))
        checked += len(g)
    assert max(max_energy, max_grid, max_cost) < 1e-7
    return {"passed": True, "rows": checked, "max_energy_residual_kwh": max_energy,
            "max_grid_residual_kwh": max_grid, "max_cost_residual_eur": max_cost}


def metrics(trace, wear=.02, terminal=.1):
    result = trace.groupby(["site", "policy"]).agg(
        operating_cost_eur=("cost_eur", "sum"), final_soc=("soc_after", "last"),
        throughput_kwh=("throughput_kwh", "sum"), negative_export_loss_eur=("negative_export_loss_eur", "sum"))
    result["inventory_corrected_cost_eur"] = result.operating_cost_eur-result.final_soc*.14*terminal
    result["cost_including_wear_eur"] = result.inventory_corrected_cost_eur+result.throughput_kwh*wear
    return result


def compare(per_site, optimized, baseline="self_consumption"):
    base, opt = per_site.xs(baseline, level="policy"), per_site.xs(optimized, level="policy")
    assert base.index.equals(opt.index)
    savings = base-opt
    return {"baseline": baseline, "optimized": optimized, "sites": len(base),
            "operating_savings_eur": float(savings.operating_cost_eur.sum()),
            "inventory_corrected_savings_eur": float(savings.inventory_corrected_cost_eur.sum()),
            "savings_including_wear_eur": float(savings.cost_including_wear_eur.sum()),
            "positive_sites_including_wear": int(savings.cost_including_wear_eur.gt(1e-8).sum()),
            "min_site_saving_including_wear_eur": float(savings.cost_including_wear_eur.min()),
            "median_site_saving_including_wear_eur": float(savings.cost_including_wear_eur.median())}
