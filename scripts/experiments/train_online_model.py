"""Train a model whose weather inputs are present in the competition API.

This deliberately uses only exact API field names.  The historical CSV has
many richer weather columns, but fields without an exact online counterpart
are excluded rather than aliased to irradiance or filled with zero.
"""

from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.forecast import fit_shared_model, load_weather_csv


ONLINE_TRAINING_WEATHER = ("temperature_c", "cloud_cover_percent")


def train(data_dir: Path, model_dir: Path, metrics_path: Path) -> dict:
    paths = sorted(data_dir.rglob("*.csv"), key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem)
    if not paths:
        raise FileNotFoundError(f"no CSV files under {data_dir}")
    frames = []
    for path in paths:
        source = load_weather_csv(path)
        keep = ["time", "pv", "load", *ONLINE_TRAINING_WEATHER]
        frames.append(source[[column for column in keep if column in source.columns]].copy())
        missing = [column for column in ONLINE_TRAINING_WEATHER if column not in frames[-1]]
        if missing:
            raise ValueError(f"{path}: missing exact API-compatible fields {missing}")
    model_dir.mkdir(parents=True, exist_ok=True)
    reports = {}
    for target in ("pv", "load"):
        model, report = fit_shared_model(frames, target)
        report["online_weather_contract"] = list(ONLINE_TRAINING_WEATHER)
        report["excluded_weather_fields"] = "all historical fields without an exact API name"
        model_path = model_dir / f"shared_{target}.joblib"
        import joblib
        joblib.dump(model, model_path)
        reports[target] = {"model": str(model_path), **report}
    output = {"sites": len(frames), "rows": sum(len(frame) for frame in frames),
              "weather_contract": list(ONLINE_TRAINING_WEATHER),
              "history_requirement": "at least 168 hourly observations per station before model inference",
              "models": reports}
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("forecast_data"))
    parser.add_argument("--model-dir", type=Path, default=Path("models/online_api"))
    parser.add_argument("--metrics", type=Path, default=Path("outputs/online_api_model_metrics.json"))
    args = parser.parse_args()
    print(json.dumps(train(args.data, args.model_dir, args.metrics), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
