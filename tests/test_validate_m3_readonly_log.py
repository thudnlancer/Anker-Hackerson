from pathlib import Path
import tempfile
import unittest

import pandas as pd

from validate_m3_readonly_log import validate


class M3ReadonlyLogTests(unittest.TestCase):
    def fixture(self, root, *, stale=False, writes=0):
        root = Path(root)
        console, audit = root / "console.log", root / "audit.csv"
        lines = ["mode=dry-run; write_enabled=False; strategy=milp; config=redacted"]
        rows = []
        for tick in range(3):
            for site in ("1", "2", "3"):
                status = "stale_or_unavailable" if stale and site == "2" else "zero_flow_dispatched"
                lines.append(repr({"site": site, "status": status,
                                   "write_enabled": False, "writes_performed": writes,
                                   "alignment_source": "api_current_slot",
                                   "api_current_start": "2031-01-01T00:00:00Z",
                                   "api_current_end": "2031-01-01T01:00:00Z",
                                   "selected_price_slot": "2031-01-01T00:00:00Z",
                                   "ha_device_time_raw": f"2031-01-01 00:{tick * 5:02d}:00",
                                   "resolved_device_time": f"2031-01-01T00:{tick * 5:02d}:00+00:00"}))
                if status != "stale_or_unavailable":
                    rows.append({"site": site, "writes_performed": writes,
                                 "alignment_source": "api_current_slot"})
        console.write_text("\n".join(lines), encoding="utf-8")
        pd.DataFrame(rows).to_csv(audit, index=False)
        return console, audit

    def test_rejects_missing_current_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            console, audit = self.fixture(root)
            console.write_text(console.read_text().replace("api_current_slot", "guessed_timezone"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "alignment"):
                validate(console, audit)

    def test_rejects_nonadvancing_device_clock(self):
        with tempfile.TemporaryDirectory() as root:
            console, audit = self.fixture(root)
            console.write_text(console.read_text().replace("00:05:00", "00:00:00"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "did not increase"):
                validate(console, audit)

    def test_three_ticks_and_zero_writes(self):
        with tempfile.TemporaryDirectory() as root:
            console, audit = self.fixture(root)
            result = validate(console, audit)
            self.assertEqual((result["ticks"], result["audited_rows"]), (3, 9))

    def test_rejects_stale_station(self):
        with tempfile.TemporaryDirectory() as root:
            console, audit = self.fixture(root, stale=True)
            with self.assertRaisesRegex(ValueError, "stale"):
                validate(console, audit)

    def test_rejects_write(self):
        with tempfile.TemporaryDirectory() as root:
            console, audit = self.fixture(root, writes=1)
            with self.assertRaisesRegex(ValueError, "write"):
                validate(console, audit)


if __name__ == "__main__":
    unittest.main()
