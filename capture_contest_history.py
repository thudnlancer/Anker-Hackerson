"""Read-only contest/history fetch, strict validation, and token-free cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.online_contract import OnlineContractError, cache_contest_history
from energy_lab.sim_client import SimulatorClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("local_online_config.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/contest_history"))
    parser.add_argument("--sites", default="1,2,3")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))["environment_machine"]
    client = SimulatorClient(config["sim_base"], config["sim_username"], config["sim_password"])
    report = {"writes_performed": 0, "token_saved": False, "sites": {}}
    try:
        client.login()
    except Exception as exc:
        report["login_error"] = str(exc).split(":")[0]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    for raw_site in args.sites.split(","):
        site = int(raw_site.strip())
        try:
            payload = client.contest_history(site)
            path = cache_contest_history(args.cache_dir, payload, requested_site=site)
            report["sites"][str(site)] = {"status": "cached", "path": str(path)}
        except OnlineContractError as exc:
            report["sites"][str(site)] = {"status": "invalid", "error": str(exc)}
        except Exception as exc:
            response = getattr(exc, "response", None)
            code = getattr(response, "status_code", None)
            report["sites"][str(site)] = {
                "status": "window_not_started" if code == 404 else "request_failed",
                "http": code,
                "error": str(exc).split(":")[0],
            }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if any(item["status"] in ("invalid", "request_failed") for item in report["sites"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
