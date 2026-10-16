"""Compatibility entry point; implementation lives in scripts/online."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / "scripts/online/capture_local_mock_safety.py"),
               run_name="__main__")
