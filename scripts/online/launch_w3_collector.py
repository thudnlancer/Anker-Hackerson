"""Start a read-only collector with explicit, isolated paths and durable logs."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def command(config: Path, cache_dir: Path, health_dir: Path, node_id: str,
            iterations: int, interval_seconds: float, device_timezone: str | None) -> list[str]:
    if not config.is_file():
        raise ValueError("collector config must exist")
    if iterations < 0 or interval_seconds <= 0:
        raise ValueError("iterations must be nonnegative and interval positive")
    if not node_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("node-id must be filename-safe")
    args = [sys.executable, "-u", str(ROOT / "scripts/online/collect_online_history.py"),
            "--config", str(config.resolve()), "--cache-dir", str(cache_dir.resolve()),
            "--health-dir", str(health_dir.resolve()), "--node-id", node_id,
            "--iterations", str(iterations), "--interval-seconds", str(interval_seconds),
            "--max-consecutive-errors", "12"]
    if device_timezone:
        args.extend(["--device-timezone", device_timezone])
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--health-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--iterations", type=int, required=True,
                        help="0 means run until stopped; choose explicitly")
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--device-timezone", help="confirmed HA device timezone only")
    args = parser.parse_args()
    try:
        argv = command(args.config, args.cache_dir, args.health_dir, args.node_id,
                       args.iterations, args.interval_seconds, args.device_timezone)
    except ValueError as exc:
        parser.error(str(exc))
    evidence = args.evidence_dir.resolve()
    if evidence.exists():
        parser.error("evidence directory already exists; choose a new run directory")
    evidence.mkdir(parents=True)
    with (evidence / "collector.out.log").open("xb") as stdout, \
            (evidence / "collector.err.log").open("xb") as stderr:
        child = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=stdout, stderr=stderr,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(child.pid)


if __name__ == "__main__":
    main()
