"""Compatibility entry point; implementation lives in scripts/online."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / "scripts/online/run_local_mock.py"),
               run_name="__main__")
