"""Recoverable read-only evidence collection; no device-control or pickle loads.

The sink must be idempotent by run/site/decision identity. Accepted business
evidence is an immutable outbox: interrupted delivery is retried on restart.
Only receipt wall time and explicitly tagged capture paths are nonmaterial.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable
import uuid

import pandas as pd
import requests

from .config import exchange_ha_token, is_placeholder, require_runtime_fields
from .interface_contract import state_is_fresh
from .shadow import _infer_api_timezone, daily_points, evaluate_snapshot, validate_state

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_REASONS = {"stale_snapshot", "stale_device_clock", "invalid_device_clock", "invalid_device_observation",
            "invalid_price_window", "invalid_power_forecast", "optimizer_to_command_contract_mismatch"}
_RECORD_KEYS = {"schema_version", "run_id", "site_id", "decision_id", "source_kind", "model_version",
                "policy_version", "solver_status", "snapshot", "action", "cost", "replay_context", "provenance"}
_SNAPSHOT_KEYS = {"device_time", "observed_at", "soc_percent", "pv_kw", "load_kw", "capacity_kwh",
                  "max_charge_kw", "max_discharge_kw", "step_hours", "soc_min_percent", "soc_max_percent",
                  "charge_efficiency", "discharge_efficiency", "pv_forecast_kw", "load_forecast_kw",
                  "import_eur_kwh", "export_eur_kwh", "terminal_eur_kwh", "degradation_eur_kwh", "sources",
                  "history_mode", "cache_tail", "forecast_status", "fallback_reason", "units"}


def _utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("explicit wall-clock timezone required")
    return stamp.tz_convert("UTC").isoformat()


def _utcnow():
    return datetime.now(timezone.utc)


def _positive(value, name):
    if isinstance(value, bool):
        raise ValueError(name + " must be finite and positive")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")
    return value


def _checked_record(record):
    """Reject accidental extra configuration/state fields before persistence."""
    if not isinstance(record, dict) or set(record) - _RECORD_KEYS:
        raise ValueError("business record fields required")
    for key in ("run_id", "site_id", "decision_id"):
        if not isinstance(record.get(key), str) or not _ID.fullmatch(record[key]):
            raise ValueError("invalid evidence identity")
    if not isinstance(record.get("snapshot"), dict) or set(record["snapshot"]) - _SNAPSHOT_KEYS:
        raise ValueError("business snapshot fields required")
    whitelist = {
        "action": {"kind", "power_kw", "control_semantics"},
        "cost": {"purchase_eur", "export_revenue_eur", "degradation_eur", "terminal_credit_eur", "objective_eur"},
        "provenance": {"origin", "input_kind", "references", "note"},
        "replay_context": {"upstream_source_sha256", "command_contract", "first_step"},
    }
    for name, keys in whitelist.items():
        value = record.get(name)
        if value is not None and (not isinstance(value, dict) or set(value) - keys):
            raise ValueError("business evidence fields required")
    for parent, key, keys in (
        (record["snapshot"], "sources", {"pv", "load", "pv_forecast", "load_forecast"}),
        (record["snapshot"], "units", {"power", "energy", "price", "cost"}),
        (record.get("replay_context") or {}, "first_step", {"charge_kw", "discharge_kw", "grid_import_kw", "grid_export_kw"}),
    ):
        if key in parent and (not isinstance(parent[key], dict) or set(parent[key]) - keys):
            raise ValueError("business evidence fields required")
    # Validate plain JSON and finite numbers without importing the application schema.
    payload = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return json.loads(payload), payload


def _semantic(record):
    value = deepcopy(record)
    value["snapshot"].pop("observed_at", None)
    provenance = value.get("provenance")
    if provenance:
        # Never discard repository/model/source references. Capture paths must
        # explicitly carry one of these tags to be treated as receipt metadata.
        provenance["references"] = [ref for ref in provenance.get("references", [])
            if not isinstance(ref, str) or not ref.startswith(("capture:", "snapshot:"))]

    def numbers(item):
        if isinstance(item, dict):
            return {key: numbers(val) for key, val in item.items()}
        if isinstance(item, list):
            return [numbers(val) for val in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return hashlib.sha256(json.dumps(numbers(value), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EvidenceConflict(ValueError):
    """A tick already has different material evidence; originals remain intact."""


class ClockRegression(ValueError):
    """The simulator/device reset; this run must not emit further decisions."""


class PayloadIntegrityError(ValueError):
    """An outbox payload no longer matches its accepted bytes/hash."""


class StatusDeliveryError(RuntimeError):
    """The status store rejected ownership or is unavailable; stop delivery."""


class StreamCheckpoint:
    """Small SQLite outbox and nonsecret cycle journal; no runtime config stored."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS stream_runs (
                    run_id TEXT PRIMARY KEY, site_binding TEXT NOT NULL,
                    last_success_at TEXT, last_error_at TEXT, last_error TEXT,
                    blocked_reason TEXT, cycle INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS stream_clocks (
                    run_id TEXT NOT NULL, site_id TEXT NOT NULL, tick TEXT NOT NULL,
                    PRIMARY KEY(run_id,site_id));
                CREATE TABLE IF NOT EXISTS stream_records (
                    run_id TEXT NOT NULL, site_id TEXT NOT NULL, device_tick TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0, error_type TEXT,
                    PRIMARY KEY(run_id,site_id,device_tick));
                CREATE TABLE IF NOT EXISTS stream_cycles (
                    run_id TEXT NOT NULL, cycle INTEGER NOT NULL, summary TEXT NOT NULL,
                    PRIMARY KEY(run_id,cycle));
                CREATE TABLE IF NOT EXISTS stream_conflicts (
                    run_id TEXT NOT NULL, site_id TEXT NOT NULL, device_tick TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, PRIMARY KEY(run_id,site_id,device_tick,fingerprint));
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def bind(self, run_id, stations):
        binding = hashlib.sha256(json.dumps({str(site): spec["suffix"] for site, spec in stations.items()},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO stream_runs(run_id,site_binding) VALUES(?,?)", (run_id, binding))
            if db.execute("SELECT site_binding FROM stream_runs WHERE run_id=?", (run_id,)).fetchone()[0] != binding:
                raise ValueError("run station mapping changed; use a new run_id")

    def state(self, run_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM stream_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def observe(self, run_id, ticks):
        """Atomically preflight every fresh station before any new delivery."""
        regressed = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for site, tick in ticks.items():
                row = db.execute("SELECT tick FROM stream_clocks WHERE run_id=? AND site_id=?", (run_id, site)).fetchone()
                if row and pd.Timestamp(tick) < pd.Timestamp(row[0]):
                    regressed = True
            if regressed:
                db.execute("UPDATE stream_runs SET blocked_reason='clock_regression' WHERE run_id=?", (run_id,))
            else:
                for site, tick in ticks.items():
                    db.execute("INSERT INTO stream_clocks VALUES(?,?,?) ON CONFLICT(run_id,site_id) DO UPDATE SET tick=excluded.tick", (run_id, site, tick))
        if regressed:
            raise ClockRegression("use a new run_id after device clock reset")

    def stage(self, record):
        record, payload = _checked_record(record)
        tick = _utc(record["snapshot"]["device_time"])
        key = (record["run_id"], record["site_id"], tick)
        fingerprint = _semantic(record)
        conflict = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM stream_records WHERE run_id=? AND site_id=? AND device_tick=?", key).fetchone()
            if row:
                conflict = row["fingerprint"] != fingerprint
                if conflict:
                    db.execute("INSERT OR IGNORE INTO stream_conflicts VALUES(?,?,?,?)", (*key, fingerprint))
                outcome = "duplicate" if row["delivered"] else "pending"
            else:
                db.execute("INSERT INTO stream_records(run_id,site_id,device_tick,payload,payload_sha256,fingerprint) VALUES(?,?,?,?,?,?)",
                           (*key, payload, hashlib.sha256(payload.encode()).hexdigest(), fingerprint))
                outcome = "pending"
        if conflict:
            raise EvidenceConflict("material evidence changed at the same device tick")
        return key, outcome

    def pending(self, run_id):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM stream_records WHERE run_id=? AND delivered=0 ORDER BY device_tick,site_id", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def delivered(self, key, error_type=None):
        with self._connect() as db:
            db.execute("UPDATE stream_records SET delivered=?,error_type=? WHERE run_id=? AND site_id=? AND device_tick=?",
                       (int(error_type is None), error_type, *key))

    def finish(self, run_id, status):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT cycle FROM stream_runs WHERE run_id=?", (run_id,)).fetchone()[0] + 1
            status["cycle"] = current
            db.execute("UPDATE stream_runs SET cycle=?,last_success_at=?,last_error_at=?,last_error=? WHERE run_id=?",
                       (current, status["last_success_at"], status["last_error_at"], status["last_error"], run_id))
            db.execute("INSERT INTO stream_cycles VALUES(?,?,?)", (run_id, current,
                json.dumps(status, ensure_ascii=False, allow_nan=False, separators=(",", ":"))))


_CAPTURE_MODULE = None


def _fetch(cfg):
    """Load the trusted read-only entry point, never the legacy runner's main."""
    global _CAPTURE_MODULE
    if _CAPTURE_MODULE is None:
        path = Path(__file__).resolve().parents[2] / "run_shadow_dry_run.py"
        spec = importlib.util.spec_from_file_location("energy_lab_readonly_capture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CAPTURE_MODULE = module
    return _CAPTURE_MODULE.fetch_snapshot_from_config(cfg, include_prices=True)


class ReadOnlyStream:
    """A three-station collector using normalized in-memory config and sinks.

    ``capture``/``evaluate``/``clock``/``sleep`` are injectable for offline tests.
    The default capture caches an exchanged HA access token only in this object.
    The caller's config, checkpoint, summaries and sinks never receive secrets.
    """

    def __init__(self, cfg: dict, *, run_id: str, checkpoint: StreamCheckpoint,
                 record_sink: Callable[[dict], object], status_sink: Callable[[dict], object] | None = None,
                 poll_interval_seconds=5., freshness_threshold_seconds=15., heartbeat_expiry_seconds=None,
                 solver_seconds=1., device_timezone=None, input_kind="mixed", capture=None,
                 evaluate=evaluate_snapshot, clock=_utcnow, sleep=time.sleep, owner_id=None):
        require_runtime_fields(cfg, write_enabled=False)
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise ValueError("invalid run_id")
        if (len(cfg["stations"]) != 3 or len({str(site) for site in cfg["stations"]}) != 3
                or len({spec["suffix"] for spec in cfg["stations"].values()}) != 3
                or any(not _ID.fullmatch(str(site)) for site in cfg["stations"])):
            raise ValueError("exactly three explicit station identities are required")
        if input_kind not in ("mixed", "synthetic") or not callable(record_sink):
            raise ValueError("explicit input_kind and callable sink required")
        self._cfg = deepcopy(cfg)
        self._cfg["stations"] = {str(key): value for key, value in self._cfg["stations"].items()}
        self.run_id, self.checkpoint = run_id, checkpoint
        self.record_sink, self.status_sink = record_sink, status_sink
        self.poll = _positive(poll_interval_seconds, "poll interval")
        self.max_age = _positive(freshness_threshold_seconds, "freshness threshold")
        self.expiry = _positive(max(30., 3 * self.poll) if heartbeat_expiry_seconds is None else heartbeat_expiry_seconds, "heartbeat expiry")
        if max(self.poll, self.max_age, self.expiry) > 3600:
            raise ValueError("collection status intervals cannot exceed 3600 seconds")
        self.solver_seconds = _positive(solver_seconds, "solver time")
        self.device_timezone, self.input_kind = device_timezone, input_kind
        self.capture, self.evaluate, self.clock, self.sleep = capture, evaluate, clock, sleep
        self.owner_id = owner_id or uuid.uuid4().hex
        self.started_at = _utc(clock())
        self.checkpoint.bind(run_id, self._cfg["stations"])
        self.failures = 0
        self.terminal = False
        self._auth_phase = False
        self.last_status = None
        self.last_round_success = False
        self._announced = False

    def _status(self, state, *, reason=None, error_type=None, sites=None, success=False, record_cycle=True):
        now = _utc(self.clock())
        previous = self.checkpoint.state(self.run_id)
        status = {"run_id": self.run_id, "owner_id": self.owner_id, "started_at": self.started_at,
                  "updated_at": now, "state": state, "reason": reason,
                  "last_success_at": now if success else previous["last_success_at"],
                  "last_error_at": now if error_type else previous["last_error_at"],
                  "last_error": error_type if error_type else previous["last_error"],
                  "error_type": error_type, "poll_interval_seconds": self.poll,
                  "freshness_threshold_seconds": self.max_age, "heartbeat_expiry_seconds": self.expiry,
                  "sites": sites or {}, "writes_performed": 0}
        status["cycle"] = previous["cycle"] + int(record_cycle)
        if self.status_sink:
            try:
                self.status_sink(deepcopy(status))
            except Exception as exc:
                # A refused owner must never go on to emit decisions. The
                # previous owner's external heartbeat remains authoritative.
                self.terminal = True
                self.last_round_success = False
                status.update(state="stopped", reason="status_sink_failed", error_type=type(exc).__name__,
                              last_error=type(exc).__name__, last_error_at=now,
                              last_success_at=previous["last_success_at"])
                self.last_status = status
                raise StatusDeliveryError("collection status was not accepted") from None
        if record_cycle:
            self.checkpoint.finish(self.run_id, status)
        self.last_status = status
        return status

    def _heartbeat(self):
        status = self._status("running", reason="collecting" if self._announced else "starting", record_cycle=False)
        self._announced = True
        return status

    def _payload(self, row):
        if hashlib.sha256(row["payload"].encode()).hexdigest() != row["payload_sha256"]:
            raise PayloadIntegrityError("outbox bytes changed")
        record = json.loads(row["payload"])
        _checked_record(record)
        return record

    def _deliver_pending(self):
        failures = {}
        for row in self.checkpoint.pending(self.run_id):
            key = row["run_id"], row["site_id"], row["device_tick"]
            try:
                record = self._payload(row)
                self._heartbeat()
                self.record_sink(record)
            except StatusDeliveryError:
                raise
            except Exception as exc:
                error = type(exc).__name__
                self._heartbeat()
                self.checkpoint.delivered(key, error)
                failures[row["site_id"]] = error
            else:
                self._heartbeat()
                self.checkpoint.delivered(key)
        return failures

    def _capture(self):
        if self.capture:
            return self.capture(deepcopy(self._cfg))
        # Keep the auth exchange phase explicit so malformed auth responses stop
        # instead of endlessly repeating bad password attempts every five seconds.
        if is_placeholder(self._cfg.get("ha_token")):
            self._auth_phase = True
            token = exchange_ha_token(self._cfg)
            if is_placeholder(token):
                raise RuntimeError("authentication did not return a token")
            self._cfg["ha_token"] = token
        self._auth_phase = False
        return _fetch(self._cfg)

    def collect_once(self):
        try:
            return self._collect_once()
        except StatusDeliveryError:
            return self.last_status

    def _collect_once(self):
        prior = self.checkpoint.state(self.run_id)
        if self.terminal:
            return self.last_status
        if prior["blocked_reason"]:
            self.terminal = True
            return self._status("stopped", reason=prior["blocked_reason"] or "stopped", record_cycle=False)
        self.last_round_success = False
        # Claim/refresh the external owner before authentication, capture or
        # recovery. A second process with this run cannot proceed after refusal.
        self._heartbeat()
        try:
            snapshot = self._capture()
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("stations"), dict):
                raise ValueError("invalid snapshot")
            price_points = daily_points(snapshot["prices"])
            # Capture may outlive our lease. A successor may already have
            # recorded a newer tick; never poison its clocks/outbox on return.
            self._heartbeat()
        except StatusDeliveryError:
            raise
        except Exception as exc:
            response = getattr(exc, "response", None)
            auth_failed = self._auth_phase or (response is not None and response.status_code in (401, 403))
            # A simulator auth response without its required token is terminal too.
            auth_failed = auth_failed or (isinstance(exc, KeyError) and exc.args == ("token",))
            self.terminal = bool(auth_failed)
            self.failures += 1
            return self._status("stopped" if auth_failed else "degraded", reason="auth_failed" if auth_failed else "capture_failed",
                                error_type=type(exc).__name__)
        self.failures = 0
        sites, ticks, valid = {}, {}, {}
        for site in self._cfg["stations"]:
            try:
                item = snapshot["stations"][site]
                wall_now = self.clock()
                if not state_is_fresh(item["received_at"], now=wall_now, max_age_seconds=self.max_age):
                    raise ValueError("stale_snapshot")
                if not state_is_fresh(item["state"].get("device_time_last_changed"), now=wall_now, max_age_seconds=self.max_age):
                    raise ValueError("stale_device_clock")
                tick = pd.Timestamp(item["state"]["device_time"])
                if tick.tzinfo is None:
                    coordinate = self.device_timezone or _infer_api_timezone(price_points, item.get("weather", {}))
                    if coordinate is None:
                        raise ValueError("invalid_device_clock")
                    tick = tick.tz_localize(coordinate)
                ticks[site] = _utc(tick)
                # An invalid SOC/power must not hide an independently fresh clock reset.
                validate_state(item["state"], item["received_at"], now=wall_now, max_age=self.max_age)
                valid[site] = item
                sites[site] = {"status": "validated", "device_tick": ticks[site], "observed_at": _utc(item["received_at"])}
            except Exception as exc:
                sites[site] = {"status": "blocked", "reason": str(exc) if str(exc) in _REASONS else "invalid_site_snapshot",
                               "error_type": type(exc).__name__}
        try:
            self._heartbeat()
            self.checkpoint.observe(self.run_id, ticks)
        except ClockRegression as exc:
            self.terminal = True
            return self._status("stopped", reason="clock_regression", error_type=type(exc).__name__, sites=sites)
        delivery_failures = self._deliver_pending()
        for site, item in valid.items():
            outcome = {}

            def evidence_sink(record):
                try:
                    if record.get("run_id") != self.run_id or record.get("site_id") != site:
                        raise ValueError("callback evidence identity mismatch")
                    if _utc(record["snapshot"]["device_time"]) != ticks[site] or _utc(record["snapshot"]["observed_at"]) != _utc(item["received_at"]):
                        raise ValueError("callback evidence clock mismatch")
                    # A long solve must not promote observations that became stale.
                    validate_state(item["state"], item["received_at"], now=self.clock(), max_age=self.max_age)
                    self._heartbeat()
                    key, stage = self.checkpoint.stage(record)
                    outcome["status"] = stage
                    if stage == "pending":
                        # Deliver the original stored payload even for a repeated
                        # tick with a new wall receipt. Do not replace its hash.
                        row = next(row for row in self.checkpoint.pending(self.run_id)
                                   if (row["run_id"], row["site_id"], row["device_tick"]) == key)
                        try:
                            original = self._payload(row)
                            self._heartbeat()
                            self.record_sink(original)
                        except StatusDeliveryError:
                            raise
                        except Exception as exc:
                            self._heartbeat()
                            self.checkpoint.delivered(key, type(exc).__name__)
                            raise
                        self._heartbeat()
                        self.checkpoint.delivered(key)
                        outcome["status"] = "delivered"
                except Exception as exc:
                    outcome.update(status="conflict" if isinstance(exc, EvidenceConflict) else "failed", error_type=type(exc).__name__)
                    raise

            try:
                result = self.evaluate({"prices": snapshot["prices"], "stations": {site: item}},
                    now=self.clock(), max_age=self.max_age, solver_seconds=self.solver_seconds,
                    device_timezone=self.device_timezone, evidence_sink=evidence_sink,
                    evidence_identity={"run_id": self.run_id, "input_kind": self.input_kind})["stations"][site]
                if self.terminal:
                    return self.last_status
                solver_state = result.get("status")
                sites[site]["solver_status"] = solver_state if solver_state in {"candidate_only", "infeasible", "timeout", "failed", "blocked"} else "failed"
                if outcome.get("status") in {"delivered", "duplicate"} and solver_state == "candidate_only":
                    sites[site]["status"] = "collected"
                else:
                    sites[site].update(status="blocked", reason="evidence_conflict" if outcome.get("status") == "conflict" else "evidence_unavailable",
                                      error_type=outcome.get("error_type") or result.get("explainability_export", {}).get("error_type"))
            except Exception as exc:
                if self.terminal:
                    return self.last_status
                sites[site].update(status="blocked", reason="evaluation_failed", error_type=type(exc).__name__)
        for site, error in delivery_failures.items():
            sites[site].update(status="blocked", reason="pending_delivery_failed", error_type=error)
        success = all(value["status"] == "collected" for value in sites.values())
        self.last_round_success = success
        errors = [value.get("error_type") for value in sites.values() if value.get("error_type")]
        return self._status("running" if success else "degraded", reason=None if success else "partial_collection",
                            error_type=errors[0] if errors else None if success else "IncompleteCollection", sites=sites, success=success)

    def run(self, *, cycles=None):
        """Run a bounded invocation or continue until interrupted/auth/reset stop."""
        if cycles is not None and (isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 1):
            raise ValueError("cycles must be a positive integer")
        completed = 0
        try:
            while cycles is None or completed < cycles:
                self.collect_once()
                completed += 1
                if self.terminal:
                    return self.last_status
                if cycles is None or completed < cycles:
                    delay = min(60., self.poll * 2 ** min(self.failures, 6)) if self.failures else self.poll
                    self.sleep(delay)
        except KeyboardInterrupt:
            self.terminal = True
            try:
                return self._status("stopped", reason="interrupted", record_cycle=False)
            except StatusDeliveryError:
                return self.last_status
        try:
            return self._status("stopped", reason="cycles_completed", record_cycle=False)
        except StatusDeliveryError:
            return self.last_status
