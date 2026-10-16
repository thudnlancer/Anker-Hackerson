"""Compatibility entry point for the D1 collector-quality analysis."""

from pathlib import Path
import runpy

from scripts.analysis.analyze_d1_window import analyze


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "scripts/analysis/analyze_d1_window.py"),
                   run_name="__main__")
