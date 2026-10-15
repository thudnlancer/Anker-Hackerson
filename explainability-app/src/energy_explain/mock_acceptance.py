"""Observe one explicitly selected mock; this module never starts or controls it."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from energy_lab.config import is_placeholder, load_runtime_config
from energy_lab.explainability_stream import ReadOnlyStream, StreamCheckpoint
from energy_lab.shadow import evaluate_snapshot, daily_points, _infer_api_timezone
from .live_source import LiveSource, SourceUnavailable
from .schemas import DecisionIn, Intervention, SettlementIn
from .store import Store


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('explicit UTC offset required')
    return parsed.astimezone(timezone.utc)


def same_window(contest, selected):
    return contest.get('track') == 'mock' and all(
        contest.get(key) == selected.get(key) for key in
        ('track', 'start_at', 'end_at', 'date_range', 'days', 'speed'))


class SnapshotAlignmentPending(ValueError):
    """A newly active mock is still receiving cached HA ticks from a prior run."""


def require_window_alignment(snapshot, selected):
    import pandas as pd
    start_date,end_date = selected['date_range'].split(' ~ ')
    lower = pd.Timestamp(start_date, tz='UTC')
    upper = pd.Timestamp(end_date, tz='UTC') + pd.Timedelta(days=1)
    coordinate = _infer_api_timezone(daily_points(snapshot['prices']), {})
    for station in snapshot['stations'].values():
        tick = pd.Timestamp(station['state']['device_time'])
        if tick.tzinfo is None:
            if coordinate is None:
                raise SnapshotAlignmentPending('device coordinate unavailable')
            tick = tick.tz_localize(coordinate)
        if not lower <= tick.tz_convert('UTC') < upper:
            # Keep the captured business input for diagnosis, but do not let a
            # prior mock's clock enter this run's monotonic checkpoint.
            raise SnapshotAlignmentPending('HA tick outside selected mock data window')


def shadow_evaluate(snapshot, *, evidence_sink, **kwargs):
    def emit(record):
        saved = deepcopy(record)
        # Separate immutable shadow evidence from an actual bounded submission
        # at the same tick. Both remain under the one simulator run identity.
        saved['decision_id'] = 'shadow-' + saved['decision_id']
        evidence_sink(saved)
    return evaluate_snapshot(snapshot, evidence_sink=emit, **kwargs)


def result_fingerprint(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode()).hexdigest()


def match_final(dashboard, selected, previous_count, previous_fingerprints=()):
    """Bind a unique new result to this observed window, never live KPIs/row 0."""
    contest = dashboard.get('contest', {})
    if not same_window(contest, selected) or contest.get('mode') != 'ended':
        return None
    rows = dashboard.get('results', {}).get('runs', [])
    if len(rows) <= previous_count:
        return None
    matches = []
    for row in rows:
        try:
            if result_fingerprint(row) in previous_fingerprints:
                continue
        except (ValueError, TypeError):
            continue
        if any(row.get(k) != selected[k] for k in ('track', 'date_range', 'days', 'speed')):
            continue
        try:
            delay = (stamp(row['ended_at']) - stamp(selected['end_at'])).total_seconds()
        except (KeyError, TypeError, ValueError):
            continue
        if not -2 <= delay <= 60:
            continue
        # Validate identity fields if the upstream adds them in future.
        if any(k in row and row[k] != selected[k] for k in ('start_at', 'end_at')):
            continue
        if 'contestID' in selected and row.get('contestID') != selected['contestID']:
            continue
        sites = row.get('sites', [])
        if len(sites) != 3 or {str(x.get('site_id')) for x in sites} != {'1', '2', '3'}:
            continue
        total = row.get('total', {})
        required = ('baseline_cost', 'saving', 'saving_settled', 'device_revenue', 'baseline_revenue')
        if not all(isinstance(total.get(k), (int, float)) and not isinstance(total[k], bool)
                   and math.isfinite(total[k]) for k in required):
            continue
        good = True
        for site in sites:
            metrics = site.get('metrics', {})
            numeric = required + ('baseline_settled', 'device_settled', 'saving_settled_percent',
                'device_grid_import_cost', 'device_grid_export_revenue', 'device_net_grid_cost')
            if not all(isinstance(metrics.get(k), (int, float)) and not isinstance(metrics[k], bool)
                       and math.isfinite(metrics[k]) for k in numeric):
                good = False; break
            if not all(math.isclose(left, right, abs_tol=1e-7) for left, right in (
                (metrics['device_revenue'] - metrics['baseline_revenue'], metrics['saving']),
                (metrics['device_settled'] - metrics['baseline_settled'], metrics['saving_settled']),
                (metrics['device_grid_import_cost'] - metrics['device_grid_export_revenue'], metrics['device_net_grid_cost']))):
                good = False; break
        if good and all(math.isclose(sum(site['metrics'][k] for site in sites), total[k], abs_tol=1e-7)
                        for k in required):
            matches.append(row)
    return deepcopy(matches[0]) if len(matches) == 1 else None


def settlement_record(run_id, selected, *, final=None, captured_at=None, verification=None):
    captured_at = captured_at or datetime.now(timezone.utc).isoformat()
    final_hash = hashlib.sha256(json.dumps(final, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]
    return SettlementIn.model_validate(dict(
        event_id=('mock-final-' + final_hash) if final else 'mock-pending',
        status='complete' if final else 'pending', source_kind='simulator_mock',
        captured_at=captured_at, window_start=selected['start_at'], window_end=selected['end_at'],
        saving_settled_eur=final['total']['saving_settled'] if final else None,
        saving_settled_percent=final['total'].get('saving_settled_percent') if final else None,
        raw_fields=dict(track='mock', execution_scope='bounded_control_then_shadow',
            run_id=run_id, date_range=selected['date_range'],
            sites=final['sites'] if final else [], total=final['total'] if final else None,
            ended_at=final['ended_at'] if final else None,
            binding_basis='observed same window plus unique new result and ended_at',
            server_verification=verification,
            independent_signature_verification=False,
            scope_note='Limited HA control followed by self-consumption and shadow collection; not a full-run MILP performance test.')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--solver-seconds', type=float, default=2)
    args = parser.parse_args(argv)
    if not math.isfinite(args.solver_seconds) or not 0 < args.solver_seconds <= 5:
        parser.error('shadow solver budget must be finite and between 0 and 5 seconds')
    manifest = json.loads(args.manifest.read_text())
    selected, run_id = manifest['initial']['contest'], manifest['run_id']
    if selected.get('track') != 'mock' or stamp(selected['end_at']) <= stamp(selected['start_at']):
        parser.error('a specific increasing mock window is required')
    baseline = manifest['initial'].get('result_fingerprints')
    if (not isinstance(baseline,list) or len(baseline) != manifest['initial']['results_count']
            or any(not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value) for value in baseline)):
        parser.error('exact pre-start result fingerprints are required')
    cfg = load_runtime_config(args.config)
    for key in ('ha_token', 'ha_username', 'ha_password', 'sim_username', 'sim_password'):
        supplied = os.environ.get('ANKER_' + key.upper())
        if supplied and is_placeholder(cfg.get(key)):
            cfg[key] = supplied
    cfg.update(live_enabled=False, allow_ha_write=False, allow_formal_contest=False)
    source = LiveSource(cfg, max_auth_refreshes=4)
    store, checkpoint = Store(args.db), StreamCheckpoint(args.checkpoint)
    owner = uuid.uuid4().hex
    start_wall = datetime.now(timezone.utc).isoformat()
    args.output.mkdir(parents=True, exist_ok=True)
    binding = dict(run_id=run_id, contest={key:selected[key] for key in
        ('track','start_at','end_at','date_range','days','speed')},
        db=str(args.db.resolve()), checkpoint=str(args.checkpoint.resolve()))
    binding_path = args.output / 'observer-binding.json'
    if binding_path.exists():
        if json.loads(binding_path.read_text()) != binding:
            parser.error('observer run/window/database binding cannot change on restart')
    else:
        binding_path.write_text(json.dumps(binding, sort_keys=True, indent=2)+'\n')
    report = dict(run_id=run_id, phase='waiting', pid=os.getpid(), started_at=start_wall,
                  rounds=0, ha_writes_performed=0, execution_scope='shadow_only', samples=[],
                  solver_budget_seconds=args.solver_seconds)
    if (args.output / 'progress.json').exists():
        old = json.loads((args.output / 'progress.json').read_text())
        if old.get('run_id') != run_id:
            parser.error('output belongs to a different run')
        report.update(rounds=old.get('rounds', 0), samples=old.get('samples', []))

    def save(name, value):
        target = args.output / name
        tmp = target.with_suffix(target.suffix + '.tmp')
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        tmp.replace(target)

    def progress():
        report.update(updated_at=datetime.now(timezone.utc).isoformat(), auth_refreshes=dict(source.refreshes))
        save('progress.json', report)

    def status(state, reason):
        if stream is not None:
            return stream._status(state, reason=reason, record_cycle=False)
        return store.update_collection_status(dict(run_id=run_id, owner_id=owner, state=state, reason=reason,
            started_at=start_wall, updated_at=datetime.now(timezone.utc).isoformat(), last_success_at=None,
            poll_interval_seconds=5, freshness_threshold_seconds=15, heartbeat_expiry_seconds=45))

    failure = None
    capture_generation = 0
    def capture(_cfg):
        nonlocal failure, capture_generation
        try:
            snapshot, states = source.capture()
            save('latest-snapshot.json', snapshot)
            capture_generation += 1
            require_window_alignment(snapshot, selected)
            return snapshot
        except SourceUnavailable as exc:
            if exc.category.endswith('_authentication_failed'):
                failure = dict(category=exc.category, http_status=exc.http_status)
            raise

    stream = None
    deadline = stamp(selected['end_at']) + timedelta(minutes=5)
    consecutive_reads = 0
    try:
        existing = store.get_settlement(run_id) if any(r['run_id']==run_id for r in store.runs()) else None
        if existing and any(existing[key] != stamp(selected[source_key]).isoformat().replace('+00:00','Z')
                            for key,source_key in (('window_start','start_at'),('window_end','end_at'))):
            raise ValueError('existing settlement belongs to another mock window')
        status('running', 'waiting_start')
        if store.get_settlement(run_id) is None:
            store.add_settlement(run_id, settlement_record(run_id, selected))
        progress()
        while datetime.now(timezone.utc) < deadline:
            cycle_start = time.monotonic()
            try:
                dashboard = source.dashboard()
            except SourceUnavailable as exc:
                if exc.category.endswith('_authentication_failed'):
                    report['error'] = dict(category=exc.category, http_status=exc.http_status)
                    report['phase'] = 'authentication_failed'; break
                consecutive_reads += 1
                report['last_dashboard_error'] = exc.category
                if consecutive_reads >= 10:
                    report['phase'] = 'source_failed'; break
                status('degraded', 'dashboard_unavailable'); progress(); time.sleep(5); continue
            consecutive_reads = 0
            contest = dashboard['contest']
            save('dashboard.json', dict(captured_at=datetime.now(timezone.utc).isoformat(),
                contest=contest, results=dashboard.get('results', {})))
            report['contest'] = contest
            if not same_window(contest, selected):
                report['phase'] = 'contest_changed'; break
            if contest['mode'] == 'waiting':
                status('running', 'waiting_start'); progress(); time.sleep(5); continue
            if contest['mode'] == 'running':
                if stream is None:
                    stream = ReadOnlyStream(cfg, run_id=run_id, checkpoint=checkpoint, owner_id=owner,
                        capture=capture, evaluate=shadow_evaluate,
                        record_sink=lambda record:store.add_decision(DecisionIn.model_validate(record)),
                        status_sink=store.update_collection_status, heartbeat_expiry_seconds=45,
                        solver_seconds=args.solver_seconds)
                report['phase'] = 'running'
                before_capture = capture_generation
                result = stream.collect_once(); report['rounds'] += 1
                report['last_collection'] = result
                if result.get('state') != 'running':
                    failed = args.output/'failed-cycles'
                    failed.mkdir(exist_ok=True)
                    # A source failure can leave last round's latest snapshot
                    # on disk. Never label that old input as this failed cycle.
                    if capture_generation > before_capture:
                        (failed/f"cycle-{result['cycle']}-snapshot.json").write_bytes((args.output/'latest-snapshot.json').read_bytes())
                    (failed/f"cycle-{result['cycle']}-status.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
                if failure or stream.terminal:
                    report.update(phase='source_failed', error=failure or {'category':result.get('reason')}); break
                arm = 'closing' if datetime.now(timezone.utc) >= stamp(selected['end_at']) - timedelta(minutes=2) else 'opening'
                done = {x['site_id'] for x in report['samples'] if x['arm'] == arm}
                for station in store.stations(run_id)['stations']:
                    if station['site_id'] in done:
                        continue
                    key = station['id']; why = store.explanation(key)
                    job = store.create_job(key, Intervention(first_charge_kw=0))
                    report['samples'].append(dict(arm=arm, site_id=station['site_id'], decision_key=key,
                        job_id=job['id'], explanation=why))
                progress(); time.sleep(max(0, 5 - (time.monotonic() - cycle_start))); continue
            if contest['mode'] == 'ended':
                status('stopped', 'mock_window_finished')
                final = match_final(dashboard, selected, manifest['initial']['results_count'],
                    manifest['initial'].get('result_fingerprints', ()))
                if final:
                    saved = dict(captured_at=datetime.now(timezone.utc).isoformat(), contest=contest,
                        matched_record=final, verification=dashboard['results'].get('verify'),
                        signature_scope='server declaration; no independent validation')
                    save('final-results.json', saved)
                    event = settlement_record(run_id, selected, final=final,
                        captured_at=saved['captured_at'], verification=saved['verification'])
                    previous = store.get_settlement(run_id)
                    if not previous or previous['event_id'] != event.event_id:
                        store.add_settlement(run_id, event)
                    elif previous['raw_fields'] != event.raw_fields:
                        raise ValueError('saved mock settlement differs from the matching final')
                    report['phase'] = 'completed'; break
                report['phase'] = 'awaiting_result'; progress(); time.sleep(5); continue
            report['phase'] = 'unsupported_lifecycle'; break
        else:
            report['phase'] = 'no_final_result'
    except KeyboardInterrupt:
        report['phase'] = 'interrupted'
    except Exception as exc:
        report.update(phase='failed', error={'category':type(exc).__name__})
    finally:
        try: status('stopped', report['phase'])
        except Exception: pass
        source.close()
        records = store.decisions(run_id)
        report.update(finished_at=datetime.now(timezone.utc).isoformat(), decisions=len(records),
            site_counts={site:sum(d['site_id']==site for d in records) for site in cfg['stations']},
            pending=len(checkpoint.pending(run_id)),
            settlement=store.get_settlement(run_id),
            jobs=[store.get_job(x['job_id']) for x in report['samples']])
        save('summary.json', report); progress()
        print(json.dumps({k:report[k] for k in ('run_id','phase','rounds','decisions','site_counts')},ensure_ascii=False),flush=True)
    return 0 if report['phase'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
