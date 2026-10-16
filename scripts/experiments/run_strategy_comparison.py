from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.strategy_backtest import run_strategy_comparison

def main() -> None:
    parser = argparse.ArgumentParser(description="Compare rule dispatch with forecast plus rolling LP/MILP")
    parser.add_argument("--data", type=Path, default=Path("forecast_data"))
    parser.add_argument("--output", type=Path, default=Path("outputs/strategy_comparison"))
    parser.add_argument("--max-sites", type=int, help="limit sites for a quick pilot run")
    parser.add_argument("--site-start", type=int, default=0, help="zero-based site offset for chunk evaluation")
    parser.add_argument("--train-all-sites", action="store_true", help="fit shared models on all sites before evaluating the selected chunk")
    parser.add_argument("--horizon", type=int, default=24)
    parser.add_argument("--train-fraction", type=float, default=0.8)
    parser.add_argument("--weather-modes", default="oracle,persistence")
    parser.add_argument("--soc-min", type=float, default=10.0)
    parser.add_argument("--degradation-cost", type=float, default=0.002)
    parser.add_argument("--terminal-price", type=float, default=None, help="EUR/kWh inventory settlement value at the end of the holdout")
    parser.add_argument("--solver", choices=("lp", "milp"), default="lp")
    parser.add_argument("--workers", type=int, default=1, help="independent site/weather worker processes")
    parser.add_argument("--worker-threads", type=int, default=1, help="BLAS/OpenMP threads per evaluation process; 0 keeps library defaults")
    parser.add_argument("--resume", action="store_true", help="reuse matching completed site checkpoints from an interrupted run")
    args = parser.parse_args()
    weather_modes = tuple(mode.strip() for mode in args.weather_modes.split(",") if mode.strip())
    result = run_strategy_comparison(
        args.data, args.output, max_sites=args.max_sites, site_start=args.site_start,
        train_all_sites=args.train_all_sites, train_fraction=args.train_fraction,
        horizon=args.horizon, soc_min_percent=args.soc_min,
        degradation_cost_eur_kwh=args.degradation_cost, terminal_price=args.terminal_price,
        weather_modes=weather_modes, solver=args.solver, workers=args.workers,
        worker_threads=args.worker_threads,
        resume=args.resume,
    )
    print(json.dumps(result["policies"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
