"""CLI guards for the supervised B1 verifier; never contact HA."""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_zero_flow_verification.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)


def test_live_requires_existing_journal_before_any_network(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mode": "live", "stations": {"1": {"suffix": "test"}}}), encoding="utf-8")
    result = _run("--config", config, "--site", "1", "--mode", "live",
                  "--authorization-note", "test-only", "--output", tmp_path / "evidence")
    assert result.returncode != 0
    assert "requires an existing --reconciliation-state journal" in result.stderr
    assert not (tmp_path / "evidence").exists()


def test_live_rejects_pending_incident_before_any_network(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mode": "live", "stations": {"1": {"suffix": "test"}}}), encoding="utf-8")
    journal = tmp_path / "journal.json"
    journal.write_text(json.dumps({"version": 1, "pending": {"1": {"incident_id": "test"}},
                                   "clearances": []}), encoding="utf-8")
    result = _run("--config", config, "--site", "1", "--mode", "live",
                  "--authorization-note", "test-only", "--reconciliation-state", journal,
                  "--output", tmp_path / "evidence")
    assert result.returncode != 0
    assert "blocked by pending HA reconciliation sites" in result.stderr
    assert not (tmp_path / "evidence").exists()


def test_default_mode_is_dry_run_even_when_config_says_live(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mode": "live", "stations": {"1": {"suffix": "test"}}}), encoding="utf-8")
    snapshot = tmp_path / "snapshot.json"
    state = {"operating_mode": "self_consumption", "device_time": "2032-04-02 00:15:00"}
    snapshot.write_text(json.dumps({"baseline": state, "readback": state}), encoding="utf-8")
    output = tmp_path / "evidence"
    result = _run("--config", config, "--site", "1", "--snapshot", snapshot, "--output", output)
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert report["writes_performed"] == 0
    assert report["verdict"] is None
