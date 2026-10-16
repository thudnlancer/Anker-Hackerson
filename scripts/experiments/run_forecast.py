from pathlib import Path
import argparse
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.forecast import train_folder

parser = argparse.ArgumentParser(description="Train per-site PV/load forecast baselines")
parser.add_argument("--data", type=Path, default=Path("data"))
parser.add_argument("--zip", type=Path, help="training ZIP; extracted to --data before training")
parser.add_argument("--models", type=Path, default=Path("models"))
parser.add_argument("--metrics", type=Path, default=Path("outputs/local_artifacts/forecast_metrics.json"))
args = parser.parse_args()
if args.zip:
    args.data.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip) as archive:
        archive.extractall(args.data)
print(train_folder(args.data, args.models, args.metrics))
