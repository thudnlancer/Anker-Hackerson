"""Quantify the 30-min granularity proxy: SOC divergence (planned vs actual) per site."""
import csv
import statistics
from pathlib import Path

BASE = Path("outputs/local_mock_20261016_run5")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> None:
    rows = list(csv.DictReader(open(BASE / "audit.csv", encoding="utf-8")))
    for site in ("1", "2", "3"):
        srows = [r for r in rows if r["site"] == site]
        div = [fnum(r["soc_divergence_percent"]) for r in srows]
        div = [d for d in div if d is not None]
        if not div:
            print(f"site {site}: no soc_divergence data")
            continue
        n = len(div)
        mean = statistics.mean(div)
        p50 = statistics.median(div)
        # mean abs and sign bias
        mean_abs = statistics.mean(abs(d) for d in div)
        neg = sum(1 for d in div if d < 0)
        print(f"site {site} (n={n}): mean={mean:+.3f} p50={p50:+.3f} "
              f"mean_abs={mean_abs:.3f}  neg_frac={neg/n:.2%}")


if __name__ == "__main__":
    main()
