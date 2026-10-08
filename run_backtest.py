from pathlib import Path
import argparse
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).parent / "src"))
from energy_lab.backtest import run_folder

parser = argparse.ArgumentParser(description="Run the transparent offline storage baseline")
parser.add_argument("--data", type=Path, default=Path("data"))
parser.add_argument("--output", type=Path, default=Path("outputs"))
parser.add_argument("--zip", type=Path, help="training-data.zip; extracted to --data before reading")
args = parser.parse_args()
if args.zip:
    args.data.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip) as archive:
        archive.extractall(args.data)
print(run_folder(args.data, args.output))
