"""Restart script defaults to a read-only ownership preflight."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "restart_w3_collector.ps1"


@pytest.mark.skipif(shutil.which("powershell") is None, reason="requires Windows PowerShell")
def test_missing_inputs_fail_without_process_control(tmp_path):
    result = subprocess.run(["powershell", "-NoProfile", "-File", str(SCRIPT),
                             "-ExpectedPid", "1", "-Config", "missing.json",
                             "-CacheDir", "outputs/cache", "-HealthDir", "outputs/health",
                             "-EvidenceDir", "outputs/restart-test", "-NodeId", "nodeA"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "collector config missing" in result.stderr
    assert not (ROOT / "outputs" / "restart-test").exists()


@pytest.mark.skipif(shutil.which("powershell") is None, reason="requires Windows PowerShell")
def test_path_outside_outputs_fails_before_pid_lookup(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    result = subprocess.run(["powershell", "-NoProfile", "-File", str(SCRIPT),
                             "-ExpectedPid", "1", "-Config", str(config),
                             "-CacheDir", str(tmp_path), "-HealthDir", "outputs/health",
                             "-EvidenceDir", "outputs/restart-test", "-NodeId", "nodeA"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "must be under workspace outputs" in result.stderr


@pytest.mark.skipif(shutil.which("powershell") is None, reason="requires Windows PowerShell")
def test_default_preflight_refuses_missing_heartbeat_without_writing():
    evidence = ROOT / "outputs" / "restart_contract_test"
    assert not evidence.exists()
    result = subprocess.run(["powershell", "-NoProfile", "-File", str(SCRIPT),
                             "-ExpectedPid", "1", "-Config", "local_online_config.example.json",
                             "-CacheDir", "outputs/local_mock_20261016_run3",
                             "-HealthDir", "outputs/local_mock_20261016_run3",
                             "-EvidenceDir", "outputs/restart_contract_test", "-NodeId", "nodeA"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "heartbeat missing" in result.stderr
    assert not evidence.exists()
