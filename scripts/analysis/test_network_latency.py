"""Clean warm-HTTTP latency comparison: direct vs local proxy (127.0.0.1:22307).

Uses the real SimulatorClient/HAClient (auth + keep-alive), so numbers match
what the runner actually experiences.
"""
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.config import exchange_ha_token, load_runtime_config
from energy_lab.sim_client import SimulatorClient
from energy_lab.ha_client import HAClient

PROXY = "http://127.0.0.1:22307"


def measure(fn, n=12):
    fn()  # warm-up (auth + establish connection)
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return ts


def report(name, ts):
    if not ts:
        print(f"{name}: (all failed)")
        return
    q = statistics.quantiles(ts, n=10) if len(ts) >= 10 else []
    extra = f" p90={q[8]:.0f}ms" if q else ""
    print(f"{name:22s} n={len(ts)} min={min(ts):.0f} med={statistics.median(ts):.0f}{extra} max={max(ts):.0f}ms")


def run(label, proxy_mode, use_proxy):
    if use_proxy:
        os.environ["HTTP_PROXY"] = PROXY
        os.environ["HTTPS_PROXY"] = PROXY
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)
    sim = SimulatorClient(cfg["sim_base"], cfg["sim_username"], cfg["sim_password"],
                          proxy_mode=proxy_mode)
    ha = HAClient(cfg["ha_base"], exchange_ha_token(cfg), proxy_mode=proxy_mode)
    try:
        report(f"{label} sim /api/contest", measure(lambda: sim.get("/api/contest")))
    except Exception as exc:
        print(f"{label} sim: FAILED {type(exc).__name__}: {str(exc)[:80]}")
    try:
        report(f"{label} sim current_price", measure(lambda: sim.current_price()))
    except Exception as exc:
        print(f"{label} sim current_price: FAILED {type(exc).__name__}: {str(exc)[:80]}")
    try:
        report(f"{label} ha snapshot", measure(lambda: ha.snapshot()))
    except Exception as exc:
        print(f"{label} ha snapshot: FAILED {type(exc).__name__}: {str(exc)[:80]}")


if __name__ == "__main__":
    cfg = load_runtime_config(Path("config/local/local_online_config.json"), "dry-run")
    print("=== DIRECT (proxy_mode=direct) ===")
    run("direct", "direct", use_proxy=False)
    print("\n=== PROXY (127.0.0.1:22307) ===")
    run("proxy", "environment", use_proxy=True)
