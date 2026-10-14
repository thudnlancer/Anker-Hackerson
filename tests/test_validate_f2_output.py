import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate_f2_output import ARMS, COUNT_FIELDS, validate


class F2OutputValidationTests(unittest.TestCase):
    def fixture(self, root):
        root = Path(root)
        data, output = root / "data", root / "output"
        data.mkdir()
        output.mkdir()
        dataset = data / "1.csv"
        dataset.write_text("fixture", encoding="utf-8")
        dataset_sha = hashlib.sha256(dataset.read_bytes()).hexdigest()
        sources = {name: "a" * 64 for name in (
            "run_contest_strategy_replay.py", "src/energy_lab/contest_replay.py",
            "src/energy_lab/rolling.py", "src/energy_lab/model.py", "src/energy_lab/validation.py")}
        frozen = {"f2_authorized": False, "signature": {"f2_authorization": False},
                  "parameters": {"solver_seconds": 2., "capacity_kwh": 14., "max_power_w": 5000.,
                                 "soc_min_percent": 0., "soc_max_percent": 100.,
                                 "charge_efficiency": .95, "discharge_efficiency": 1.,
                                 "degradation_cost_eur_per_kwh": .02, "successful_plan_idle": "zero_flow",
                                 "terminal_price": {"rule": "current_visible_mean"}},
                  "reproducibility": {"available_practice_dataset_sha256": {"forecast_data/1.csv": dataset_sha},
                                      "source_sha256": sources}}
        f1 = root / "f1.json"
        f1.write_text(json.dumps(frozen), encoding="utf-8")
        f1_sha = hashlib.sha256(f1.read_bytes()).hexdigest()
        params = {"stage": 4, "max_sites": 1, "solver_time_limit": 2., "capacity_kwh": 14.,
                  "max_power_w": 5000., "soc_min": 0., "soc_max": 100.,
                  "charge_efficiency": .95, "discharge_efficiency": 1., "wear": .02,
                  "hold_mode": "zero_flow", "terminal_price_mode": "current_visible_mean",
                  "f1_manifest_sha256": f1_sha}
        (output / "manifest.json").write_text(json.dumps({"parameters": params, "hours": 168,
            "f1_manifest_sha256": f1_sha, "data_sha256": {"forecast_data/1.csv": dataset_sha},
            "source_sha256": sources}), encoding="utf-8")
        hours = pd.date_range("2026-01-01", periods=168, freq="h")
        rows = []
        for arm in ARMS:
            for time in hours:
                baseline = arm == "self_consumption"
                rows.append({"site": "1", "time": time, "arm": arm,
                             "solver_status": "not_applicable" if baseline else "optimal",
                             "nonoptimal_solves": 0, "accepted_incumbent": False, "mip_gap": None,
                             "fallback": False, "fallback_reason": None,
                             "optimizer_attributed": not baseline,
                             "attribution": "non-optimizer-attributed" if baseline else "optimizer-attributed",
                             "command_mode": "self_consumption" if baseline else "zero_flow",
                             "flow": "idle", "power_w": 0., "soc_before": 50., "soc_after": 50.,
                             "grid_import_kwh": 0., "grid_export_kwh": 0., "cost_eur": 0.,
                             "throughput_kwh": 0., "negative_export_loss_eur": 0.})
        trace = pd.DataFrame(rows)
        trace.to_csv(output / "site_1_trace.csv", index=False)
        arms = {arm: {key: (168 if key == "non_optimizer_attributed_steps" and arm == "self_consumption"
                              else 168 if key == "optimizer_attributed_steps" and arm != "self_consumption"
                              else 0) for key in COUNT_FIELDS} for arm in ARMS}
        for metrics in arms.values():
            metrics.update({key: 0. for key in ("operating_cost_eur", "settled_cost_eur",
                       "throughput_kwh", "internal_wear_eur", "settled_saving_eur")})
        site = {"site": "1", "hours": 168, "arms": arms, "physical_audit": {"passed": True, "rows": 672}}
        (output / "site_1_summary.json").write_text(json.dumps(site), encoding="utf-8")
        (output / "summary.json").write_text(json.dumps({"f1_manifest_sha256": f1_sha,
            "official_score_claim": False, "writes_performed": 0, "sites": [site],
            "aggregate": {"arms": arms}}), encoding="utf-8")
        raw = pd.DataFrame({"time": hours, "pv": 0., "load": 0., "buy": .3, "sell": .1})
        return f1, f1_sha, data, output, raw

    def test_single_site_counts_and_physical_audit(self):
        with tempfile.TemporaryDirectory() as root:
            f1, sha, data, output, raw = self.fixture(root)
            with patch("validate_f2_output.load_csv", return_value=raw):
                result = validate(output, manifest_path=f1, expected_manifest_sha256=sha,
                                  data=data, allow_smoke=True)
            self.assertTrue(result["valid"])
            self.assertEqual(result["physical_audit_rows"], 672)
            self.assertFalse(result["formal_result_authorized"])
            self.assertEqual(result["counts"]["persistence"]["optimizer_attributed_steps"], 168)

    def test_unsigned_formal_and_bad_audit_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            f1, sha, data, output, raw = self.fixture(root)
            with self.assertRaisesRegex(ValueError, "formal F2 requires"):
                validate(output, manifest_path=f1, expected_manifest_sha256=sha, data=data)
            path = output / "site_1_trace.csv"
            trace = pd.read_csv(path)
            trace.loc[trace.arm.eq("persistence"), "attribution"] = "non-optimizer-attributed"
            trace.to_csv(path, index=False)
            with patch("validate_f2_output.load_csv", return_value=raw):
                with self.assertRaisesRegex(ValueError, "attribution/fallback label"):
                    validate(output, manifest_path=f1, expected_manifest_sha256=sha,
                             data=data, allow_smoke=True)


if __name__ == "__main__":
    unittest.main()
