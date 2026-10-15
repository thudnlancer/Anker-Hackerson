"""Read-only capture of price/current and dated prices. Never stores credentials."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.config import load_runtime_config
from energy_lab.sim_client import SimulatorClient
from energy_lab.price_anchor import anchor_prices
from run_live import price_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cfg = load_runtime_config(args.config)
    sim = SimulatorClient(cfg["sim_base"], cfg["sim_username"], cfg["sim_password"])
    samples = []
    for endpoint, read in (("/api/price/current", sim.current_price),
                           ("/api/price", sim.prices), ("/api/price/current", sim.current_price)):
        started = datetime.now(timezone.utc).isoformat()
        response = read()
        samples.append({"endpoint": endpoint, "requested_at": started,
                        "received_at": datetime.now(timezone.utc).isoformat(), "response": response})
    try:
        _, contract = anchor_prices(price_window(samples[1]["response"]),
                                    samples[0]["response"], samples[2]["response"])
        result = {"unique_current_slot": True, **contract}
    except ValueError as exc:
        result = {"unique_current_slot": False, "reason": str(exc)}
    artifact = {"read_only": True, "writes_performed": 0,
                "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
                "samples": samples, "contract_check": result}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    print("artifact_sha256=" + hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
