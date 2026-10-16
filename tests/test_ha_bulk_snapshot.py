"""One HA GET feeds all stations; missing entities remain site-local."""
import sys
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.ha_client import HAClient
from energy_lab.config import canonical_suffix
from energy_lab.price_anchor import read_anchored_tick
from test_run_live_chain import StubSim, make_prices, state
from scripts.online.run_live import price_window


class BulkSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.client = HAClient("http://ha.invalid", "test")
        self.payload = []
        for suffix in ("1501", "1502", "1503"):
            sample = state()
            for key, entity in self.client._station_names(canonical_suffix(suffix)).items():
                changed = sample.get(f"{key}_last_changed", sample["device_time_last_changed"])
                self.payload.append({"entity_id": entity, "state": sample.get(key, 0),
                                     "last_changed": changed, "last_updated": changed})

    def test_one_get_for_three_stations(self):
        class Response:
            content = b"[]"
            def raise_for_status(self):
                pass
            def json(self):
                return self_payload
        self_payload = self.payload
        cfg = {"stations": {str(i): {"suffix": f"15{i:02d}"} for i in (1, 2, 3)},
               "device_timezone": None, "poll_seconds": 5, "solver_seconds": 2}
        with patch.object(self.client.session, "get", return_value=Response()) as get:
            result = read_anchored_tick(cfg, self.client, StubSim(make_prices()), price_window)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(result["alignment_attempts"], 1)
        self.assertEqual(result["metadata"]["serial_solver_worst_seconds"], 6)
        self.assertFalse(result["metadata"]["serial_tick_fits_poll"])
        self.assertEqual(result["states"]["1"]["missing_entities"], [])

    def test_missing_entity_is_diagnosed_per_station(self):
        entity = f"sensor.{canonical_suffix('1502')}_device_time"
        snapshot = {}
        for item in self.payload:
            if item["entity_id"] != entity:
                snapshot[item["entity_id"]] = [item]
        failed = self.client.station_from_snapshot("1502", snapshot)
        healthy = self.client.station_from_snapshot("1501", snapshot)
        self.assertEqual(failed["missing_entities"], [entity])
        self.assertEqual(failed["entity_diagnostics"]["device_time"]["reason"], "missing")
        self.assertEqual(healthy["missing_entities"], [])
        self.assertIn("last_changed", healthy["entity_diagnostics"]["soc"])

    def test_session_is_thread_local_for_delivery_workers(self):
        # The constructing thread keeps the shared ``session`` (read path and
        # single-threaded callers); a delivery worker gets its own Session so
        # concurrent ``call()`` POSTs never share a requests.Session.
        self.assertIs(self.client._session(), self.client.session)

        def run_in_thread(fn):
            box = []
            t = threading.Thread(target=lambda: box.append(fn()))
            t.start()
            t.join()
            return box[0]

        worker_session = run_in_thread(self.client._session)
        self.assertIsNot(worker_session, self.client.session)
        # The same worker thread reuses its Session across calls, while a
        # different worker thread gets a fresh one.
        same_worker_again = run_in_thread(lambda: (
            self.client._session(), self.client._session()))
        self.assertIs(same_worker_again[0], same_worker_again[1])
        self.assertIsNot(same_worker_again[0], worker_session)


if __name__ == "__main__":
    unittest.main()
