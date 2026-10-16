"""Compatibility entry point for the read-only B3 calibration audit."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "scripts/online/audit_physical_calibration.py"),
                   run_name="__main__")
