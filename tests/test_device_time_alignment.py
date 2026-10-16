import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.online.audit_device_time_alignment import inspect_snapshot, summarize


class DeviceTimeAlignmentTests(unittest.TestCase):
    def test_inspect_snapshot_detects_cross_station_offset(self):
        snapshot = {"stations": {
            "1": {"state": {"device_time": "2032-04-02 00:15:00",
                             "device_time_last_changed": "2032-04-02T00:00:00Z"}},
            "2": {"state": {"device_time": "2032-04-02 00:00:00",
                             "device_time_last_changed": "2032-04-01T23:59:57Z"}},
        }}
        record = inspect_snapshot(snapshot)
        self.assertFalse(record["aligned"])
        self.assertEqual(record["stations"]["2"]["device_time"], "2032-04-02 00:00:00")

    def test_summary_reports_alignment_frequency_and_offsets(self):
        samples = [
            {"aligned": True, "stations": {"1": {"device_time": "2032-04-02 00:15:00"},
                                              "2": {"device_time": "2032-04-02 00:15:00"}}},
            {"aligned": False, "stations": {"1": {"device_time": "2032-04-02 00:30:00"},
                                               "2": {"device_time": "2032-04-02 00:15:00"}}},
        ]
        report = summarize(samples)
        self.assertEqual(report["aligned_samples"], 1)
        self.assertEqual(report["unaligned_samples"], 1)
        self.assertEqual(report["offset_minutes_from_latest_by_site"]["2"]["-15"], 1)


if __name__ == "__main__":
    unittest.main()
