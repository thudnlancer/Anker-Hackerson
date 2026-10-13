import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import energy_lab.strategy_backtest as runner


class RecoveryTests(unittest.TestCase):
    def test_interruption_resume_matches_fresh_and_rejects_changed_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "data"
            data.mkdir()
            for site in (1, 2):
                (data / f"{site}.csv").write_text("fixture", encoding="utf-8")
            frame = pd.DataFrame({"time": pd.date_range("2026-01-01", periods=100, freq="h"),
                                  "pv": 0., "load": 0.})

            def evaluate(task):
                site = Path(task[0]).stem
                rows = []
                for policy in ("rule", "forecast_milp_persistence"):
                    for time in frame.time.iloc[80:]:
                        rows.append({"site": site, "time": time, "weather_mode": "persistence",
                                     "policy": policy, "flow": "idle", "power_w": 0.,
                                     "soc_before": 50., "soc_after": 50., "grid_import_kwh": 0.,
                                     "grid_export_kwh": 0., "cost_eur": 0., "negative_export_loss_eur": 0.,
                                     "throughput_kwh": 0., "lp_status": "optimal", "lp_fallback": False})
                return rows, {"one_step_pairs": 0}

            def interrupted(task):
                if Path(task[0]).stem == "2":
                    raise RuntimeError("injected interruption")
                return evaluate(task)

            kwargs = dict(weather_modes=("persistence",), solver="milp", terminal_price=.1)
            with patch.object(runner, "load_weather_csv", return_value=frame), \
                 patch.object(runner, "fit_shared_model_full", return_value="fixed-model"):
                with patch.object(runner, "_evaluate_site_task", side_effect=interrupted):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        runner.run_strategy_comparison(data, root / "resume", **kwargs)
                self.assertFalse((root / "resume/strategy_comparison_summary.json").exists())
                with patch.object(runner, "_evaluate_site_task", side_effect=evaluate) as work:
                    runner.run_strategy_comparison(data, root / "resume", resume=True, **kwargs)
                    self.assertEqual(work.call_count, 1)
                    runner.run_strategy_comparison(data, root / "fresh", **kwargs)
                self.assertEqual((root / "resume/strategy_comparison_trace.csv").read_bytes(),
                                 (root / "fresh/strategy_comparison_trace.csv").read_bytes())
                with self.assertRaises(FileExistsError):
                    runner.run_strategy_comparison(data, root / "fresh", **kwargs)
                with patch.object(runner, "_evaluate_site_task", side_effect=interrupted):
                    with self.assertRaises(RuntimeError):
                        runner.run_strategy_comparison(data, root / "changed", **kwargs)
                (data / "1.csv").write_text("changed-input", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Resume rejected"):
                    runner.run_strategy_comparison(data, root / "changed", resume=True, **kwargs)


if __name__ == "__main__":
    unittest.main()
