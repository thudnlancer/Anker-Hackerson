from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from energy_explain.mock_acceptance import match_final, result_fingerprint, same_window, settlement_record, shadow_evaluate
from energy_explain.mock_acceptance import require_window_alignment, SnapshotAlignmentPending
from energy_explain.schemas import SettlementIn
from energy_explain.store import Store
from tests.helpers import make_decision


SELECTED = dict(track='mock', start_at='2031-01-01T00:00:00Z',
    end_at='2031-01-01T00:56:00Z', date_range='2031-01-01 ~ 2031-01-08', days=7, speed=180)


def dashboard():
    sites = []
    for site in ('1', '2', '3'):
        sites.append(dict(site_id=site, metrics=dict(baseline_cost=10., saving=1., saving_settled=-2.,
            device_revenue=8., baseline_revenue=7., baseline_settled=5., device_settled=3.,
            saving_settled_percent=-20., device_grid_import_cost=2., device_grid_export_revenue=3.,
            device_net_grid_cost=-1., valid=True, shortage_count=0)))
    final = dict(track='mock', date_range=SELECTED['date_range'], days=7, speed=180,
        ended_at='2031-01-01T00:56:04Z', sites=sites,
        total=dict(baseline_cost=30., saving=3., saving_settled=-6., device_revenue=24.,
                   baseline_revenue=21., saving_settled_percent=-20., valid=True))
    return dict(contest=dict(SELECTED, mode='ended'), results=dict(runs=[{'track':'old'}, final]))


def test_final_uses_unique_new_complete_result_not_first_row():
    record = match_final(dashboard(), SELECTED, 1)
    assert record['total']['saving_settled'] == -6
    assert record['ended_at'] == '2031-01-01T00:56:04Z'


def test_unrelated_new_row_does_not_promote_an_already_existing_match():
    value = dashboard()
    previous = [result_fingerprint(row) for row in value['results']['runs']]
    value['results']['runs'].append({'track':'unrelated-new'})
    assert match_final(value, SELECTED, 2, previous) is None


@pytest.mark.parametrize('case', ['unchanged_count','other_window','running','duplicate','missing_site',
                                 'nonfinite','cost_mismatch','old_final','identity_conflict'])
def test_ambiguous_invalid_and_unrelated_results_stay_unsettled(case):
    value = dashboard(); record = value['results']['runs'][1]; previous = 1
    if case == 'unchanged_count': previous = 2
    elif case == 'other_window': value['contest']['start_at'] = '2031-01-01T00:01:00Z'
    elif case == 'running': value['contest']['mode'] = 'running'
    elif case == 'duplicate': value['results']['runs'].append(deepcopy(record))
    elif case == 'missing_site': record['sites'].pop()
    elif case == 'nonfinite': record['sites'][0]['metrics']['saving_settled'] = float('nan')
    elif case == 'cost_mismatch': record['sites'][0]['metrics']['device_net_grid_cost'] = 123
    elif case == 'old_final': record['ended_at'] = '2031-01-01T00:50:00Z'
    elif case == 'identity_conflict': record['start_at'] = '2031-01-01T00:01:00Z'
    assert match_final(value, SELECTED, previous) is None


def test_mock_source_is_preserved_and_demo_cannot_receive_external_scores(tmp_path):
    store = Store(tmp_path/'test.sqlite')
    store.add_decision(make_decision(source_kind='external'))
    pending = settlement_record('test-run', SELECTED, captured_at='2026-10-15T00:00:00Z')
    store.add_settlement('test-run', pending)
    final = settlement_record('test-run', SELECTED, final=match_final(dashboard(), SELECTED, 1),
                              captured_at='2026-10-15T01:00:00Z', verification={'verified':True})
    store.add_settlement('test-run', final)
    saved = store.get_settlement('test-run')
    assert saved['source_kind'] == 'simulator_mock'
    assert saved['saving_settled_eur'] == -6
    assert saved['raw_fields']['independent_signature_verification'] is False
    store.add_decision(make_decision(run_id='demo-only'))
    with pytest.raises(ValueError): store.add_settlement('demo-only', final)
    assert not store.add_settlement('test-run', final)['created']


def test_shadow_namespace_does_not_mutate_control_identity():
    original = dict(run_id='run', site_id='1', decision_id='tick-original')
    seen = []
    def evaluate(snapshot, *, evidence_sink, **kwargs):
        evidence_sink(original)
        return {'stations':{}}
    with patch('energy_explain.mock_acceptance.evaluate_snapshot', evaluate):
        shadow_evaluate({}, evidence_sink=seen.append)
    assert original['decision_id'] == 'tick-original'
    assert seen[0]['decision_id'] == 'shadow-tick-original'


def test_prior_mock_clock_is_rejected_before_checkpoint_acceptance():
    snapshot={'prices':{'today':{'points':[{'start':'2031-01-01T00:00:00Z','import_price':.3,'export_price':.1}]}},
        'stations':{'1':{'state':{'device_time':'2031-02-01 08:00:00'}}}}
    with pytest.raises(SnapshotAlignmentPending):require_window_alignment(snapshot, SELECTED)
    snapshot['stations']['1']['state']['device_time']='2031-01-01 08:00:00'
    require_window_alignment(snapshot, SELECTED)


def test_history_link_survives_newer_shadow_candidate_and_is_run_isolated(tmp_path):
    from energy_explain.execution import SubmissionReceipt
    store = Store(tmp_path/'history.sqlite')
    saved = store.add_decision(make_decision(source_kind='external'))
    d = store.get_decision(saved['id']); wall = datetime.now(timezone.utc)-timedelta(seconds=1)
    # The fixture observation precedes the current wall clock.
    receipt = SubmissionReceipt.model_validate(dict(run_id=d['run_id'],site_id=d['site_id'],
        decision_id=d['decision_id'],instruction_id='actual-command',event_id='actual-receipt',
        issued_device_time=d['snapshot']['device_time'],issued_wall_time=wall.isoformat(),
        completed_wall_time=wall.isoformat(),mode='third_party_control',flow='charge',target_kw=d['action']['power_kw'],
        semantics='battery_charge_target',source_ref='offline-test',sender_status='accepted'))
    store.add_submission_receipt(d['id'],receipt)
    newer = make_decision(source_kind='external',decision_id='shadow-later')
    newer.snapshot.device_time += timedelta(minutes=15)
    newer_id = store.add_decision(newer)['id']
    station = store.stations('test-run')['stations'][0]
    assert station['id'] == newer_id
    assert station['last_submission']['decision_key'] == d['id']
    assert station['last_submission']['execution_status'] == 'submitted'
    store.add_decision(make_decision(source_kind='external',run_id='other-run'))
    assert store.stations('other-run')['stations'][0]['last_submission'] is None
