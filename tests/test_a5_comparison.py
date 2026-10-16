import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiments.generate_a5_comparison import build


class A5ComparisonTests(unittest.TestCase):
    def test_rejects_non_matching_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir(); new.mkdir()
            summary = {"sites": [], "aggregate": {"arms": {}}}
            for path, data_hash in ((old, "a"), (new, "b")):
                (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
                (path / "manifest.json").write_text(json.dumps({
                    "parameters": {"hold_mode": path.name, "output": str(path)},
                    "data_sha256": {"1.csv": data_hash}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifests differ"):
                build(old, new)


if __name__ == "__main__":
    unittest.main()
