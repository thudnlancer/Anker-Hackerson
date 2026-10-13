"""Append-only decision records and SQLite durable queue. No device connections."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

from .schemas import DecisionIn, ExecutionEvent, Intervention, SettlementIn


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
        self.get_decision(key)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM execution_events WHERE decision_key=? ORDER BY sequence", (key,)).fetchall()
        return [dict(json.loads(r["payload"]), sequence=r["sequence"], received_at=r["received_at"]) for r in rows]

    def add_execution(self, key: str, event: ExecutionEvent) -> dict:
        self.get_decision(key)
        payload = canonical(event.model_dump(mode="json"))
        with self.connect(write=True) as db:
            existing = db.execute("SELECT payload FROM execution_events WHERE decision_key=? AND event_id=?", (key, event.event_id)).fetchone()
            if existing:
                if json.loads(existing["payload"]) != event.model_dump(mode="json"):
                    raise Conflict("event identity already exists with different content")
                return {"created": False, "event": event.model_dump(mode="json")}
            submission = db.execute("SELECT * FROM execution_events WHERE decision_key=? AND instruction_id=? AND kind='submitted' ORDER BY sequence LIMIT 1", (key, event.instruction_id)).fetchone()
            if event.kind == "submitted":
                if submission:
                    raise Conflict("instruction already has a submission event")
            else:
                if not submission:
                    raise ValueError("terminal event requires a prior matching submission")
                saved = json.loads(submission["payload"])
                if saved["issued_at"] != event.model_dump(mode="json")["issued_at"]:
                    raise ValueError("terminal event issued_at must match the submitted instruction")
            db.execute("INSERT INTO execution_events (decision_key,event_id,instruction_id,kind,issued_at,payload,received_at) VALUES (?,?,?,?,?,?,?)",
                (key, event.event_id, event.instruction_id, event.kind, event.issued_at.isoformat(), payload, now()))
        return {"created": True, "event": event.model_dump(mode="json")}

    def execution_status(self, key: str) -> str:
        events = self.execution_events(key)
        submissions = [e for e in events if e["kind"] == "submitted"]
        if not submissions:
            return "candidate_only"
        latest = max(submissions, key=lambda e: (datetime.fromisoformat(e["issued_at"].replace("Z", "+00:00")), e["sequence"]))
        terminal = [e for e in events if e["instruction_id"] == latest["instruction_id"] and e["kind"] != "submitted"]
        return terminal[-1]["kind"] if terminal else "submitted"

    def add_settlement(self, run_id: str, event: SettlementIn) -> dict:
        payload = canonical(event.model_dump(mode="json"))
        with self.connect(write=True) as db:
            run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise NotFound("run not found")
            if run["source_kind"] == "demo" and event.source_kind == "official":
                raise ValueError("demo runs cannot be labelled as official settlement")
            if run["source_kind"] == "external" and event.source_kind != "official":
                raise ValueError("external runs require official-source settlement records")
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

    def stations(self, run_id: str | None = None) -> dict:
        runs = self.runs()
        run_id = run_id or (runs[0]["run_id"] if runs else None)
        if run_id and not any(r["run_id"] == run_id for r in runs):
            raise NotFound("run not found")
        with self.connect() as db:
            keys = db.execute("SELECT id,site_id FROM decisions WHERE run_id=? ORDER BY device_time DESC,created_at DESC,id", (run_id,)).fetchall()
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
                   "sources":s["sources"],"provenance":d.get("provenance")})
        return {"run_id":run_id,"runs":runs,"stations": sorted(stations,key=lambda x:x["site_id"]),
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
