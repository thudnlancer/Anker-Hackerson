import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scripts.offline import build_epoch_datasets, run_epoch_replay


def make_capture(root: Path, ticks: int = 16, sites=("1", "2")) -> Path:
    cache = root / "cache"
    cache.mkdir(parents=True)
    base = pd.Timestamp("2026-10-10T08:30:00+00:00")
    for site in sites:
        lines = []
        for i in range(ticks):
            stamp = base + pd.Timedelta(minutes=15 * i)
            lines.append(json.dumps({
                "site": site, "device_time": stamp.isoformat(),
                "soc": 50.0, "pv": 100.0 * (i % 4), "load": 500.0,
                "import_price": 0.30, "export_price": 0.10,
                "price_status": "ok", "weather_status": "valid",
                "temperature_c": 20.0, "cloud_cover_percent": 10.0,
                "solar_irradiance_wm2": 500.0}))
        (cache / f"site_{site}.nodeA.jsonl").write_text("\n".join(lines) + "\n",
                                                        encoding="utf-8")
    return root


class BuildEpochDatasetsTests(unittest.TestCase):
    def test_builds_epochs_with_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=16)
            out = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(out),
                                            "--epoch-hours", "1", "--min-epochs", "6",
                                            "--sites", "1", "2"]):
                self.assertEqual(build_epoch_datasets.main(), 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["epochs"]), 6)
            self.assertTrue(manifest["overlap"])  # 16 ticks < 6 x 4 ticks non-sliding
            frame = pd.read_csv(out / "epoch_00" / "site_1.csv")
            self.assertEqual(len(frame), 4)
            self.assertEqual(list(frame.columns), build_epoch_datasets.COLUMNS)
            self.assertTrue((frame.import_price == 0.30).all())

    def test_non_overlapping_when_data_allows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=8)
            out = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(out),
                                            "--epoch-hours", "0.5", "--min-epochs", "3",
                                            "--sites", "1"]):
                self.assertEqual(build_epoch_datasets.main(), 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["overlap"])  # 8 ticks = 4 epochs of 2 ticks

    def test_missing_price_repaired_from_same_hour(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=8)
            shard = capture / "cache" / "site_1.nodeA.jsonl"
            rows = [json.loads(line) for line in shard.read_text().splitlines()]
            rows[1]["import_price"] = None  # sibling ticks in hour 08 keep their price
            rows[1]["export_price"] = None
            shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            out = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(out),
                                            "--epoch-hours", "0.5", "--min-epochs", "2",
                                            "--sites", "1"]):
                self.assertEqual(build_epoch_datasets.main(), 0)
            frame = pd.read_csv(out / "epoch_00" / "site_1.csv")
            self.assertTrue((frame.import_price == 0.30).all())

    def test_missing_price_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=16)
            shard = capture / "cache" / "site_1.nodeA.jsonl"
            rows = [json.loads(line) for line in shard.read_text().splitlines()]
            for i in range(6, 10):  # hour 10:00-10:45 without any priced tick
                rows[i]["import_price"] = None
                rows[i]["export_price"] = None
            shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            with patch.object(sys, "argv", ["build", str(capture),
                                            "--output", str(root / "e"),
                                            "--epoch-hours", "1", "--min-epochs", "2",
                                            "--sites", "1"]):
                with self.assertRaisesRegex(ValueError, "missing prices"):
                    build_epoch_datasets.main()

    def test_isolated_gap_filled_and_flagged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=8)
            shard = capture / "cache" / "site_1.nodeA.jsonl"
            rows = [json.loads(line) for line in shard.read_text().splitlines()]
            del rows[3]  # remove one interior tick
            shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            out = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(out),
                                            "--epoch-hours", "0.5", "--min-epochs", "2",
                                            "--sites", "1", "--max-fill-ticks", "1"]):
                self.assertEqual(build_epoch_datasets.main(), 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["epochs"][1]["sites"]["1"]["filled"], 1)
            frame = pd.read_csv(out / "epoch_01" / "site_1.csv")
            filled_row = frame[frame.filled == True]  # noqa: E712
            self.assertEqual(len(filled_row), 1)
            self.assertEqual(float(filled_row.iloc[0].pv_w), 100.0)  # mean of neighbors
            self.assertEqual(float(filled_row.iloc[0].import_price), 0.30)

    def test_gap_beyond_limit_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=8)
            shard = capture / "cache" / "site_1.nodeA.jsonl"
            rows = [json.loads(line) for line in shard.read_text().splitlines()]
            del rows[3]  # one fillable gap, but the limit is zero
            shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            with patch.object(sys, "argv", ["build", str(capture),
                                            "--output", str(root / "e"),
                                            "--epoch-hours", "0.5", "--min-epochs", "2",
                                            "--sites", "1"]):
                with self.assertRaisesRegex(ValueError, "exceed limit"):
                    build_epoch_datasets.main()

    def test_consecutive_gap_cannot_be_filled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=8)
            shard = capture / "cache" / "site_1.nodeA.jsonl"
            rows = [json.loads(line) for line in shard.read_text().splitlines()]
            del rows[3:5]  # two consecutive missing ticks inside epoch 1's window
            shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            with patch.object(sys, "argv", ["build", str(capture),
                                            "--output", str(root / "e"),
                                            "--epoch-hours", "0.5", "--min-epochs", "2",
                                            "--sites", "1", "--max-fill-ticks", "2"]):
                with self.assertRaisesRegex(ValueError, "epoch 1 site 1"):
                    build_epoch_datasets.main()


