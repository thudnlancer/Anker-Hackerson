"""Operator-only quarantine clearance; stop all live runners and verify HA first."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.reconciliation import ReconciliationStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--incident-id", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--confirm-runner-stopped", action="store_true")
    args = parser.parse_args()
    if not args.confirm_runner_stopped:
        parser.error("stop all live runners and pass --confirm-runner-stopped")
    store = ReconciliationStore(args.state)
    store.clear(args.site, args.incident_id, args.operator, args.evidence, args.reason)
    print(f"cleared site={args.site} incident={args.incident_id}; restart runner only after review")


if __name__ == "__main__":
    main()
