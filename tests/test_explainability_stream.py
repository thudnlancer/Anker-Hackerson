"""Durable outbox and readonly collection invariants; fake credentials, no HTTP."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from energy_lab.explainability_stream import EvidenceConflict, ReadOnlyStream, StreamCheckpoint
import run_explainability_stream as runner


WALL = datetime(2026, 10, 13, tzinfo=timezone.utc)
TICK = "2031-01-01T00:15:00+00:00"


def config():
    return {"ha_base": "http://unit.test:8123", "ha_username": "unit-user", "ha_password": "PRIVATE-HA-PASSWORD",
            "sim_base": "http://unit.test:45678", "sim_username": "unit-sim", "sim_password": "PRIVATE-SIM-PASSWORD",
            "stations": {str(site): {"suffix": f"anker_solix_solarbank_max_ac_unit_{site}"} for site in (1, 2, 3)},
            "allow_ha_write": True, "live_enabled": True, "allow_formal_contest": True}


def snapshot(wall=WALL, tick=TICK):
    return {"prices": {"today": {"points": [{"time": "2031-01-01T00:00:00Z", "import_price": .2, "export_price": .1}]}},
            "stations": {str(site): {"received_at": wall.isoformat(), "state": {"soc": "45", "solar_power": "400",
                "home_load": "1000", "device_time": tick, "device_time_last_changed": wall.isoformat(),
                "ha_token": "PRIVATE-STATE-TOKEN"}, "weather": {"unavailable": True}} for site in (1, 2, 3)},
            "configuration": {"password": "PRIVATE-SNAPSHOT-PASSWORD"}}


def record(run="unit-stream", site="1", tick=TICK, observed=WALL.isoformat()):
    digest = hashlib.sha256((run + "\0" + site + "\0" + tick).encode()).hexdigest()[:24]
    return {"schema_version": 1, "run_id": run, "site_id": site, "decision_id": "tick-" + digest,
            "source_kind": "external", "model_version": "unit-model", "policy_version": "unit-policy", "solver_status": "optimal",
            "snapshot": {"device_time": tick, "observed_at": observed, "soc_percent": 45., "pv_kw": .4, "load_kw": 1.,
                "capacity_kwh": 14., "max_charge_kw": 5., "max_discharge_kw": 5., "step_hours": .25,
                "soc_min_percent": 0., "soc_max_percent": 100., "charge_efficiency": .95, "discharge_efficiency": 1.,
                "pv_forecast_kw": [.4] * 96, "load_forecast_kw": [1.] * 96, "import_eur_kwh": [.2] * 96,
                "export_eur_kwh": [.1] * 96, "terminal_eur_kwh": .2, "degradation_eur_kwh": .02,
                "sources": {"pv": "synthetic", "load": "synthetic", "pv_forecast": "synthetic", "load_forecast": "synthetic"},
                "history_mode": "none", "cache_tail": None, "forecast_status": "ready", "fallback_reason": None,
                "units": {"power": "kW", "energy": "kWh", "price": "EUR/kWh", "cost": "EUR"}},
            "action": {"kind": "hold", "power_kw": -0., "control_semantics": "self_consumption"},
            "cost": {"purchase_eur": 2.88, "export_revenue_eur": 0., "degradation_eur": 0., "terminal_credit_eur": 1.26, "objective_eur": 1.62},
            "provenance": {"origin": "external_adapter", "input_kind": "synthetic",
                "references": ["https://unit.test/repo", "source.py@sha256:unit", "capture:/tmp/capture-one.json"], "note": "unit fixture"}}


def fake_evaluate(data, *, evidence_sink, evidence_identity, **kwargs):
    results = {}
    for site, item in data["stations"].items():
        current = record(evidence_identity["run_id"], site, item["state"]["device_time"], item["received_at"])
        current["snapshot"]["soc_percent"] = float(item["state"]["soc"])
        try:
            evidence_sink(current)
            results[site] = {"status": "candidate_only", "writes_performed": 0}
        except Exception as exc:
            results[site] = {"status": "candidate_only", "explainability_export": {"status": "failed", "error_type": type(exc).__name__}}
    return {"stations": results}


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "checkpoint.sqlite"
        self.checkpoint = StreamCheckpoint(self.path)
        self.checkpoint.bind("unit-stream", config()["stations"])

    def tearDown(self):
        self.temp.cleanup()

    def test_semantic_duplicate_preserves_first_bytes_hash_and_wall_receipt(self):
        first = record()
        key, _ = self.checkpoint.stage(first)
        original = self.checkpoint.pending("unit-stream")[0]
        duplicate = deepcopy(first)
        duplicate["snapshot"]["observed_at"] = "2026-10-13T00:00:05Z"
        duplicate["provenance"]["references"][-1] = "capture:/tmp/capture-two.json"
        duplicate["action"]["power_kw"] = 0
        duplicate["snapshot"]["capacity_kwh"] = 14
        self.assertEqual(self.checkpoint.stage(duplicate), (key, "pending"))
        self.assertEqual(self.checkpoint.pending("unit-stream"), [original])
        self.assertEqual(original["payload_sha256"], hashlib.sha256(original["payload"].encode()).hexdigest())
        self.checkpoint.delivered(key)
        restarted = StreamCheckpoint(self.path)
        self.assertEqual(restarted.stage(duplicate), (key, "duplicate"))

    def test_every_material_input_action_cost_source_or_model_change_conflicts(self):
        self.checkpoint.stage(record())
        for field in ("soc", "forecast", "action", "cost", "source", "model", "source_reference"):
            with self.subTest(field=field):
                changed = record()
                if field == "soc": changed["snapshot"]["soc_percent"] = 46
                if field == "forecast": changed["snapshot"]["pv_forecast_kw"][1] = .5
                if field == "action": changed["action"]["power_kw"] = .1
                if field == "cost": changed["cost"]["objective_eur"] += .001
                if field == "source": changed["snapshot"]["sources"]["pv"] = "observed"
                if field == "model": changed["model_version"] = "unit-model-next"
                if field == "source_reference": changed["provenance"]["references"][1] = "source.py@sha256:other"
                with self.assertRaises(EvidenceConflict): self.checkpoint.stage(changed)
        self.assertEqual(len(self.checkpoint.pending("unit-stream")), 1)
        self.assertEqual(json.loads(self.checkpoint.pending("unit-stream")[0]["payload"]), record())

    def test_sites_and_runs_isolate_ticks_and_mapping_change_requires_new_run(self):
        self.checkpoint.stage(record())
        self.checkpoint.stage(record(site="2"))
        self.checkpoint.bind("other-run", config()["stations"])
        self.checkpoint.stage(record(run="other-run"))
        self.assertEqual(len(self.checkpoint.pending("unit-stream")), 2)
        self.assertEqual(len(self.checkpoint.pending("other-run")), 1)
        cfg = config(); cfg["stations"]["1"]["suffix"] = "unit-other-device"
        with self.assertRaisesRegex(ValueError, "new run_id"): self.checkpoint.bind("unit-stream", cfg["stations"])

    def test_unknown_configuration_fields_are_rejected_before_persistence(self):
        for path in ("top", "snapshot", "sources", "action"):
            value = record()
            target = value if path == "top" else value["snapshot"]["sources"] if path == "sources" else value[path]
            target["ha_password"] = "PRIVATE-INJECTED-PASSWORD"
            with self.assertRaises(ValueError): self.checkpoint.stage(value)
        self.assertEqual(self.checkpoint.pending("unit-stream"), [])
        self.assertNotIn(b"PRIVATE-INJECTED-PASSWORD", self.path.read_bytes())


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "checkpoint.sqlite"
        self.now = WALL
        self.data = snapshot()
        self.records, self.statuses, self.sleeps = [], [], []

    def tearDown(self): self.temp.cleanup()

    def collector(self, **kwargs):
        options = {"run_id": "unit-stream", "checkpoint": StreamCheckpoint(self.path), "record_sink": self.records.append,
                   "status_sink": self.statuses.append, "capture": lambda cfg: deepcopy(self.data), "evaluate": fake_evaluate,
                   "clock": lambda: self.now, "sleep": self.sleeps.append}
        options.update(kwargs)
        return ReadOnlyStream(config(), **options)

    def test_three_stations_wall_clock_status_and_same_tick_repeat(self):
        collector = self.collector()
        first = collector.collect_once()
        self.assertEqual(first["state"], "running")
        self.assertEqual(first["last_success_at"], WALL.isoformat())
        self.assertEqual(len(self.records), 3)
        self.assertGreater(self.records[0]["snapshot"]["device_time"], first["updated_at"])
        self.now += timedelta(seconds=5)
        self.data = snapshot(self.now)
        second = collector.collect_once()
        self.assertEqual(second["state"], "running")
        self.assertEqual(second["last_success_at"], self.now.isoformat())
        self.assertEqual(len(self.records), 3)
        self.assertEqual(second["cycle"], 2)
        for key in ("owner_id", "started_at", "last_error_at", "last_error", "poll_interval_seconds", "freshness_threshold_seconds", "heartbeat_expiry_seconds"):
            self.assertIn(key, second)

    def test_bad_site_isolated_and_freshness_rechecked_after_previously_good_round(self):
        collector = self.collector()
        collector.collect_once()
        success_at = collector.last_status["last_success_at"]
        self.now += timedelta(seconds=20)
        self.data = snapshot(self.now, "2031-01-01T00:30:00+00:00")
        self.data["stations"]["2"]["state"]["soc"] = "unavailable PRIVATE-OBSERVATION"
        self.data["stations"]["3"]["state"]["device_time_last_changed"] = WALL.isoformat()
        status = collector.collect_once()
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["last_success_at"], success_at)
        self.assertEqual([record["site_id"] for record in self.records[3:]], ["1"])
        self.assertEqual(status["sites"]["3"]["reason"], "stale_device_clock")
        self.assertNotIn("PRIVATE-OBSERVATION", json.dumps(status))
        # Retaining an old snapshot cannot become fresh simply because a prior
        # round succeeded or an accelerated device tick is far in the future.
        self.now += timedelta(seconds=20)
        self.assertEqual(collector.collect_once()["state"], "degraded")
        self.assertEqual(len(self.records), 4)

    def test_material_conflict_blocks_one_site_without_overwriting_original(self):
        collector = self.collector()
        collector.collect_once()
        self.now += timedelta(seconds=5); self.data = snapshot(self.now)
        self.data["stations"]["1"]["state"]["soc"] = "46"
        status = collector.collect_once()
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["sites"]["1"]["reason"], "evidence_conflict")
        self.assertEqual(len(self.records), 3)
        self.assertEqual(self.records[0]["snapshot"]["soc_percent"], 45)

    def test_pending_delivery_survives_restart_and_reuses_original_wall_receipt(self):
        failing = Mock(side_effect=RuntimeError("PRIVATE-DELIVERY-ERROR"))
        first = self.collector(record_sink=failing)
        self.assertEqual(first.collect_once()["state"], "degraded")
        self.assertEqual(len(first.checkpoint.pending("unit-stream")), 3)
        self.now += timedelta(seconds=5); self.data = snapshot(self.now)
        restarted = self.collector()
        status = restarted.collect_once()
        self.assertEqual(status["state"], "running")
        self.assertEqual(len(self.records), 3)
        self.assertEqual(restarted.checkpoint.pending("unit-stream"), [])
        self.assertTrue(all(item["snapshot"]["observed_at"] == WALL.isoformat() for item in self.records))
        self.assertNotIn("PRIVATE-DELIVERY-ERROR", json.dumps(self.statuses))

    def test_clock_regression_preflights_all_sites_before_delivery_and_persists_stop(self):
        collector = self.collector(); collector.collect_once()
        self.now += timedelta(seconds=5)
        self.data = snapshot(self.now, "2031-01-01T00:30:00+00:00")
        self.data["stations"]["3"]["state"].update(device_time="2031-01-01T00:00:00+00:00", soc="unavailable")
        status = collector.collect_once()
        self.assertEqual((status["state"], status["reason"]), ("stopped", "clock_regression"))
        self.assertEqual(len(self.records), 3)
        capture = Mock(side_effect=AssertionError("old run should not capture again"))
        self.assertEqual(self.collector(capture=capture).collect_once()["reason"], "clock_regression")
        capture.assert_not_called()
        self.data = snapshot(self.now)
        self.assertEqual(self.collector(run_id="new-run").collect_once()["state"], "running")

    def test_naive_device_coordinate_from_api_kept_separate_from_wall_clock(self):
        self.data = snapshot(tick="2031-01-01T00:15:00")
        # Real producer stamps UTC after its same API-coordinate alignment.
        def evaluate(data, **kwargs):
            data = deepcopy(data)
            for item in data["stations"].values(): item["state"]["device_time"] += "+00:00"
            return fake_evaluate(data, **kwargs)
        status = self.collector(evaluate=evaluate).collect_once()
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["sites"]["1"]["device_tick"], TICK)

    def test_auth_failure_stops_without_repeated_password_attempts(self):
        error = requests.HTTPError("PRIVATE-HTTP-AUTH", response=Mock(status_code=401))
        capture = Mock(side_effect=error)
        collector = self.collector(capture=capture)
        status = collector.run(cycles=3)
        self.assertEqual((status["state"], status["reason"]), ("stopped", "auth_failed"))
        capture.assert_called_once()
        self.assertEqual(self.sleeps, [])
        self.assertNotIn("PRIVATE-HTTP-AUTH", json.dumps(status))

    def test_transient_capture_uses_bounded_backoff_and_bounded_cycles_stop_status(self):
        capture = Mock(side_effect=[requests.Timeout("PRIVATE-REQUEST"), requests.Timeout(), deepcopy(self.data)])
        collector = self.collector(capture=capture)
        status = collector.run(cycles=3)
        self.assertEqual(capture.call_count, 3)
        self.assertEqual(self.sleeps, [10., 20.])
        self.assertEqual((status["state"], status["reason"]), ("stopped", "cycles_completed"))
        self.assertEqual(status["cycle"], 3)
        self.assertEqual(collector.checkpoint.state("unit-stream")["cycle"], 3)
        self.assertEqual(status["last_success_at"], WALL.isoformat())
        self.assertEqual(len(self.records), 3)

    def test_interrupted_lifecycle_does_not_increment_capture_count(self):
        def interrupt(delay): raise KeyboardInterrupt()
        collector = self.collector(sleep=interrupt)
        status = collector.run(cycles=3)
        self.assertEqual((status["state"], status["reason"], status["cycle"]), ("stopped", "interrupted", 1))
        self.assertEqual(collector.checkpoint.state("unit-stream")["cycle"], 1)

    def test_missing_evidence_and_long_solve_cannot_claim_healthy(self):
        no_evidence = lambda data, **kwargs: {"stations": {site: {"status": "candidate_only"} for site in data["stations"]}}
        self.assertEqual(self.collector(evaluate=no_evidence).collect_once()["state"], "degraded")
        self.assertEqual(self.records, [])
        def late(data, **kwargs):
            self.now += timedelta(seconds=20)
            return fake_evaluate(data, **kwargs)
        self.assertEqual(self.collector(evaluate=late).collect_once()["state"], "degraded")
        self.assertEqual(self.records, [])

    def test_upstream_solver_outcomes_keep_timeout_and_infeasible_distinct_without_decisions(self):
        from energy_lab.shadow import evaluate_snapshot
        self.data["prices"] = {name: {"points": [
            {"time": f"{date}T{hour:02d}:00:00Z", "import_price": .2, "export_price": .1}
            for hour in range(24)]} for name, date in
            (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
        cases = (("limit_reached", "timeout", "solver_timeout", "SolverTimeLimit"),
                 ("timeout", "timeout", "solver_timeout", "SolverTimeLimit"),
                 ("infeasible", "infeasible", "solver_infeasible", "SolverInfeasible"),
                 ("solver_error", "failed", "solver_failed", "SolverFailure"),
                 ("unbounded", "failed", "solver_failed", "SolverFailure"))
        sink = Mock()
        for upstream, normalized, reason, error in cases:
            with self.subTest(upstream=upstream), patch("energy_lab.shadow.optimize_window",
                return_value=SimpleNamespace(status=upstream, message="PRIVATE-SOLVER-MESSAGE")) as solve:
                collector = self.collector(evaluate=evaluate_snapshot, record_sink=sink)
                status = collector.collect_once()
                self.assertEqual(solve.call_count, 3)
                self.assertEqual(status["state"], "degraded")
                self.assertEqual(status["last_error"], error)
                for site in status["sites"].values():
                    self.assertEqual(site["solver_status"], normalized)
                    self.assertEqual(site["reason"], reason)
                    self.assertEqual(site["error_type"], error)
                self.assertEqual(collector.checkpoint.pending("unit-stream"), [])
                with collector.checkpoint._connect() as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM stream_records").fetchone()[0], 0)
                self.assertNotIn("PRIVATE-", json.dumps(status))
        sink.assert_not_called()

    def test_one_timeout_does_not_hide_another_station_success_or_infeasibility(self):
        def evaluate(data, **kwargs):
            site = next(iter(data["stations"]))
            if site == "1":
                return fake_evaluate(data, **kwargs)
            return {"stations": {site: {"status": "limit_reached" if site == "2" else "infeasible",
                "candidate": None, "message": "PRIVATE-UPSTREAM-BODY"}}}
        collector = self.collector(evaluate=evaluate)
        status = collector.collect_once()
        self.assertEqual(status["sites"]["1"]["status"], "collected")
        self.assertEqual(status["sites"]["2"]["solver_status"], "timeout")
        self.assertEqual(status["sites"]["2"]["reason"], "solver_timeout")
        self.assertEqual(status["sites"]["3"]["solver_status"], "infeasible")
        self.assertEqual(status["sites"]["3"]["reason"], "solver_infeasible")
        self.assertEqual([record["site_id"] for record in self.records], ["1"])
        self.assertNotIn("PRIVATE-", json.dumps(status))

    def test_candidate_export_failure_retains_only_known_failure_code(self):
        for code in ("negative_solver_power", "solver_cost_mismatch", "PRIVATE-EXTERNAL-CODE"):
            def evaluate(data, **kwargs):
                return {"stations": {site: {"status": "candidate_only", "explainability_export": {
                    "status": "failed", "failure_code": code, "error_type": "PRIVATE-ERROR-TYPE",
                    "message": "PRIVATE-EXCEPTION-BODY"}} for site in data["stations"]}}
            with self.subTest(code=code):
                collector = self.collector(evaluate=evaluate)
                status = collector.collect_once()
                for site in status["sites"].values():
                    self.assertEqual(site["solver_status"], "candidate_only")
                    self.assertEqual(site["reason"], "evidence_unavailable")
                    if code.startswith("PRIVATE-"):
                        self.assertNotIn("failure_code", site)
                        self.assertEqual(site["error_type"], "EvidenceExportFailure")
                    else:
                        self.assertEqual(site["failure_code"], code)
                        self.assertEqual(site["error_type"], "EvidenceExportError")
                self.assertEqual(self.records, [])
                self.assertNotIn("PRIVATE-", json.dumps(status))

    def test_export_failure_code_is_accepted_by_the_real_collection_status_store(self):
        sys.path.insert(0, str(ROOT / "explainability-app" / "src"))
        from energy_explain.store import Store
        store = Store(Path(self.temp.name) / "failure-status.sqlite")
        def evaluate(data, **kwargs):
            return {"stations": {site: {"status": "candidate_only", "explainability_export": {
                "status": "failed", "failure_code": "solver_cost_mismatch",
                "error_type": "EvidenceExportError"}} for site in data["stations"]}}
        collector = self.collector(evaluate=evaluate, status_sink=store.update_collection_status)
        status = collector.collect_once()
        self.assertEqual(status["state"], "degraded")
        saved = store.collection_status("unit-stream", wall=self.now)
        self.assertEqual(saved["sites"]["1"]["failure_code"], "solver_cost_mismatch")
        self.assertEqual(saved["sites"]["1"]["error_type"], "EvidenceExportError")
        self.assertEqual(store.decisions("unit-stream"), [])

    def test_blocked_input_reason_is_fixed_or_whitelisted(self):
        for reason in ("invalid_price_window", "PRIVATE-UPSTREAM-REASON", {"PRIVATE": "BODY"}):
            def evaluate(data, **kwargs):
                return {"stations": {site: {"status": "blocked", "reason": reason,
                    "error_type": "PRIVATE-ERROR-TYPE"} for site in data["stations"]}}
            with self.subTest(reason=reason):
                status = self.collector(evaluate=evaluate).collect_once()
                for site in status["sites"].values():
                    self.assertEqual(site["solver_status"], "blocked")
                    self.assertEqual(site["reason"], "invalid_price_window" if reason == "invalid_price_window" else "solver_blocked")
                    self.assertEqual(site["error_type"], "SolverInputRejected")
                self.assertNotIn("PRIVATE-", json.dumps(status))
    def test_default_capture_caches_ha_token_only_in_memory_and_never_mutates_caller_config(self):
        cfg = config(); original = deepcopy(cfg)
        with patch("energy_lab.explainability_stream.exchange_ha_token", return_value="PRIVATE-CACHED-TOKEN") as exchange, \
             patch("energy_lab.explainability_stream._fetch", return_value=self.data) as capture:
            collector = ReadOnlyStream(cfg, run_id="unit-stream", checkpoint=StreamCheckpoint(self.path),
                record_sink=self.records.append, status_sink=self.statuses.append, evaluate=fake_evaluate, clock=lambda: self.now)
            collector.collect_once(); collector.collect_once()
        exchange.assert_called_once()
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(cfg, original)
        for marker in ("PRIVATE-CACHED-TOKEN", "PRIVATE-HA-PASSWORD", "PRIVATE-SIM-PASSWORD", "PRIVATE-STATE-TOKEN", "PRIVATE-SNAPSHOT-PASSWORD"):
            self.assertNotIn(marker, json.dumps(self.records + self.statuses))
            self.assertNotIn(marker.encode(), self.path.read_bytes())

    def test_status_owner_refusal_stops_before_auth_capture_and_pending_delivery(self):
        checkpoint = StreamCheckpoint(self.path)
        checkpoint.bind("unit-stream", config()["stations"])
        checkpoint.stage(record())
        capture = Mock(side_effect=AssertionError("a refused owner must not capture"))
        status_sink = Mock(side_effect=RuntimeError("PRIVATE-OWNER-ERROR"))
        collector = self.collector(capture=capture, status_sink=status_sink)
        with patch("energy_lab.explainability_stream.exchange_ha_token") as auth:
            status = collector.run(cycles=3)
        self.assertEqual((status["state"], status["reason"]), ("stopped", "status_sink_failed"))
        capture.assert_not_called(); auth.assert_not_called()
        self.assertEqual(self.records, [])
        self.assertEqual(len(checkpoint.pending("unit-stream")), 1)
        self.assertNotIn("PRIVATE-OWNER-ERROR", json.dumps(status))

    def test_lost_owner_between_solve_and_delivery_stops_remaining_sites(self):
        # Startup, post-capture, clock mutation and outbox staging succeed;
        # ownership is then lost before the import callback is attempted.
        statuses = Mock(side_effect=[None] * 4 + [RuntimeError("PRIVATE-STORE-ERROR")])
        collector = self.collector(status_sink=statuses)
        status = collector.collect_once()
        self.assertEqual(status["reason"], "status_sink_failed")
        self.assertEqual(self.records, [])
        self.assertEqual(len(collector.checkpoint.pending("unit-stream")), 1)
        self.assertEqual(statuses.call_count, 5)

    def test_old_owner_returning_from_capture_cannot_poison_successor_clock_or_outbox(self):
        sys.path.insert(0, str(ROOT / "explainability-app" / "src"))
        from energy_explain.schemas import DecisionIn
        from energy_explain.store import Store

        clock = [WALL]
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None): return clock[0]

        store = Store(Path(self.temp.name) / "shared-app.sqlite")
        checkpoint = StreamCheckpoint(self.path)
        sink = lambda item: store.add_decision(DecisionIn.model_validate(item))
        successor = []

        def old_capture(cfg):
            old = snapshot(WALL + timedelta(seconds=29), TICK)
            clock[0] = WALL + timedelta(seconds=31)
            newer = snapshot(clock[0], "2031-01-01T00:30:00+00:00")
            collector_b = self.collector(checkpoint=checkpoint, record_sink=sink,
                status_sink=store.update_collection_status, owner_id="owner-b",
                clock=lambda: clock[0], capture=lambda cfg: deepcopy(newer))
            self.assertEqual(collector_b.collect_once()["state"], "running")
            successor.append(collector_b)
            return old

        with patch("energy_explain.store.datetime", Frozen):
            old_owner = self.collector(checkpoint=checkpoint, record_sink=sink,
                status_sink=store.update_collection_status, owner_id="owner-a",
                clock=lambda: clock[0], capture=old_capture)
            status = old_owner.collect_once()
            self.assertEqual(status["reason"], "status_sink_failed")
            self.assertIsNone(checkpoint.state("unit-stream")["blocked_reason"])
            self.assertEqual(checkpoint.state("unit-stream")["cycle"], 1)
            with sqlite3.connect(self.path) as db:
                clocks = [row[0] for row in db.execute("SELECT tick FROM stream_clocks")]
            self.assertEqual(clocks, ["2031-01-01T00:30:00+00:00"] * 3)
            self.assertEqual(checkpoint.pending("unit-stream"), [])
            self.assertEqual(successor[0].collect_once()["state"], "running")
            self.assertEqual(store.collection_status("unit-stream", wall=clock[0])["owner_id"], "owner-b")
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM decisions").fetchone()[0], 3)

    def test_outbox_corruption_is_detected_before_restore_or_repeat_delivery(self):
        checkpoint = StreamCheckpoint(self.path)
        checkpoint.bind("unit-stream", config()["stations"])
        checkpoint.stage(record())
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE stream_records SET payload=replace(payload, 'unit-model', 'wrong-model')")
        status = self.collector().collect_once()
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["sites"]["1"]["error_type"], "PayloadIntegrityError")
        self.assertEqual([item["site_id"] for item in self.records], ["2", "3"])
        self.assertEqual(len(checkpoint.pending("unit-stream")), 1)

    def test_cli_defaults_and_mutually_exclusive_bounds(self):
        options = runner.parser().parse_args(["--run-id", "unit-run", "--once"])
        self.assertEqual(options.poll, 5.)
        self.assertEqual(options.db, Path("explainability-app/data/energy.sqlite"))
        self.assertEqual(options.checkpoint, Path("outputs/explainability-stream/checkpoint.sqlite"))
        with patch("sys.stderr"):
            with self.assertRaises(SystemExit): runner.parser().parse_args(["--run-id", "unit-run", "--once", "--cycles", "3"])
        for value in (0., -1., float("nan"), float("inf")):
            with self.assertRaises(ValueError): self.collector(poll_interval_seconds=value)
        with self.assertRaises(ValueError): self.collector(heartbeat_expiry_seconds=0)

    def test_duplicate_entity_mapping_cannot_fabricate_three_stations(self):
        cfg = config()
        cfg["stations"]["2"]["suffix"] = cfg["stations"]["1"]["suffix"]
        with self.assertRaisesRegex(ValueError, "three explicit"):
            ReadOnlyStream(cfg, run_id="unit-stream", checkpoint=StreamCheckpoint(self.path), record_sink=self.records.append)

    def test_actual_shared_solver_and_store_capture_same_solve_without_network(self):
        sys.path.insert(0, str(ROOT / "explainability-app" / "src"))
        from energy_explain.schemas import DecisionIn
        from energy_explain.store import Store
        from energy_lab.shadow import evaluate_snapshot

        self.now = datetime.now(timezone.utc)
        self.data = snapshot(self.now)
        self.data["prices"] = {name: {"points": [{"time": f"{date}T{hour:02d}:00:00Z", "import_price": .2, "export_price": .1}
                for hour in range(24)]} for name, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
        store = Store(Path(self.temp.name) / "app.sqlite")
        collector = self.collector(evaluate=evaluate_snapshot, solver_seconds=10,
            record_sink=lambda item: store.add_decision(DecisionIn.model_validate(item)), status_sink=store.update_collection_status)
        first = collector.collect_once()
        self.assertEqual(first["state"], "running", first)
        with store.connect() as db:
            count = db.execute("SELECT count(*) FROM decisions").fetchone()[0]
            values = [json.loads(row[0]) for row in db.execute("SELECT payload FROM decisions")]
        self.assertEqual(count, 3)
        self.assertEqual({item["site_id"] for item in values}, {"1", "2", "3"})
        self.assertTrue(all(item["snapshot"]["step_hours"] == .25 for item in values))
        self.assertTrue(all(item["provenance"]["input_kind"] == "mixed" for item in values))
        second = collector.collect_once()
        self.assertEqual(second["state"], "running", second)
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM decisions").fetchone()[0], 3)
        refused = self.collector(evaluate=evaluate_snapshot, status_sink=store.update_collection_status,
            capture=Mock(side_effect=AssertionError("active owner must prevent capture")))
        self.assertEqual(refused.collect_once()["reason"], "status_sink_failed")


if __name__ == "__main__": unittest.main()
