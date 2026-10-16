"""Compatibility entry point for the supervised B1 zero-flow verifier."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "scripts/online/run_zero_flow_verification.py"),
                   run_name="__main__")
