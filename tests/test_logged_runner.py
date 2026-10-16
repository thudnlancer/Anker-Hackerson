import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LoggedRunnerTests(unittest.TestCase):
    def test_parser_failure_keeps_true_exit_code_and_log(self):
        script = Path(__file__).resolve().parents[1] / "scripts/experiments/run_logged_experiment.py"
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(script), "--output", folder,
                                     "--solver", "invalid"], capture_output=True)
            self.assertEqual(result.returncode, 2)
            status = json.loads((Path(folder) / "process_status.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(status["returncode"], 2)
            self.assertIn(b"invalid choice", (Path(folder) / "console.log").read_bytes())


if __name__ == "__main__":
    unittest.main()
