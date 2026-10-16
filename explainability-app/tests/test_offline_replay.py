import csv
from datetime import datetime, timedelta
import hashlib

import pytest
from fastapi.testclient import TestClient

from energy_explain.api import create_app
from energy_explain.offline_replay import OfflineReplay


@pytest.fixture
def trace(tmp_path):
    """A deterministic, synthetic 168-hour trace; CI needs no local outputs."""
    path = tmp_path / "site_1_trace.csv"
    fields = ["site", "time", "arm", "solver_status", "fallback",
              "pv_w", "load_w", "buy", "sell", "flow", "power_w",
              "soc_before", "soc_after", "grid_import_kwh", "grid_export_kwh",
              "cost_eur", "negative_export_loss_eur", "command_mode",
              "command_flow", "command_target_w", "planned_flow",
              "planned_power_w", "optimizer_attributed", "attribution"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for hour in range(168):
            local_hour = datetime(2026, 9, 14, 14) + timedelta(hours=hour)
            for arm in ("self_consumption", "persistence", "irradiance"):
                optimized = arm != "self_consumption"
                writer.writerow({
                    "site": "1", "time": local_hour.strftime("%Y-%m-%d %H:%M:%S"),
                    "arm": arm, "solver_status": "optimal" if optimized else "not_applicable",
                    "fallback": "False", "pv_w": "1200", "load_w": "800",
                    "buy": "0.3", "sell": "0.1", "flow": "charge", "power_w": "400",
                    "soc_before": "50", "soc_after": "51", "grid_import_kwh": "0",
                    "grid_export_kwh": "0", "cost_eur": "0.02",
                    "negative_export_loss_eur": "0", "command_mode": "charge",
                    "command_flow": "charge", "command_target_w": "400",
                    "planned_flow": "charge", "planned_power_w": "400",
                    "optimizer_attributed": str(optimized),
                    "attribution": "optimizer-attributed" if optimized else "non-optimizer-attributed",
                })
    return path


def test_w1_mapping_is_read_only_and_preserves_local_hour(tmp_path, trace):
    sha = hashlib.sha256(trace.read_bytes()).hexdigest()
    app = create_app(tmp_path / "app.sqlite", offline_trace_path=trace, offline_trace_sha256=sha)
    with TestClient(app) as client:
        summary = client.get("/api/offline-replay").json()
        assert summary["time_basis"] == "dataset_local_hour"
        assert summary["hours_per_arm"] == 168
        assert summary["official_score_claim"] is False
        assert summary["negative_price_rows"] == 0
        detail = client.get("/api/offline-replay/steps/0?arm=irradiance").json()
        with trace.open(encoding="utf-8", newline="") as stream:
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


def test_w1_adapter_rejects_source_mismatch_and_missing_configuration(tmp_path, trace):
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        OfflineReplay(trace, "0" * 64)
    with TestClient(create_app(tmp_path / "empty.sqlite")) as client:
        assert client.get("/api/offline-replay").status_code == 404


def test_legacy_trace_missing_diagnostics_is_unknown_without_solver_inference(tmp_path, trace):
    legacy = tmp_path / "legacy.csv"
    with trace.open(encoding="utf-8", newline="") as source, legacy.open("w", encoding="utf-8", newline="") as target:
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
