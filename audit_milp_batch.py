"""Compatibility entry point for the persisted MILP batch audit."""

from scripts.analysis.audit_milp_batch import audit


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("outputs/grid_10site_soc10_deg002_milp_persistence"))
    parser.add_argument("--sites", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(audit(args.input, args.sites), indent=2))
