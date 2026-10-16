"""C4: solver_seconds is loadable from contest config with 2.0s frozen default."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.config import load_runtime_config, require_runtime_fields


def write_config(payload: dict) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with handle:
        json.dump(payload, handle)
    return Path(handle.name)


def env_config(local: dict | None = None, experiment: dict | None = None) -> dict:
    payload = {"mode": "online_dry_run",
               "environment_machine": {"ha_base": "http://x", "sim_base": "http://y"}}
    if local is not None:
        payload["local"] = local
    if experiment is not None:
        payload["experiment"] = experiment
    return payload


class SolverSecondsMappingTests(unittest.TestCase):
    def test_env_format_defaults_to_frozen_two_seconds(self):
        cfg = load_runtime_config(write_config(env_config()))
        self.assertEqual(cfg["solver_seconds"], 2.0)

    def test_env_format_reads_local_section(self):
        cfg = load_runtime_config(write_config(env_config(local={"solver_seconds": 3.5})))
        self.assertEqual(cfg["solver_seconds"], 3.5)

    def test_experiment_section_overrides_local(self):
        cfg = load_runtime_config(write_config(
            env_config(local={"solver_seconds": 3.5}, experiment={"solver_seconds": 1.0})))
        self.assertEqual(cfg["solver_seconds"], 1.0)

    def test_flat_format_defaults_and_explicit(self):
        flat = {"ha_base": "http://x", "sim_base": "http://y"}
        self.assertEqual(load_runtime_config(write_config(flat))["solver_seconds"], 2.0)
        flat["solver_seconds"] = 4.0
        self.assertEqual(load_runtime_config(write_config(flat))["solver_seconds"], 4.0)

    def test_contest_config_file_loads_frozen_value(self):
        # The gitignored live contest config must carry the frozen value explicitly.
        # run6 profiling: hard MILP instances reach their final incumbent within
        # 0.1s and never improve before the 2.0s limit; the serial single-slot
        # solve then starves the write budget (38 delivery_blocked). 0.5s keeps
        # identical incumbent quality while preserving the delivery reserve.
        contest = Path(__file__).resolve().parents[1] / "config/local/local_online_config.json"
        if contest.exists():
            cfg = load_runtime_config(contest)
            self.assertEqual(cfg["solver_seconds"], 0.5)
            self.assertLess(cfg["solver_seconds"], cfg["poll_seconds"])

    def test_environment_timezone_is_exposed_for_device_time_alignment(self):
        cfg = load_runtime_config(write_config(env_config(local={"timezone": "Asia/Shanghai"})))
        self.assertIsNone(cfg["device_timezone"])
        self.assertIsNone(cfg["device_timezone_source"])

    def test_explicit_device_timezone_is_required_and_wins_over_local_timezone(self):
        payload = env_config(local={"timezone": "UTC", "device_timezone": "Asia/Shanghai"})
        cfg = load_runtime_config(write_config(payload))
        self.assertEqual(cfg["device_timezone"], "Asia/Shanghai")
        self.assertEqual(cfg["device_timezone_source"], "local.device_timezone")

    def test_experiment_device_timezone_overrides_local_device_timezone(self):
        cfg = load_runtime_config(write_config(env_config(
            local={"device_timezone": "UTC"},
            experiment={"device_timezone": "Asia/Shanghai"})))
        self.assertEqual(cfg["device_timezone"], "Asia/Shanghai")
        self.assertEqual(cfg["device_timezone_source"], "experiment.device_timezone")

    def test_flat_config_requires_explicit_device_timezone(self):
        cfg = load_runtime_config(write_config({"timezone": "Asia/Shanghai"}))
        self.assertIsNone(cfg["device_timezone"])
        cfg = load_runtime_config(write_config({"device_timezone": "Asia/Shanghai"}))
        self.assertEqual(cfg["device_timezone_source"], "device_timezone")

    def test_environment_machine_explicit_timezone_precedes_local(self):
        payload = env_config(local={"timezone": "UTC", "device_timezone": "UTC"})
        payload["environment_machine"]["device_timezone"] = "Asia/Shanghai"
        cfg = load_runtime_config(write_config(payload))
        self.assertEqual(cfg["device_timezone"], "Asia/Shanghai")
        self.assertEqual(cfg["device_timezone_source"], "environment_machine.device_timezone")

    def test_local_timezone_only_is_rejected_by_online_price_guard(self):
        from scripts.online.run_live import validate_milp_price_window
        cfg = load_runtime_config(write_config(env_config(local={"timezone": "Asia/Shanghai"})))
        self.assertEqual(validate_milp_price_window([], "2031-01-01 08:15:00", cfg["device_timezone"]),
                         "device_time_misaligned:missing_trusted_device_timezone")

    def test_runtime_gate_rejects_non_positive_or_slow_budget(self):
        base = {"ha_base": "http://x", "sim_base": "http://y", "sim_username": "u",
                "sim_password": "p", "ha_token": "token", "stations": {"1": {"suffix": "s"}},
                "poll_seconds": 5.0}
        for value in (0.0, -1.0, 5.0, 6.0):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "solver_seconds"):
                require_runtime_fields({**base, "solver_seconds": value})
        require_runtime_fields({**base, "solver_seconds": 2.0})


if __name__ == "__main__":
    unittest.main()