class RunEpochReplayTests(unittest.TestCase):
    def test_replay_smoke_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=8)
            epochs = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(epochs),
                                            "--epoch-hours", "1", "--min-epochs", "1",
                                            "--sites", "1"]):
                build_epoch_datasets.main()
            out = root / "replay"
            with patch.object(sys, "argv", ["replay", "--epochs-dir", str(epochs),
                                            "--strategies", "milp",
                                            "--forecast-modes", "persistence",
                                            "--output", str(out)]):
                self.assertEqual(run_epoch_replay.main(), 0)
            report = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(len(report["summaries"]), 1)
            summary = report["summaries"][0]
            self.assertEqual(summary["ticks"], 4)
            self.assertTrue(0 <= summary["final_soc_percent"] <= 100)

    def test_unknown_forecast_mode_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=4)
            epochs = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(epochs),
                                            "--epoch-hours", "1", "--min-epochs", "1",
                                            "--sites", "1"]):
                build_epoch_datasets.main()
            frame = pd.read_csv(epochs / "epoch_00" / "site_1.csv")
            with self.assertRaisesRegex(ValueError, "unknown forecast mode"):
                run_epoch_replay.replay_site(frame, "milp", "oracle", 5.0)

    def test_v16_mode_requires_forecaster(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=4)
            epochs = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(epochs),
                                            "--epoch-hours", "1", "--min-epochs", "1",
                                            "--sites", "1"]):
                build_epoch_datasets.main()
            frame = pd.read_csv(epochs / "epoch_00" / "site_1.csv")
            with self.assertRaisesRegex(ValueError, "requires a load_forecaster"):
                run_epoch_replay.replay_site(frame, "milp", "v16", 5.0)

    def test_v16_mode_smoke_with_fake_forecaster(self):
        class FakeForecaster:
            def __init__(self):
                self.calls = 0

            def forecast_w(self, history, steps=12):
                self.calls += 1
                return np.full(steps, 600.0)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=8)
            epochs = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(epochs),
                                            "--epoch-hours", "1", "--min-epochs", "1",
                                            "--sites", "1"]):
                build_epoch_datasets.main()
            frame = pd.read_csv(epochs / "epoch_00" / "site_1.csv")
            fake = FakeForecaster()
            trace, summary = run_epoch_replay.replay_site(frame, "milp", "v16", 5.0,
                                                          load_forecaster=fake)
            self.assertEqual(summary["ticks"], 4)
            self.assertEqual(summary["v16_forecast_calls"], fake.calls)
            self.assertGreaterEqual(fake.calls, 1)


    def test_evidence_export_jsonl(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = make_capture(root / "cap", ticks=104)
            epochs = root / "epochs"
            with patch.object(sys, "argv", ["build", str(capture), "--output", str(epochs),
                                            "--epoch-hours", "26", "--min-epochs", "1",
                                            "--sites", "1"]):
                build_epoch_datasets.main()
            out = root / "replay"
            with patch.object(sys, "argv", ["replay", "--epochs-dir", str(epochs),
                                            "--strategies", "milp",
                                            "--forecast-modes", "persistence",
                                            "--evidence-run-id", "test-run",
                                            "--evidence-every", "1",
                                            "--output", str(out)]):
                self.assertEqual(run_epoch_replay.main(), 0)
            lines = (out / "evidence-decisions.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines)
            record = json.loads(lines[0])
            self.assertEqual(record["run_id"], "test-run")
            self.assertEqual(record["site_id"], "epoch_00:site_1:milp:persistence")
            self.assertEqual(record["source_kind"], "external")
            for key in ("decision_id", "snapshot", "action", "cost", "replay_context"):
                self.assertIn(key, record)
            self.assertEqual(record["solver_status"], "optimal")
            self.assertEqual(len(record["snapshot"]["pv_forecast_kw"]), 96)


class V16LoadCacheTests(unittest.TestCase):
    def test_window_alignment_and_refresh(self):
        class FakeForecaster:
            def __init__(self):
                self.calls = 0

            def forecast_w(self, history, steps=12):
                self.calls += 1
                self.last_history = np.asarray(history, dtype=float)
                return np.arange(100.0, 100.0 + steps)  # preds[i] covers made_at+1+i

        load = np.full(40, 500.0)
        fake = FakeForecaster()
        cache = run_epoch_replay.V16LoadCache(fake, refresh_ticks=4)
        window = cache.window(load, 0, 5)
        self.assertEqual(window[0], 500.0)          # current tick realized
        self.assertEqual(window[1], 100.0)          # preds[0] -> tick 1
        self.assertEqual(window[4], 103.0)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(len(fake.last_history), 18)  # left-padded at t=0
        window = cache.window(load, 3, 2)             # still fresh
        self.assertEqual(fake.calls, 1)
        self.assertEqual(window[1], 103.0)          # preds[3] -> tick 4
        window = cache.window(load, 4, 2)             # refresh_ticks reached
        self.assertEqual(fake.calls, 2)

    def test_flat_extension_beyond_prediction_block(self):
        class FakeForecaster:
            def forecast_w(self, history, steps=12):
                return np.full(steps, 700.0)

        load = np.full(40, 500.0)
        cache = run_epoch_replay.V16LoadCache(FakeForecaster(), refresh_ticks=100)
        window = cache.window(load, 0, 30)
        self.assertTrue((window[13:] == 700.0).all())  # preds hold past tick 12
        self.assertEqual(window[12], 700.0)
