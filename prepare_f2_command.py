"""Print the explicit F2 command only after a separately signed manifest is authorized.

This tool never starts replay. The integration owner must approve a new signed
manifest and supply its exact SHA-256; the current unsigned F1 manifest fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def command_from_manifest(manifest: dict, *, data: Path, output: Path, workers: int) -> list[str]:
    signature = manifest["signature"]
    if not (manifest.get("f2_authorized") is True
            and signature.get("f2_authorization") is True
            and signature.get("integration_owner")
            and signature.get("signed_at")):
        raise ValueError("F1 integration signature/F2 authorization is incomplete")
    p = manifest["parameters"]
    if (p["discharge_efficiency"] != 1.0 or p["online_time_step"] is None
            or p["capacity_kwh"] != 14.0 or p["max_power_w"] != 5000.0
            or p["mip_rel_gap"] != 1e-9):
        raise ValueError("unsupported frozen model convention")
    if p["pv_forecast"]["online_scope"].find("persistence-only") < 0:
        raise ValueError("online PV scope changed; review F1")
    return ["python", "run_contest_strategy_replay.py", "--data", str(data),
            "--output", str(output), "--stage", "4", "--max-sites", "100",
            "--workers", str(workers), "--wear", str(p["degradation_cost_eur_per_kwh"]),
            "--solver-time-limit", str(p["solver_seconds"]),
            "--hold-mode", p["successful_plan_idle"],
            "--terminal-price-mode", p["terminal_price"]["rule"],
            "--capacity-kwh", str(p["capacity_kwh"]), "--max-power-w", str(p["max_power_w"]),
            "--soc-min", str(p["soc_min_percent"]), "--soc-max", str(p["soc_max_percent"]),
            "--charge-efficiency", str(p["charge_efficiency"]),
            "--discharge-efficiency", str(p["discharge_efficiency"])]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.expected_sha256.lower():
        parser.error("manifest SHA-256 mismatch")
    if args.workers < 1:
        parser.error("workers must be positive")
    command = command_from_manifest(json.loads(raw), data=args.data,
                                    output=args.output, workers=args.workers)
    print(json.dumps({"manifest_sha256": args.expected_sha256.lower(),
                      "command_argv": command, "started": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
