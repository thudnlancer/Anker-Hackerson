"""Dump the contest history (hourly price/PV/load/weather) for all 3 sites to JSON."""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.config import load_runtime_config, require_runtime_fields
from energy_lab.sim_client import SimulatorClient


def main() -> None:
    cfg = load_runtime_config(Path("config/local/local_online_config.json"), "dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    sim = SimulatorClient(cfg["sim_base"], cfg["sim_username"], cfg["sim_password"],
                          proxy_mode=cfg["proxy_mode"])
    outdir = Path("outputs/local_mock_20261016_run5")
    for site in (1, 2, 3):
        h = sim.get("/api/contest/history", site=site)
        pts = h["points"]
        p0, pn = pts[0], pts[-1]
        print(f"site={site}: days={h['days']} interval_hours={h['interval_hours']} n_points={len(pts)}")
        print(f"  first time {p0['time']}  last time {pn['time']}")
        print(f"  price fields: {[k for k in p0 if 'price' in k]}")
        for k in ("import_price_eur_kwh", "export_price_eur_kwh", "pv_power_avg_w", "load_power_avg_w"):
            print(f"  {k}: first={p0.get(k)}  last={pn.get(k)}")
        (outdir / f"contest_history_site{site}.json").write_text(
            json.dumps(h, ensure_ascii=False), encoding="utf-8")
    print("saved")


if __name__ == "__main__":
    main()
