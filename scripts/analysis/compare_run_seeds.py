"""Compare run seeds/date_ranges to test whether data is seed-dependent."""
import json
from pathlib import Path

BASE = Path("outputs/local_mock_20261016_run5")


def main() -> None:
    d = json.loads((BASE / "dashboard.json").read_text(encoding="utf-8"))
    runs = d["results"]["runs"]
    for i, r in enumerate(runs):
        t = r.get("total", {})
        print(f"idx{i}: range={r.get('date_range')}  seed={r.get('data_seed')}  "
              f"saving_settled={t.get('saving_settled'):.3f}")
    print()
    bm = d["results"].get("best_mock")
    if bm:
        print("best_mock keys:", list(bm.keys()) if isinstance(bm, dict) else type(bm))
        print(json.dumps(bm, ensure_ascii=False)[:500])


if __name__ == "__main__":
    main()
