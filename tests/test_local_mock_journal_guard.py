"""A new mock run cannot hide an unresolved HA write behind another journal."""

import json
import sys
from unittest.mock import patch

import pytest

from scripts.online import run_local_mock


def test_alternate_clean_journal_is_blocked_before_output_or_network(tmp_path):
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"version": 1, "pending": {}, "clearances": []}), encoding="utf-8")
    incident = tmp_path / "outputs" / "old_run" / "ha_reconciliation_state.json"
    incident.parent.mkdir(parents=True)
    incident.write_text(json.dumps({"version": 1, "pending": {"2": {"incident_id": "unknown"}},
                                    "clearances": []}), encoding="utf-8")
    output = tmp_path / "new_run"
    argv = ["run_local_mock.py", "--config", str(tmp_path / "unused.json"),
            "--output-dir", str(output), "--reconciliation-state", str(clean),
            "--confirm-mock-ha-write"]
    with patch.object(run_local_mock, "ROOT", tmp_path), patch.object(sys, "argv", argv):
        with pytest.raises(SystemExit, match="blocked by pending HA reconciliation sites"):
            run_local_mock.main()
    assert not output.exists()
    assert json.loads(incident.read_text(encoding="utf-8"))["pending"]["2"]["incident_id"] == "unknown"
