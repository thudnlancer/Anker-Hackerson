import csv
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from energy_explain.api import create_app
from energy_explain.offline_replay import OfflineReplay


TRACE = Path(__file__).resolve().parents[3] / "实验环境" / "outputs" / "w1_a5_zero_flow_smoke_20261014_1" / "site_1_trace.csv"
SHA = "c0a7ff9479f2f8faffc6791cd4c0670f38107beba22599fcfd2c4a5479e99fbf"


def test_w1_mapping_is_read_only_and_preserves_local_hour(tmp_path):
    assert hashlib.sha256(TRACE.read_bytes()).hexdigest() == SHA
    app = create_app(tmp_path / "app.sqlite", offline_trace_path=TRACE, offline_trace_sha256=SHA)
    with TestClient(app) as client:
        summary = client.get("/api/offline-replay").json()
        assert summary["time_basis"] == "dataset_local_hour"
        assert summary["hours_per_arm"] == 168
        assert summary["official_score_claim"] is False
        assert summary["negative_price_rows"] == 0
        detail = client.get("/api/offline-replay/steps/0?arm=irradiance").json()
        with TRACE.open(encoding="utf-8", newline="") as stream:
            row = next(r for r in csv.DictReader(stream) if r["arm"] == "irradiance")
        record = detail["record"]
        assert record["local_hour"] == row["time"] == "2026-09-14 14:00:00"
        assert record["planned_power_kw"] == pytest.approx(float(row["planned_power_w"]) / 1000)
        assert record["command_target_kw"] == pytest.approx(float(row["command_target_w"]) / 1000)
        assert record["power_kw"] == pytest.approx(float(row["power_w"]) / 1000)
        assert record["soc_before"] == pytest.approx(float(row["soc_before"]))
        assert record["cost_eur"] == pytest.approx(float(row["cost_eur"]))
        assert record["optimizer_attributed"] is True
        assert detail["execution_status"] == "not_observed"
        assert detail["settlement"] is None
        assert detail["capabilities"]["counterfactual"] is False
        assert "device_time" not in record and "observed_at" not in record
        assert all(fact["evidence_refs"] for fact in detail["facts"])
        assert client.get("/api/stations").json()["stations"] == []
        assert client.get("/api/decisions").json()["decisions"] == []
        assert client.get("/api/offline-replay/steps/168").status_code == 404
        assert client.get("/api/offline-replay/steps/0?arm=unknown").status_code == 404


def test_w1_adapter_rejects_source_mismatch_and_missing_configuration(tmp_path):
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        OfflineReplay(TRACE, "0" * 64)
    with TestClient(create_app(tmp_path / "empty.sqlite")) as client:
        assert client.get("/api/offline-replay").status_code == 404


def test_legacy_trace_missing_diagnostics_is_unknown_without_solver_inference(tmp_path):
    legacy = tmp_path / "legacy.csv"
    with TRACE.open(encoding="utf-8", newline="") as source, legacy.open("w", encoding="utf-8", newline="") as target:
        reader = csv.DictReader(source)
        fields = [field for field in reader.fieldnames or [] if field not in {"optimizer_attributed", "attribution", "fallback"}]
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for row in reader:
            row.pop("optimizer_attributed", None)
            row.pop("attribution", None)
            row.pop("fallback", None)
            writer.writerow(row)
    sha = hashlib.sha256(legacy.read_bytes()).hexdigest()
    replay = OfflineReplay(legacy, sha)
    for arm in ("irradiance", "self_consumption"):
        detail = replay.detail(arm, 0)
        record = detail["record"]
        assert record["solver_status"] == ("optimal" if arm == "irradiance" else "not_applicable")
        assert record["optimizer_attributed"] is None
        assert record["attribution"] is None
        assert record["fallback"] is None
        assert "优化器归因 未知" in detail["facts"][3]["text"]
        assert "归因标签 未知" in detail["facts"][3]["text"]
        assert "回退 未知" in detail["facts"][3]["text"]
        assert all(not any(key in ref for key in ("optimizer_attributed", "attribution", "fallback"))
                   for ref in detail["facts"][3]["evidence_refs"])
