"""Compatibility entry point for D2 day-block bootstrap analysis."""

from pathlib import Path
import runpy

from scripts.analysis.analyze_dayblock_bootstrap import (
    ALPHAS, analyze_lead, assign_day, bootstrap_ratio, is_daylight, load_rows,
    mae_ratio, mixed_mae, run, verdict,
)


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "scripts/analysis/analyze_dayblock_bootstrap.py"),
                   run_name="__main__")
