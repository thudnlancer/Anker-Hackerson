"""Append-only decision records and SQLite durable queue. No device connections."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

from .schemas import DecisionIn, ExecutionEvent, Intervention, PVAccountingIn, SettlementIn
from .collection import CollectionStatus, record_freshness


class Conflict(ValueError):
    pass


class NotFound(LookupError):
    pass


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS snapshots (
                hash TEXT PRIMARY KEY, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                site_id TEXT NOT NULL, decision_id TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL REFERENCES snapshots(hash), device_time TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(run_id, site_id, decision_id));
              CREATE INDEX IF NOT EXISTS decision_station ON decisions(run_id,site_id,device_time);
              CREATE TABLE IF NOT EXISTS execution_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, decision_key TEXT NOT NULL REFERENCES decisions(id),
                event_id TEXT NOT NULL, instruction_id TEXT NOT NULL, kind TEXT NOT NULL,
                issued_at TEXT NOT NULL, payload TEXT NOT NULL, received_at TEXT NOT NULL,
                UNIQUE(decision_key,event_id));
              CREATE TABLE IF NOT EXISTS settlements (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id),
                event_id TEXT NOT NULL, payload TEXT NOT NULL, received_at TEXT NOT NULL,
                UNIQUE(run_id,event_id));
              CREATE TABLE IF NOT EXISTS explanations (
                id TEXT PRIMARY KEY, decision_key TEXT NOT NULL REFERENCES decisions(id),
                template_version TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, decision_key TEXT NOT NULL REFERENCES decisions(id),
                cache_key TEXT NOT NULL, first_charge_kw REAL NOT NULL, status TEXT NOT NULL,
                result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0);
              CREATE INDEX IF NOT EXISTS queue_status ON jobs(status,created_at);
              CREATE TABLE IF NOT EXISTS plan_jobs (
                id TEXT PRIMARY KEY, decision_key TEXT NOT NULL REFERENCES decisions(id),
                cache_key TEXT NOT NULL, target_soc_percent REAL NOT NULL,
                deadline TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0);
              CREATE INDEX IF NOT EXISTS plan_queue_status ON plan_jobs(status,created_at);
              CREATE TABLE IF NOT EXISTS pv_daily (
                site_id TEXT NOT NULL, device_date TEXT NOT NULL, arm TEXT NOT NULL,
                payload TEXT NOT NULL, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(site_id,device_date,arm));
              CREATE TABLE IF NOT EXISTS collection_status (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS submission_receipts (
                decision_key TEXT NOT NULL REFERENCES decisions(id), instruction_id TEXT NOT NULL,
                payload TEXT NOT NULL, received_at TEXT NOT NULL,
                PRIMARY KEY(decision_key,instruction_id));
              CREATE TABLE IF NOT EXISTS readback_observations (
                decision_key TEXT NOT NULL REFERENCES decisions(id), observation_id TEXT NOT NULL,
                payload TEXT NOT NULL, result TEXT NOT NULL, received_at TEXT NOT NULL,
                PRIMARY KEY(decision_key,observation_id));
            """)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def add_decision(self, decision: DecisionIn) -> dict:
        data = decision.model_dump(mode="json")
        payload = canonical(data)
        identity = canonical([decision.run_id, decision.site_id, decision.decision_id])
        key = str(uuid5(NAMESPACE_URL, "energy-explain:" + identity))
        snapshot_hash = digest(data["snapshot"])
        with self.connect(write=True) as db:
            old = db.execute("SELECT payload FROM decisions WHERE id=?", (key,)).fetchone()
            if old:
                # JSON clients normalize IEEE -0.0 to 0; equivalent values are idempotent.
                # Keep the originally stored bytes and snapshot hash unchanged.
                if json.loads(old["payload"]) != data:
                    raise Conflict("decision identity already exists with different content")
                return {"id": key, "created": False}
            run = db.execute("SELECT source_kind FROM runs WHERE run_id=?", (decision.run_id,)).fetchone()
            if run and run["source_kind"] != decision.source_kind:
                raise Conflict("a run cannot mix demo and external provenance")
            db.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?)", (decision.run_id, decision.source_kind, now()))
            db.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?)", (snapshot_hash, canonical(data["snapshot"])))
            db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)", (key, decision.run_id,
                decision.site_id, decision.decision_id, snapshot_hash, decision.snapshot.device_time.isoformat(timespec="microseconds"), payload, now()))
        return {"id": key, "created": True}

    def get_decision(self, key: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM decisions WHERE id=?", (key,)).fetchone()
            if not row:
                raise NotFound("decision not found")
            data = json.loads(row["payload"])
            if digest(data["snapshot"]) != row["snapshot_hash"]:
                raise Conflict("saved snapshot integrity check failed")
            data.update(id=row["id"], snapshot_hash=row["snapshot_hash"], created_at=row["created_at"])
        return data

    def execution_events(self, key: str) -> list[dict]:
        decision = self.get_decision(key)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM execution_events WHERE decision_key=? ORDER BY sequence", (key,)).fetchall()
        events = [dict(json.loads(r["payload"]), sequence=r["sequence"], received_at=r["received_at"]) for r in rows]
        if decision["source_kind"] == "external":
            verified = self._verified_instruction_ids(key)
            for event in events:
                if event["kind"] == "confirmed":
                    event["verification_status"] = "verified" if event["instruction_id"] in verified else "unverified"
        return events

    def _verified_instruction_ids(self, key: str) -> set[str]:
        with self.connect() as db:
            rows = db.execute("SELECT payload,result FROM readback_observations WHERE decision_key=?", (key,)).fetchall()
        return {json.loads(r["payload"])["instruction_id"] for r in rows if json.loads(r["result"])["status"] == "verified"}

    def add_execution(self, key: str, event: ExecutionEvent) -> dict:
        decision = self.get_decision(key)
        if decision["source_kind"] == "external" and event.kind == "confirmed":
            raise ValueError("external confirmation requires the structured submission/readback bridge")
        with self.connect(write=True) as db:
            return self._insert_execution(db, key, event)

    def _insert_execution(self, db, key: str, event: ExecutionEvent) -> dict:
        data = event.model_dump(mode="json")
        existing = db.execute("SELECT payload FROM execution_events WHERE decision_key=? AND event_id=?", (key, event.event_id)).fetchone()
        if existing:
            if json.loads(existing["payload"]) != data:
                raise Conflict("event identity already exists with different content")
            return {"created": False, "event": data}
        submission = db.execute("SELECT payload FROM execution_events WHERE decision_key=? AND instruction_id=? AND kind='submitted' ORDER BY sequence LIMIT 1", (key, event.instruction_id)).fetchone()
        if event.kind == "submitted":
            if submission:
                raise Conflict("instruction already has a submission event")
        elif not submission:
            raise ValueError("terminal event requires a prior matching submission")
        elif json.loads(submission["payload"])["issued_at"] != data["issued_at"]:
            raise ValueError("terminal event issued_at must match the submitted instruction")
        db.execute("INSERT INTO execution_events (decision_key,event_id,instruction_id,kind,issued_at,payload,received_at) VALUES (?,?,?,?,?,?,?)",
            (key, event.event_id, event.instruction_id, event.kind, event.issued_at.isoformat(), canonical(data), now()))
        return {"created": True, "event": data}

    def _check_evidence_identity(self, key: str, evidence) -> dict:
        decision = self.get_decision(key)
        if any(decision[field] != getattr(evidence, field) for field in ("run_id", "site_id", "decision_id")):
            raise ValueError("execution evidence does not match the decision identity")
        return decision

    def add_submission_receipt(self, key: str, receipt) -> dict:
        decision = self._check_evidence_identity(key, receipt)
        action = decision["action"]
        expected = "grid_export_target" if receipt.flow == "discharge" else "battery_charge"
        if action["kind"] != receipt.flow or action["control_semantics"] != expected or abs(action["power_kw"] - receipt.target_kw) > 1e-6:
            raise ValueError("actual command differs from the saved candidate; save a separate decision")
        if receipt.issued_device_time < datetime.fromisoformat(decision["snapshot"]["device_time"].replace("Z", "+00:00")):
            raise ValueError("submission device clock predates the candidate")
        if receipt.issued_wall_time < datetime.fromisoformat(decision["snapshot"]["observed_at"].replace("Z", "+00:00")):
            raise ValueError("submission wall clock predates candidate capture")
        if (receipt.completed_wall_time - datetime.now(timezone.utc)).total_seconds() > 2:
            raise ValueError("submission wall clock is in the future")
        data = receipt.model_dump(mode="json")
        event = ExecutionEvent(event_id=receipt.event_id, instruction_id=receipt.instruction_id,
            kind="submitted", issued_at=receipt.issued_device_time, note="外部提交回执；等待提交后的遥测回读。")
        with self.connect(write=True) as db:
            old = db.execute("SELECT payload FROM submission_receipts WHERE decision_key=? AND instruction_id=?", (key, receipt.instruction_id)).fetchone()
            if old:
                if json.loads(old["payload"]) != data:
                    raise Conflict("submission receipt identity has different content")
                return {"created":False, "receipt":data}
            self._insert_execution(db, key, event)
            db.execute("INSERT INTO submission_receipts VALUES (?,?,?,?)", (key, receipt.instruction_id, canonical(data), now()))
        return {"created":True, "receipt":data}

    def add_readback(self, key: str, telemetry) -> dict:
        from .execution import SubmissionReceipt, verify_readback, build_execution_event
        self._check_evidence_identity(key, telemetry)
        data = telemetry.model_dump(mode="json")
        with self.connect(write=True) as db:
            old = db.execute("SELECT payload,result FROM readback_observations WHERE decision_key=? AND observation_id=?", (key, telemetry.observation_id)).fetchone()
            if old:
                if json.loads(old["payload"]) != data:
                    raise Conflict("readback observation identity has different content")
                return {"created":False,"verification":json.loads(old["result"])}
            row = db.execute("SELECT payload FROM submission_receipts WHERE decision_key=? AND instruction_id=?", (key, telemetry.instruction_id)).fetchone()
            receipt = SubmissionReceipt.model_validate_json(row["payload"]) if row else None
            result = verify_readback(receipt, telemetry, now=datetime.now(timezone.utc))
            if result.status == "verified":
                event = ExecutionEvent.model_validate(build_execution_event(receipt, telemetry, result))
                self._insert_execution(db, key, event)
            saved = result.model_dump(mode="json")
            db.execute("INSERT INTO readback_observations VALUES (?,?,?,?,?)", (key, telemetry.observation_id, canonical(data), canonical(saved), now()))
        return {"created":True,"verification":saved}

    def execution_evidence(self, key: str) -> dict:
        self.get_decision(key)
        with self.connect() as db:
            receipts = db.execute("SELECT payload FROM submission_receipts WHERE decision_key=? ORDER BY received_at", (key,)).fetchall()
            readings = db.execute("SELECT payload,result,received_at FROM readback_observations WHERE decision_key=? ORDER BY received_at,observation_id", (key,)).fetchall()
        return {"submission_receipts":[json.loads(r["payload"]) for r in receipts],
                "readbacks":[{"observation":json.loads(r["payload"]),"verification":json.loads(r["result"]),"received_at":r["received_at"]} for r in readings]}

    def execution_status(self, key: str) -> str:
        events = self.execution_events(key)
        submissions = [e for e in events if e["kind"] == "submitted"]
        if not submissions:
            return "candidate_only"
        latest = max(submissions, key=lambda e: (datetime.fromisoformat(e["issued_at"].replace("Z", "+00:00")), e["sequence"]))
        terminal = [e for e in events if e["instruction_id"] == latest["instruction_id"] and e["kind"] != "submitted"]
        if not terminal:
            return "submitted"
        event = terminal[-1]
        if event["kind"] == "confirmed" and event.get("verification_status") == "unverified":
            return "confirmation_unverified"
        return event["kind"]

    def add_settlement(self, run_id: str, event: SettlementIn) -> dict:
        payload = canonical(event.model_dump(mode="json"))
        with self.connect(write=True) as db:
            run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise NotFound("run not found")
            if run["source_kind"] == "demo" and event.source_kind != "demo":
                raise ValueError("demo runs cannot be labelled as external settlement")
            if run["source_kind"] == "external" and event.source_kind not in {"official", "simulator_mock"}:
                raise ValueError("external runs require an official or simulator-mock settlement source")
            existing = db.execute("SELECT payload FROM settlements WHERE run_id=? AND event_id=?", (run_id, event.event_id)).fetchone()
            if existing:
                if json.loads(existing["payload"]) != event.model_dump(mode="json"):
                    raise Conflict("settlement event already exists with different content")
                return {"created": False}
            db.execute("INSERT INTO settlements (run_id,event_id,payload,received_at) VALUES (?,?,?,?)", (run_id,event.event_id,payload,now()))
        return {"created": True}

    def get_settlement(self, run_id: str) -> dict | None:
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
                raise NotFound("run not found")
            rows = db.execute("SELECT * FROM settlements WHERE run_id=?", (run_id,)).fetchall()
        if not rows:
            return None
        values = [(json.loads(row["payload"]), row["sequence"]) for row in rows]
        # Latest capture time, not arrival order: delayed older events cannot regress state.
        return max(values, key=lambda x: (datetime.fromisoformat(x[0]["captured_at"].replace("Z", "+00:00")), x[1]))[0]

    def decisions(self, run_id: str | None = None, site_id: str | None = None) -> list[dict]:
        clauses, values = [], []
        for column, value in (("run_id",run_id),("site_id",site_id)):
            if value is not None:
                clauses.append(column+"=?"); values.append(value)
        where = " WHERE "+" AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            keys = db.execute("SELECT id FROM decisions"+where+" ORDER BY device_time DESC,id", values).fetchall()
        return [self.get_decision(r["id"]) for r in keys]

    def explanation(self, key: str) -> dict:
        from .facts import explain
        value = explain(self.get_decision(key),self.execution_status(key))
        version = "facts-template-v2"
        payload = canonical(value)
        identity = digest({"decision":key,"template_version":version,"explanation":value})
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO explanations VALUES (?,?,?,?,?)",(identity,key,version,payload,now()))
            saved = db.execute("SELECT created_at FROM explanations WHERE id=?",(identity,)).fetchone()
        return value | {"explanation_id":identity,"template_version":version,"generated_at":saved["created_at"]}

    def runs(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY created_at DESC,run_id")]

    def update_collection_status(self, status: dict) -> dict:
        value = CollectionStatus.model_validate(status)
        wall = datetime.now(timezone.utc)
        if (value.updated_at - wall).total_seconds() > 2:
            raise ValueError("collector heartbeat wall clock is in the future")
        data = value.model_dump(mode="json")
        with self.connect(write=True) as db:
            run = db.execute("SELECT source_kind FROM runs WHERE run_id=?", (value.run_id,)).fetchone()
            if run and run["source_kind"] != "external":
                raise Conflict("collector requires an external run")
            old = db.execute("SELECT payload FROM collection_status WHERE run_id=?", (value.run_id,)).fetchone()
            if old:
                previous = CollectionStatus.model_validate_json(old["payload"])
                if value.updated_at < previous.updated_at or value.started_at < previous.started_at:
                    raise Conflict("delayed collector heartbeat rejected")
                if value.owner_id != previous.owner_id and previous.state != "stopped" and (wall - previous.updated_at).total_seconds() <= previous.heartbeat_expiry_seconds:
                    raise Conflict("another collector owns this run")
                # The heartbeat and checkpoint are separate databases. A crash
                # after the accepted heartbeat must not let an older checkpoint
                # erase the last whole-cycle outcome on the next process start.
                if previous.last_success_at is not None and (value.last_success_at is None or previous.last_success_at > value.last_success_at):
                    data["last_success_at"] = previous.model_dump(mode="json")["last_success_at"]
                if previous.last_error_at is not None and (value.last_error_at is None or previous.last_error_at > value.last_error_at):
                    data["last_error_at"] = previous.model_dump(mode="json")["last_error_at"]
                    data["last_error"] = previous.last_error
            db.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?)", (value.run_id, "external", now()))
            db.execute("INSERT INTO collection_status VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload", (value.run_id, canonical(data)))
        return data

    def collection_status(self, run_id: str | None, wall: datetime | None = None) -> dict | None:
        if not run_id:
            return None
        with self.connect() as db:
            row = db.execute("SELECT payload FROM collection_status WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        value = json.loads(row["payload"])
        wall = wall or datetime.now(timezone.utc)
        age = (wall - datetime.fromisoformat(value["updated_at"].replace("Z", "+00:00"))).total_seconds()
        if age < -2 or (value["state"] != "stopped" and age > value["heartbeat_expiry_seconds"]):
            value.update(state="unknown", reason="clock_unverified" if age < -2 else "heartbeat_expired")
        return value

    def stations(self, run_id: str | None = None) -> dict:
        runs = self.runs()
        run_id = run_id or (runs[0]["run_id"] if runs else None)
        if run_id and not any(r["run_id"] == run_id for r in runs):
            raise NotFound("run not found")
        wall = datetime.now(timezone.utc)
        collection = self.collection_status(run_id, wall)
        with self.connect() as db:
            keys = db.execute("SELECT id,site_id FROM decisions WHERE run_id=? ORDER BY device_time DESC,created_at DESC,id", (run_id,)).fetchall()
            submitted = db.execute("""SELECT d.id,d.site_id,r.payload,r.received_at
                FROM submission_receipts r JOIN decisions d ON d.id=r.decision_key
                WHERE d.run_id=? ORDER BY r.received_at DESC,d.id""", (run_id,)).fetchall()
        # The newest shadow candidate must not hide a prior, actually submitted
        # control decision. This is explicitly historical, never a live setpoint.
        last_submissions = {}
        for row in submitted:
            if row["site_id"] in last_submissions:
                continue
            receipt = json.loads(row["payload"])
            last_submissions[row["site_id"]] = {
                "decision_key":row["id"], "instruction_id":receipt["instruction_id"],
                "submitted_at":receipt["completed_wall_time"], "device_time":receipt["issued_device_time"],
                "action":{"kind":receipt["flow"],"power_kw":receipt["target_kw"],
                          "control_semantics":"battery_charge" if receipt["flow"] == "charge" else "grid_export_target"},
                "execution_status":self.execution_status(row["id"]),
            }
        seen, stations = set(), []
        from .facts import explain
        for row in keys:
            if row["site_id"] in seen:
                continue
            seen.add(row["site_id"])
            d = self.get_decision(row["id"])
            s = d["snapshot"]
            execution_status = self.execution_status(d["id"])
            stations.append({k: d[k] for k in ("id","run_id","site_id","decision_id","source_kind","action","solver_status")}
                | {k: s[k] for k in ("device_time","soc_percent","pv_kw","load_kw","forecast_status")}
                | {"execution_status": execution_status, "reason_summary": explain(d,execution_status)["summary"],
                   "observed_at":s["observed_at"], "created_at":d["created_at"],
                   "record_freshness":record_freshness(s["observed_at"], collection, wall),
                   "sources":s["sources"],"provenance":d.get("provenance"),
                   "last_submission":last_submissions.get(d["site_id"])})
        return {"run_id":run_id,"runs":runs,"stations": sorted(stations,key=lambda x:x["site_id"]),
                "server_time":wall.isoformat(), "collection":collection,
                "settlement":self.get_settlement(run_id) if run_id else None}

    def create_job(self, key: str, intervention: Intervention) -> dict:
        d = self.get_decision(key)
        cache_key = digest({"decision":key,"snapshot":d["snapshot_hash"],"model":d["model_version"],
                            "action":d["action"],"first_charge_kw":intervention.first_charge_kw})
        with self.connect(write=True) as db:
            cached = db.execute("SELECT id FROM jobs WHERE cache_key=? AND status IN ('queued','running','ready','infeasible') ORDER BY created_at DESC LIMIT 1", (cache_key,)).fetchone()
            if cached:
                job_id = cached["id"]
            else:
                job_id = str(uuid4())
                stamp = now()
                db.execute("INSERT INTO jobs (id,decision_key,cache_key,first_charge_kw,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (job_id,key,cache_key,intervention.first_charge_kw,"queued",stamp,stamp))
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
        if not row:
            raise NotFound("comparison task not found")
        return {"id":row["id"],"decision_id":row["decision_key"],"first_charge_kw":row["first_charge_kw"],
                "status":row["status"],"result":json.loads(row["result"]) if row["result"] else None,
                "error":row["error"],"created_at":row["created_at"],"updated_at":row["updated_at"],"attempts":row["attempts"]}

    def claim_job(self, lease_seconds: float = 40) -> dict | None:
        with self.connect(write=True) as db:
            t = time.time()
            db.execute("UPDATE jobs SET status='queued',lease_until=NULL,updated_at=? WHERE status='running' AND lease_until < ? AND attempts < 2", (now(),t))
            db.execute("UPDATE jobs SET status='failed',error='worker lease expired after two attempts',lease_until=NULL,updated_at=? WHERE status='running' AND lease_until < ? AND attempts >= 2", (now(),t))
            # The independent worker has single global concurrency, including multiple CLI launches.
            if db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?",(t+lease_seconds,now(),row["id"]))
            job_id = row["id"]
        return self.get_job(job_id)

    def finish_job(self, job_id: str, status: str, result=None, error: str | None = None, attempt: int | None = None):
        if status not in {"ready","infeasible","timeout","unsupported","failed"}:
            raise ValueError("invalid terminal task status")
        with self.connect(write=True) as db:
            row = db.execute("SELECT status,attempts FROM jobs WHERE id=?",(job_id,)).fetchone()
            if not row:
                raise NotFound("comparison task not found")
            if row["status"] != "running" or (attempt is not None and row["attempts"] != attempt):
                raise Conflict("task lease no longer belongs to this attempt")
            db.execute("UPDATE jobs SET status=?,result=?,error=?,lease_until=NULL,updated_at=? WHERE id=?",
                (status, canonical(result) if result is not None else None, error, now(),job_id))
        return self.get_job(job_id)

    def create_plan_job(self, key: str, target_soc_percent: float, deadline: str) -> dict:
        d = self.get_decision(key)
        cache_key = digest({"kind": "plan_feasibility", "decision": key,
                            "snapshot": d["snapshot_hash"], "model": d["model_version"],
                            "target_soc_percent": target_soc_percent, "deadline": deadline})
        with self.connect(write=True) as db:
            cached = db.execute("SELECT id FROM plan_jobs WHERE cache_key=? AND status IN ('queued','running','ready','infeasible') ORDER BY created_at DESC LIMIT 1", (cache_key,)).fetchone()
            if cached:
                job_id = cached["id"]
            else:
                job_id, stamp = str(uuid4()), now()
                db.execute("INSERT INTO plan_jobs (id,decision_key,cache_key,target_soc_percent,deadline,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (job_id,key,cache_key,target_soc_percent,deadline,"queued",stamp,stamp))
        return self.get_plan_job(job_id)

    def get_plan_job(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM plan_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise NotFound("plan task not found")
        return {"id": row["id"], "decision_id": row["decision_key"],
                "target_soc_percent": row["target_soc_percent"], "deadline": row["deadline"],
                "status": row["status"], "result": json.loads(row["result"]) if row["result"] else None,
                "error": row["error"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "attempts": row["attempts"]}

    def claim_plan_job(self, lease_seconds: float = 40) -> dict | None:
        with self.connect(write=True) as db:
            t = time.time()
            db.execute("UPDATE plan_jobs SET status='queued',lease_until=NULL,updated_at=? WHERE status='running' AND lease_until < ? AND attempts < 2", (now(),t))
            db.execute("UPDATE plan_jobs SET status='failed',error='worker lease expired after two attempts',lease_until=NULL,updated_at=? WHERE status='running' AND lease_until < ? AND attempts >= 2", (now(),t))
            if db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone() or db.execute("SELECT 1 FROM plan_jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT id FROM plan_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE plan_jobs SET status='running',lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?", (t+lease_seconds,now(),row["id"]))
        return self.get_plan_job(row["id"])

    def finish_plan_job(self, job_id: str, status: str, result=None, error: str | None = None,
                        attempt: int | None = None) -> dict:
        if status not in {"ready","infeasible","timeout","unsupported","failed"}:
            raise ValueError("invalid terminal plan status")
        with self.connect(write=True) as db:
            row = db.execute("SELECT status,attempts FROM plan_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise NotFound("plan task not found")
            if row["status"] != "running" or (attempt is not None and row["attempts"] != attempt):
                raise Conflict("plan lease no longer belongs to this attempt")
            db.execute("UPDATE plan_jobs SET status=?,result=?,error=?,lease_until=NULL,updated_at=? WHERE id=?",
                (status, canonical(result) if result is not None else None,error,now(),job_id))
        return self.get_plan_job(job_id)

    def add_pv_daily(self, report: PVAccountingIn) -> dict:
        data = report.model_dump(mode="json")
        totals = data["totals"]
        required = {"pv_generated_kwh", "direct_self_use_kwh", "pv_to_battery_kwh",
                    "pv_to_grid_kwh", "grid_export_kwh", "negative_export_loss_eur"}
        if set(totals) != required or any(not math.isfinite(x) or x < -1e-9 for x in totals.values()):
            raise ValueError("invalid PV accounting totals")
        if abs(totals["pv_generated_kwh"] - sum(totals[k] for k in
            ("direct_self_use_kwh", "pv_to_battery_kwh", "pv_to_grid_kwh"))) > 1e-6:
            raise ValueError("PV allocation does not conserve energy")
        if totals["pv_to_grid_kwh"] > totals["grid_export_kwh"] + 1e-6:
            raise ValueError("PV export exceeds total grid export")
        if set(data["bottlenecks"]) != {"battery_full_steps", "charge_limit_steps", "negative_price_export_steps"}:
            raise ValueError("invalid PV bottleneck fields")
        if any(v is not None and (v < 0 or v > data["steps"]) for v in data["bottlenecks"].values()):
            raise ValueError("invalid PV bottleneck count")
        payload, checksum = canonical(data), digest(data)
        with self.connect(write=True) as db:
            old = db.execute("SELECT payload_hash FROM pv_daily WHERE site_id=? AND device_date=? AND arm=?",
                             (data["site_id"],data["device_date"],data["arm"])).fetchone()
            if old:
                if old["payload_hash"] != checksum:
                    raise Conflict("PV daily identity already exists with different content")
                return {"created": False, "report": data}
            db.execute("INSERT INTO pv_daily VALUES (?,?,?,?,?,?)",
                (data["site_id"],data["device_date"],data["arm"],payload,checksum,now()))
        return {"created": True, "report": data}

    def pv_daily(self, site_id: str, arm: str = "zero_flow") -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT payload,payload_hash FROM pv_daily WHERE site_id=? AND arm=? ORDER BY device_date",
                              (site_id,arm)).fetchall()
        reports = []
        for row in rows:
            data = json.loads(row["payload"])
            if digest(data) != row["payload_hash"]:
                raise Conflict("saved PV accounting integrity check failed")
            reports.append(data)
        return reports
