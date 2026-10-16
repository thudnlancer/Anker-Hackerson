"""Compatibility entry point; implementation lives in scripts/analysis."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / "scripts/analysis/analyze_local_mock_run.py"),
               run_name="__main__")
