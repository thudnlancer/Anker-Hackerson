"""Dump the full dashboard (contest + results history) to JSON for run5 attribution."""
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
    d = sim.get("/api/dashboard")
    out = Path("outputs/local_mock_20261016_run5/dashboard.json")
    out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    results = d.get("results", {})
    items = results.get("items", []) if isinstance(results, dict) else results
    print(f"results keys: {list(results.keys()) if isinstance(results, dict) else 'list'}")
    print(f"results count: {results.get('count') if isinstance(results, dict) else len(items)}")
    for r in items:
        ended = r.get("ended_at")
        sites = r.get("sites", [])
        summary = []
        for s in sites:
            m = s.get("metrics", {})
            summary.append((s.get("site_id"), round(m.get("saving_settled", 0), 4),
                            round(m.get("saving_settled_percent", 0), 3)))
        print(f"  ended_at={ended}  n_sites={len(sites)}  saving_settled={summary}")


if __name__ == "__main__":
    main()
