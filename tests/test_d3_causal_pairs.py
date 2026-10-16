import json
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.analysis.analyze_d3_causal_pairs import analyze


def fixture(tmp_path: Path, *, target='2032-01-01T09:00:00Z', epoch='mock-2',
            ha_epoch='mock-2', ha_source='home_assistant_read_only_snapshot',
            ha_received='2026-10-14T00:02:00Z'):
    raw = {'points': [{'timestamp': target, 'solar_irradiance_wm2': 400}]}
    response_hash = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':')).encode('utf-8')).hexdigest()
    snapshot = {'epoch_id': epoch, 'sites': {'1': {'today': {
        'forecast_source': 'simulator_api_weather',
        'raw_response': raw, 'response_sha256': response_hash,
        'retrieved_at': '2026-10-14T00:01:00Z',
        'source_device_time_before_response': '2032-01-01T08:00:00Z',
        'source_device_time_after_response': '2032-01-01T08:15:00Z',
        'points': [{'target_device_time': target, 'solar_irradiance_wm2': 400}]}}}}
    path = tmp_path / 'forecast.json'
    path.write_text(json.dumps(snapshot), encoding='utf-8')
    cache = tmp_path / 'cache'
    cache.mkdir()
    row = {'site': '1', 'epoch_id': ha_epoch, 'source': ha_source,
           'device_time': '2032-01-01T09:15:00Z', 'received_at': ha_received,
           'pv': 200, 'solar_irradiance_wm2': 400,
           'irradiance_source': 'api_weather_at_HA_device_hour'}
    (cache / 'site_1.nodeA.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
    return path, cache


def calibration():
    return {'1': {'epoch_id': 'mock-2', 'scale_w_per_wm2': .5,
                  'frozen_at': '2026-10-14T00:00:00Z',
                  'last_training_device_time': '2032-01-01T07:00:00Z'}}


def test_valid_forecast_pairs_only_to_later_independent_ha_pv(tmp_path):
    path, cache = fixture(tmp_path)
    result = analyze([path], cache, epoch_id='mock-2', calibration=calibration(),
                     min_pairs=1, min_days=1)
    assert result['valid_pairs'] == result['scored_pairs'] == 1
    assert result['pv_mae_w'] == 0
    assert result['paired_observations'][0]['irradiance_is_independent_truth'] is False
    assert result['d3_complete'] is False


def test_old_or_current_target_is_excluded(tmp_path):
    path, cache = fixture(tmp_path, target='2032-01-01T08:15:00Z')
    result = analyze([path], cache, epoch_id='mock-2')
    assert result['valid_pairs'] == 0
    assert result['exclusions']['target_not_after_response_clock'] == 1


def test_old_ha_or_cross_epoch_or_api_source_cannot_be_truth(tmp_path):
    for change in ({'ha_received': '2026-10-14T00:00:00Z'},
                   {'ha_epoch': 'other'}, {'ha_source': 'simulator_api_weather'}):
        folder = tmp_path / str(len(list(tmp_path.iterdir())))
        folder.mkdir()
        path, cache = fixture(folder, **change)
        result = analyze([path], cache, epoch_id='mock-2')
        assert result['valid_pairs'] == 0
        assert result['status'] == 'insufficient_data'


def test_missing_or_noncausal_calibration_is_explicitly_unscored(tmp_path):
    path, cache = fixture(tmp_path)
    assert analyze([path], cache, epoch_id='mock-2')['scored_pairs'] == 0
    late = calibration()
    late['1']['frozen_at'] = '2026-10-14T00:03:00Z'
    result = analyze([path], cache, epoch_id='mock-2', calibration=late)
    assert result['valid_pairs'] == 1
    assert result['scored_pairs'] == 0
    assert result['exclusions']['missing_frozen_pv_conversion'] == 1


def test_duplicate_snapshots_count_one_target(tmp_path):
    path, cache = fixture(tmp_path)
    result = analyze([path, path], cache, epoch_id='mock-2', calibration=calibration(),
                     min_pairs=2, min_days=1)
    assert result['valid_pairs'] == 1
    assert result['status'] == 'insufficient_data'


def test_tampered_response_is_excluded(tmp_path):
    path, cache = fixture(tmp_path)
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    snapshot['sites']['1']['today']['raw_response']['points'][0]['solar_irradiance_wm2'] = 999
    path.write_text(json.dumps(snapshot), encoding='utf-8')
    result = analyze([path], cache, epoch_id='mock-2', calibration=calibration())
    assert result['valid_pairs'] == 0
    assert result['exclusions']['missing_or_invalid_response_hash'] == 1


def test_projected_point_not_in_raw_response_is_excluded(tmp_path):
    path, cache = fixture(tmp_path)
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    snapshot['sites']['1']['today']['points'][0]['solar_irradiance_wm2'] = 999
    path.write_text(json.dumps(snapshot), encoding='utf-8')
    result = analyze([path], cache, epoch_id='mock-2', calibration=calibration())
    assert result['valid_pairs'] == 0
    assert result['exclusions']['projection_not_in_raw_response'] == 1
