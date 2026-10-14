"""Bounded MILP/HA bridge. Defaults to candidate-only; never starts a contest."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'explainability-app/src')]

from energy_lab.config import is_placeholder, load_runtime_config
from energy_lab.shadow import _infer_api_timezone, daily_points
from energy_lab.interface_contract import state_is_fresh
from energy_explain.execution import SubmissionReceipt
from energy_explain.ha_observation import ObservationUnavailable, build_telemetry_readback, _device_tick
from energy_explain.live_control import ControlJournal, MILPController, aware, bind_contest, contest_matches
from energy_explain.live_source import LiveSource, SourceUnavailable
from energy_explain.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('local_online_config.json'))
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--live', action='store_true', help='explicitly permit bounded HA submission')
    parser.add_argument('--scope', choices=('mock', 'free-run'), default='mock')
    parser.add_argument('--expected-contest-start', help='bind the explicitly selected mock window')
    parser.add_argument('--readback-seconds', type=float, default=12)
    parser.add_argument('--solver-seconds', type=float, default=1)
    parser.add_argument('--device-timezone', help='explicit device clock coordinate if absent from price API')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.readback_seconds <= 60 or not 0 < args.solver_seconds <= 5:
        parser.error('bounded readback must be 0..60 seconds and solver 0..5 seconds')
    if (args.output / 'summary.json').exists():
        parser.error('use a new output directory; existing reports are not overwritten')
    cfg = load_runtime_config(args.config)
    # Optional process environment keeps ephemeral SOP credentials out of files.
    for key in ('ha_token','ha_username','ha_password','sim_username','sim_password'):
        supplied = os.environ.get('ANKER_' + key.upper())
        if supplied and is_placeholder(cfg.get(key)):
            cfg[key] = supplied
    if args.live and not (cfg['live_enabled'] and cfg['allow_ha_write']):
        parser.error('--live requires live_enabled and allow_ha_write in the private config')
    if args.live and cfg['allow_formal_contest']:
        parser.error('this bounded runner requires allow_formal_contest=false')
    store = Store(args.db)
    journal = ControlJournal(args.journal, resource_key=cfg['ha_base'])
    source = LiveSource(cfg)
    args.output.mkdir(parents=True, exist_ok=True)
    report = dict(run_id=args.run_id, mode='bounded_live' if args.live else 'candidate_only',
                  scope=args.scope, started_at=datetime.now(timezone.utc).isoformat(), sites={},
                  writes_performed=0, cleanup={}, outcome='running')
    touched = {}

    def save():
        (args.output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))

    def restore(site):
        item = touched[site]
        if item.get('restoration_attempted'):
            return report['cleanup'].get(site,{}).get('restored',False)
        try:
            states, captured = source.states()
            current = next((x.get('state') for x in states if x['entity_id']==item['mode_id']),None)
            already_restored = current == 'self_consumption'
            if not already_restored:
                current_contest = source.dashboard()['contest']
                if args.scope == 'mock' and any(current_contest.get(k)!=v for k,v in binding.items()):
                    raise ValueError('contest changed before restoration; new controller may own the device')
                item['client'].token = source.tokens['ha']
                report['writes_performed'] += 1
                item['restoration_attempted'] = True
                item['client'].set_self_consumption(cfg['stations'][site]['suffix'], live=True)
                states, captured = source.states()
                current = next((x.get('state') for x in states if x['entity_id']==item['mode_id']),None)
            cleanup = {'service_accepted':not already_restored, 'already_self_consumption':already_restored,
                       'observed_mode':current,'restored':current=='self_consumption','captured_at':captured.isoformat()}
            report['cleanup'][site] = cleanup
            if cleanup['restored']:
                with journal.connect() as db:
                    rows = db.execute('SELECT instruction_id,outcome FROM control_attempts WHERE run_id=? AND site_id=?',
                                      (args.run_id,site)).fetchall()
                    for row in rows:
                        outcome = {'submission':json.loads(row['outcome']) if row['outcome'] else None,'restoration':cleanup}
                        db.execute("UPDATE control_attempts SET state='restored',outcome=? WHERE instruction_id=?",
                                   (json.dumps(outcome,ensure_ascii=False),row['instruction_id']))
            return cleanup['restored']
        except Exception as exc:
            report['cleanup'][site] = {'restored':False,'error_type':type(exc).__name__}
            return False

    lock = journal.exclusive()
    locked = False
    try:
        lock.__enter__()
        locked = True
        dashboard = source.dashboard()
        contest = dashboard['contest']
        binding = bind_contest(contest, scope=args.scope) if args.live else {'candidate_capture_contest': contest}
        if args.live and args.scope=='mock' and args.expected_contest_start and contest['start_at']!=args.expected_contest_start:
            raise ValueError('active mock differs from the selected acceptance window')
        journal.bind(args.run_id, {'scope':args.scope, 'contest':binding,
                                  'stations':{site:s['suffix'] for site,s in cfg['stations'].items()}})
        report['contest'] = contest
        # Each site gets a fresh snapshot immediately before its own solve.
        # A bounded test does not reuse the first site's inputs three times.
        for site, spec in sorted(cfg['stations'].items()):
            if args.live:
                alignment_deadline = time.monotonic() + 8
                while True:
                    fresh_states, captured = source.states()
                    clock = next((x for x in fresh_states if x['entity_id']==f"sensor.{spec['suffix']}_device_time"),{})
                    if state_is_fresh(clock.get('last_changed'), now=captured, max_age_seconds=.8):
                        break
                    if time.monotonic() >= alignment_deadline:
                        raise RuntimeError('could not align a fresh device tick before bounded submission')
                    time.sleep(.2)
            snapshot, original_states = source.capture()
            coordinate = args.device_timezone or _infer_api_timezone(daily_points(snapshot['prices']), {})
            if coordinate is None:
                raise ValueError('device clock coordinate could not be established')
            controller = MILPController(run_id=args.run_id, store=store, journal=journal,
                                        stations=cfg['stations'], solver_seconds=args.solver_seconds,
                                        device_timezone=coordinate)
            snapshot['stations'] = {site:snapshot['stations'][site]}
            plan = controller.plan(snapshot)
            decision = plan['decisions'].get(site)
            if decision is None:
                report['sites'][site] = {'status':'no_candidate', 'solver':plan['stations'].get(site)}
                continue
            entities = {x['entity_id']:x for x in original_states}
            mode_id = f"select.{spec['suffix']}_operating_mode_device_runs_by_command_in_third_party_controlled"
            power_id = f"number.{spec['suffix']}_power_control"
            flow_id = f"select.{spec['suffix']}_grid_flow"
            initial_mode = entities.get(mode_id,{}).get('state')
            if args.live and initial_mode != 'self_consumption':
                report['sites'][site] = {'status':'blocked_existing_controller', 'decision_key':decision['id']}
                continue
            if args.live and ('third_party_control' not in entities.get(mode_id,{}).get('attributes',{}).get('options',[])
                              or decision['action']['kind'] not in entities.get(flow_id,{}).get('attributes',{}).get('options',[])):
                report['sites'][site] = {'status':'blocked_control_capability', 'decision_key':decision['id']}
                continue
            client = source.ha_client()

            def guard(command, index):
                if not contest_matches(source.dashboard()['contest'], binding, scope=args.scope):
                    return False
                states, captured = source.states()
                rows = {x['entity_id']:x for x in states}
                clock = rows.get(f"sensor.{spec['suffix']}_device_time",{})
                if not state_is_fresh(clock.get('last_changed'), now=captured, max_age_seconds=15):
                    return False
                if _device_tick(clock, coordinate) != command.issued_device_time:
                    return False
                if rows.get(mode_id,{}).get('state') != ('self_consumption' if index==0 else 'third_party_control'):
                    return False
                # Refresh may happen only during a read; HTTP writes are
                # never retried. Subsequent services use that current token.
                client.token = source.tokens['ha']
                if index==0:
                    # Register before the HTTP call, not after local evidence
                    # persistence: an interruption/DB failure can follow a write.
                    touched[site] = {'client':client,'mode_id':mode_id,'initial_mode':initial_mode}
                return True

            try:
                submitted = controller.submit(decision, client=client,
                    capabilities=entities.get(power_id,{}).get('attributes',{}), guard=guard, live=args.live)
            except ValueError as exc:
                report['sites'][site] = {'status':'blocked_candidate', 'decision_key':decision['id'],
                                        'reason':str(exc)}
                continue
            report['sites'][site] = submitted
            report['writes_performed'] += submitted['writes_performed']
            if submitted['writes_performed']:
                touched[site] = {'client':client, 'mode_id':mode_id, 'initial_mode':initial_mode}
            save()
            if submitted['status'] != 'accepted':
                if site in touched:
                    if restore(site):
                        touched.pop(site)
                    else:
                        raise RuntimeError('partial station restoration did not complete')
                continue
            receipt = SubmissionReceipt.model_validate(submitted['submission'])
            deadline = time.monotonic() + args.readback_seconds
            checks = []
            while time.monotonic() < deadline:
                states, captured = source.states()
                wanted = {f"sensor.{spec['suffix']}_{name}" for name in ('device_time','solar_power','home_load',
                    'battery_charging_power','battery_discharging_power','grid_import_power','grid_export_power')}
                wanted.update((mode_id,flow_id,power_id))
                submitted['last_observation'] = {'captured_at':captured.isoformat(), 'entities':[
                    {k:x.get(k) for k in ('entity_id','state','last_changed','last_updated','last_reported')}
                    for x in states if x['entity_id'] in wanted]}
                observation = build_telemetry_readback(states, suffix=spec['suffix'], receipt=receipt,
                    captured_wall_time=captured, device_timezone=coordinate)
                if isinstance(observation, ObservationUnavailable):
                    checks.append({'status':'unavailable','reason':observation.reason})
                else:
                    checked = controller.observe(decision['id'], observation)
                    checks.append(checked['verification'])
                    if checked['verification']['status'] == 'verified':
                        break
                time.sleep(min(1, max(0, deadline-time.monotonic())))
            submitted['readback_checks'] = checks
            submitted['execution_status'] = store.execution_status(decision['id'])
            if site in touched and restore(site):
                touched.pop(site)
            else:
                raise RuntimeError('station restoration did not complete')
            save()
        if not args.live:
            report['outcome'] = 'candidate_capture_completed' if len(report['sites'])==3 and all(s.get('status')=='not_sent' for s in report['sites'].values()) else 'candidate_capture_incomplete'
        else:
            report['outcome'] = 'completed' if len(report['sites'])==3 and all(s.get('execution_status')=='confirmed' for s in report['sites'].values()) else 'completed_with_unverified_or_blocked_sites'
    except SourceUnavailable as exc:
        report.update(outcome='source_failed', error_type=exc.category, http_status=exc.http_status)
    except KeyboardInterrupt:
        report.update(outcome='interrupted', error_type='KeyboardInterrupt')
    except Exception as exc:
        report.update(outcome='failed', error_type=type(exc).__name__)
    finally:
        # This bounded command's explicit scope includes restoring only stations
        # for which it actually attempted a service call. Do not reset others.
        for site in touched:
            restore(site)
        report['auth_refreshes'] = dict(source.refreshes)
        if locked:
            lock.__exit__(None,None,None)
        source.close()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
        print(json.dumps({k:report[k] for k in ('run_id','mode','outcome','writes_performed','cleanup')}, ensure_ascii=False))
    return 0 if report['outcome'] in ('completed','candidate_capture_completed') and all(x.get('restored') for x in report['cleanup'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
