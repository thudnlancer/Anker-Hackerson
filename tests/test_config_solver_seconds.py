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
        contest = Path(__file__).resolve().parents[1] / "local_online_config.json"
        if contest.exists():
            cfg = load_runtime_config(contest)
            self.assertEqual(cfg["solver_seconds"], 2.0)
            self.assertLess(cfg["solver_seconds"], cfg["poll_seconds"])

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
