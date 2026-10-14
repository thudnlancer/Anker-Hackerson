"""MILP candidate -> durable send intent -> HA receipt -> telemetry evidence.

Network/clock functions are injected. A claimed intent is never sent a second
time, including after a crash with an unknown HTTP outcome. This controller is
separate from ReadOnlyStream: its write status must not be presented as read-only.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from energy_lab.ha_dispatch import DispatchCommand, dispatch_command
from energy_lab.shadow import evaluate_snapshot
from .execution import SubmissionReceipt
from .schemas import DecisionIn
from .store import canonical


def aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('an explicit timezone is required')
    return value.astimezone(timezone.utc)


class ControlJournal:
    def __init__(self, path, *, resource_key, lock_directory=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        resource = urlsplit(resource_key)
        if resource.hostname:
            resource_key = resource.hostname.lower() + ':' + str(resource.port or (443 if resource.scheme == 'https' else 80))
        locks = Path(lock_directory) if lock_directory else Path(__file__).resolve().parents[2] / 'data/control-locks'
        locks.mkdir(parents=True, exist_ok=True)
        fingerprint = hashlib.sha256(resource_key.encode()).hexdigest()[:24]
        self.lock_path = locks / (fingerprint + '.lock')
        self.registry_path = locks / (fingerprint + '.json')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS control_runs (run_id TEXT PRIMARY KEY, binding TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS control_attempts (
                run_id TEXT NOT NULL, site_id TEXT NOT NULL, decision_id TEXT NOT NULL,
                decision_key TEXT NOT NULL, instruction_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL, command TEXT NOT NULL, outcome TEXT,
                updated_at TEXT NOT NULL, PRIMARY KEY (run_id,site_id,decision_id))''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def exclusive(self):
        with self.lock_path.open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('another controller owns these HA stations') from None
            try:
                if self.registry_path.exists():
                    previous_path = Path(json.loads(self.registry_path.read_text())['journal'])
                    if previous_path.resolve() != self.path.resolve():
                        if not previous_path.exists():
                            raise RuntimeError('the previous control journal must be inspected before handoff')
                        previous = sqlite3.connect(previous_path.resolve().as_uri() + '?mode=ro', uri=True)
                        try:
                            unresolved = previous.execute("SELECT count(*) FROM control_attempts WHERE state IN ('claimed','accepted','awaiting_readback','unknown','partial_failure')").fetchone()[0]
                            prior_attempts = previous.execute('SELECT count(*) FROM control_attempts').fetchone()[0]
                            if previous.execute("SELECT 1 FROM sqlite_master WHERE name='control_preparations'").fetchone():
                                prior_attempts += previous.execute('SELECT count(*) FROM control_preparations').fetchone()[0]
                        finally:
                            previous.close()
                        if unresolved:
                            raise RuntimeError('a previous journal contains unresolved HA effects')
                        if prior_attempts:
                            raise RuntimeError('reuse the original control journal to preserve command deduplication')
                self.registry_path.write_text(json.dumps({'journal':str(self.path.resolve())}))
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def bind(self, run_id, binding):
        payload = canonical(binding)
        with self.connect() as db:
            old = db.execute('SELECT binding FROM control_runs WHERE run_id=?', (run_id,)).fetchone()
            if old and old[0] != payload:
                raise ValueError('control run cannot change its station/contest binding')
            db.execute('INSERT OR IGNORE INTO control_runs VALUES (?,?)', (run_id, payload))

    def claim(self, decision, command):
        identity = (command.run_id, command.site_id, command.decision_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM control_attempts WHERE run_id=? AND site_id=? AND decision_id=?', identity).fetchone():
                return False
            # Unknown or pending effects block a new command, even in a new run.
            if db.execute("SELECT 1 FROM control_attempts WHERE site_id=? AND state IN ('claimed','accepted','awaiting_readback','unknown','partial_failure')", (command.site_id,)).fetchone():
                return False
            payload = dict(run_id=command.run_id, site_id=command.site_id, decision_id=command.decision_id,
                           instruction_id=command.instruction_id, suffix=command.suffix,
                           issued_device_time=command.issued_device_time.isoformat(), flow=command.flow,
                           target_w=command.target_w, semantics=command.semantics)
            db.execute('INSERT INTO control_attempts VALUES (?,?,?,?,?,?,?,?,?)',
                       (*identity, decision['id'], command.instruction_id, 'claimed', canonical(payload), None,
                        datetime.now(timezone.utc).isoformat()))
            return True

    def outcome(self, command, state, payload):
        with self.connect() as db:
            updated = db.execute('UPDATE control_attempts SET state=?,outcome=?,updated_at=? WHERE instruction_id=?',
                                (state, canonical(payload), datetime.now(timezone.utc).isoformat(), command.instruction_id))
            if updated.rowcount != 1:
                raise ValueError('missing durable command intent')

    def attempts(self, run_id=None):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM control_attempts' + (' WHERE run_id=?' if run_id else '') + ' ORDER BY updated_at',
                              (run_id,) if run_id else ()).fetchall()
        return [dict(row) for row in rows]


def bind_contest(contest, *, scope):
    if scope == 'mock':
        if contest.get('track') != 'mock' or contest.get('mode') != 'running':
            raise ValueError('mock control requires an active mock contest')
        fields = ('track', 'start_at', 'end_at', 'date_range')
        if any(not contest.get(key) for key in fields):
            raise ValueError('mock identity is incomplete')
        return {key: contest[key] for key in fields}
    if scope == 'free-run':
        if contest.get('mode') != 'idle':
            raise ValueError('free-run control requires simulator mode=idle')
        return {'mode': 'idle'}
    raise ValueError('formal contest control is not supported by this runner')


def contest_matches(contest, binding, *, scope):
    try:
        return bind_contest(contest, scope=scope) == binding
    except ValueError:
        return False


def validate_target_capability(action, attributes):
    """Reject precision loss instead of silently altering a stored MILP plan."""
    if attributes.get('unit_of_measurement') != 'W':
        raise ValueError('control power unit is not W')
    values = [float(attributes[key]) for key in ('min', 'max', 'step')]
    lower, upper, step = values
    if not all(math.isfinite(v) for v in values) or lower < 0 or upper > 5000 or step <= 0:
        raise ValueError('unsupported HA number capability')
    target = action['power_kw'] * 1000
    if not lower <= target <= upper or abs((target - lower) / step - round((target - lower) / step)) * step > .001:
        raise ValueError('candidate cannot be represented at the HA power precision')


class MILPController:
    def __init__(self, *, run_id, store, journal, stations, clock=None, solver_seconds=1, device_timezone=None):
        self.run_id, self.store, self.journal = run_id, store, journal
        self.stations = stations
        if len(stations) != 3 or len({s['suffix'] for s in stations.values()}) != 3:
            raise ValueError('three distinct stations are required')
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.solver_seconds, self.device_timezone = solver_seconds, device_timezone

    def plan(self, snapshot):
        records = []
        result = evaluate_snapshot(snapshot, now=self.clock(), solver_seconds=self.solver_seconds,
                                   device_timezone=self.device_timezone, idle_candidate='zero_charge_unverified',
                                   evidence_sink=records.append, evidence_identity={'run_id': self.run_id, 'input_kind': 'mixed'})
        saved = {}
        for record in records:
            # This metadata is set before the first immutable import. The input
            # and chosen power remain exactly those of this same solve.
            record['policy_version'] = 'milp-ha-control-sop-v1'
            record['provenance']['note'] = ('同次共享MILP求解；零电池功率采用显式charge=0控制假设，需遥测校验。'
                                            '本记录生成时仅为候选；实际提交与回读作为独立追加证据。')
            key = self.store.add_decision(DecisionIn.model_validate(record))['id']
            saved[record['site_id']] = self.store.get_decision(key)
        return dict(stations=result['stations'], decisions=saved, mode='milp_control_candidate', writes_performed=0)

    def submit(self, decision, *, client, capabilities, guard, live=False, send=dispatch_command):
        if type(live) is not bool:
            raise ValueError('live must be an explicit boolean')
        if not live:
            return {'status': 'not_sent', 'decision_key': decision['id'], 'writes_performed': 0}
        site = decision['site_id']
        if decision['run_id'] != self.run_id or site not in self.stations:
            raise ValueError('candidate identity does not belong to this controller')
        if decision != self.store.get_decision(decision['id']) or decision['source_kind'] != 'external':
            raise ValueError('only the unchanged saved external candidate can be submitted')
        if not callable(guard):
            raise ValueError('a contest and freshness guard is required')
        action = decision['action']
        if action['kind'] not in ('charge', 'discharge'):
            raise ValueError('hold cannot be silently converted to self consumption')
        validate_target_capability(action, capabilities)
        snapshot = decision['snapshot']
        if not 0 <= (aware(self.clock()) - aware(snapshot['observed_at'])).total_seconds() <= 15:
            raise ValueError('candidate snapshot is stale')
        token = hashlib.sha256(canonical([self.run_id, site, decision['decision_id']]).encode()).hexdigest()[:40]
        command = DispatchCommand(run_id=self.run_id, site_id=site, decision_id=decision['decision_id'],
                                  instruction_id='milp-' + token, suffix=self.stations[site]['suffix'],
                                  issued_device_time=aware(snapshot['device_time']), flow=action['kind'],
                                  target_w=action['power_kw'] * 1000,
                                  semantics='battery_charge_target' if action['kind'] == 'charge' else 'grid_export_target')
        if not self.journal.claim(decision, command):
            return {'status': 'not_sent_duplicate_or_pending', 'decision_key': decision['id'], 'writes_performed': 0}
        # If this call crashes, the durable claimed intent remains and blocks a
        # blind resend. Reconstructing it requires explicit recovery inspection.
        receipt = send(client, command, live=True, before_step=guard)
        state = 'unknown' if receipt.uncertain_side_effects else receipt.status
        self.journal.outcome(command, state, receipt.to_dict())
        result = {'status': receipt.status, 'decision_key': decision['id'],
                  'instruction_id': command.instruction_id, 'writes_performed': receipt.services_attempted,
                  'receipt': receipt.to_dict()}
        if receipt.status == 'accepted':
            evidence = SubmissionReceipt.model_validate(receipt.as_submission_receipt())
            # Journal keeps the actual receipt if this local append fails.
            self.store.add_submission_receipt(decision['id'], evidence)
            self.journal.outcome(command, 'awaiting_readback', receipt.to_dict())
            result['submission'] = evidence.model_dump(mode='json')
        return result

    def observe(self, decision_key, telemetry):
        decision = self.store.get_decision(decision_key)
        if decision['run_id'] != self.run_id:
            raise ValueError('readback cannot cross controller runs')
        result = self.store.add_readback(decision_key, telemetry)
        if result['verification']['status'] == 'verified':
            with self.journal.connect() as db:
                row = db.execute('SELECT command FROM control_attempts WHERE instruction_id=?', (telemetry.instruction_id,)).fetchone()
            if row:
                command = json.loads(row[0])
                command['issued_device_time'] = aware(command['issued_device_time'])
                self.journal.outcome(DispatchCommand(**command), 'verified', result)
        return result
