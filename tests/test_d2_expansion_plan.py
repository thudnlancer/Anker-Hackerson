"""D2 PowerShell chain stays plan-only unless explicitly executed."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analysis" / "run_d2_expansion.ps1"


@pytest.mark.skipif(shutil.which("powershell") is None, reason="requires Windows PowerShell")
def test_default_is_plan_only_and_creates_no_output(tmp_path):
    target = ROOT / "outputs" / "d2_plan_only_contract_test"
    assert not target.exists()
    result = subprocess.run(["powershell", "-NoProfile", "-File", str(SCRIPT),
                             "-OutputDir", "outputs/d2_plan_only_contract_test",
                             "-Batches", "2", "-Iterations", "1", "-IntervalSeconds", "1"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["mode"] == "plan_only"
    assert plan["ha_writes_allowed"] is False
    assert plan["batches"] == 2
    assert not target.exists()


@pytest.mark.skipif(shutil.which("powershell") is None, reason="requires Windows PowerShell")
def test_output_outside_workspace_is_rejected(tmp_path):
    result = subprocess.run(["powershell", "-NoProfile", "-File", str(SCRIPT),
                             "-OutputDir", str(tmp_path)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0
    assert "OutputDir must be a new child directory" in result.stderr
