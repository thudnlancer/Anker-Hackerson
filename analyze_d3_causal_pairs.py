"""Offline D3: pair visible API weather forecasts with later independent HA PV.

No network or device writes. API irradiance in the HA cache is never truth;
only the HA PV field may be used as the subsequent observation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import math

import pandas as pd


def instant(value):
    try:
        result = pd.Timestamp(value)
        if result.tzinfo is None or pd.isna(result):
            return None
        return result.tz_convert('UTC')
    except (TypeError, ValueError, OverflowError):
        return None


def load_ha_rows(cache_dir: Path):
    rows = defaultdict(list)
    for path in sorted(cache_dir.glob('site_*.jsonl')):
        site = path.name.removeprefix('site_').split('.')[0]
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get('site')) == site:
                rows[site].append(row)
    return rows


def analyze(snapshot_paths, cache_dir: Path, *, epoch_id: str,
            calibration: dict | None = None, min_pairs: int = 24,
            min_days: int = 2) -> dict:
    if not epoch_id or min_pairs < 1 or min_days < 1:
        raise ValueError('epoch_id, min_pairs and min_days must be valid')
    counters = Counter()
    pairs = []
    ha = load_ha_rows(cache_dir)
    calibration = calibration or {}
    for path in snapshot_paths:
        snapshot = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        for site, days in snapshot.get('sites', {}).items():
            for day in ('today', 'tomorrow'):
                block = days.get(day, {})
                raw_response = block.get('raw_response')
                response_hash = block.get('response_sha256')
                if raw_response is not None and response_hash is not None:
                    canonical = json.dumps(raw_response, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode('utf-8')
                    hash_valid = hashlib.sha256(canonical).hexdigest() == response_hash
                else:
                    hash_valid = False
                for point in block.get('points', []):
                    counters['forecast_points'] += 1
                    if snapshot.get('epoch_id') != epoch_id:
                        counters['cross_or_missing_epoch'] += 1
                        continue
                    if not hash_valid:
                        counters['missing_or_invalid_response_hash'] += 1
                        continue
                    raw_points = raw_response.get('points', []) if isinstance(raw_response, dict) else []
                    if not any(isinstance(source, dict) and
                               source.get('timestamp', source.get('start')) == point.get('target_device_time') and
                               source.get('solar_irradiance_wm2') == point.get('solar_irradiance_wm2')
                               for source in raw_points):
                        counters['projection_not_in_raw_response'] += 1
                        continue
                    if block.get('forecast_source') != 'simulator_api_weather':
                        counters['invalid_forecast_source'] += 1
                        continue
                    retrieved = instant(block.get('retrieved_at'))
                    before = instant(block.get('source_device_time_before_response'))
                    after = instant(block.get('source_device_time_after_response'))
                    target = instant(point.get('target_device_time'))
                    if not all(x is not None for x in (retrieved, before, after, target)) or after < before:
                        counters['invalid_forecast_clock'] += 1
                        continue
                    if target <= after:
                        counters['target_not_after_response_clock'] += 1
                        continue
                    try:
                        irradiance = float(point['solar_irradiance_wm2'])
                        if not math.isfinite(irradiance) or irradiance < 0:
                            raise ValueError()
                    except (KeyError, TypeError, ValueError):
                        counters['invalid_forecast_value'] += 1
                        continue
                    candidates = []
                    wrong_epoch = False
                    for row in ha.get(str(site), []):
                        device = instant(row.get('device_time'))
                        received = instant(row.get('received_at'))
                        if device is None or received is None or not target <= device < target + pd.Timedelta(hours=1):
                            continue
                        if row.get('epoch_id') != epoch_id:
                            wrong_epoch = True
                            continue
                        if received <= retrieved or row.get('source') != 'home_assistant_read_only_snapshot':
                            continue
                        try:
                            pv = float(row['pv'])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if math.isfinite(pv) and pv >= 0:
                            candidates.append((device, received, pv))
                    if not candidates:
                        counters['cross_or_missing_epoch' if wrong_epoch else 'missing_subsequent_independent_ha_pv'] += 1
                        continue
                    # One deterministic HA observation per forecast target hour;
                    # do not inflate sample count with repeated polling ticks.
                    device, received, observed = min(candidates, key=lambda x: (x[0], x[1]))
                    record = {'site': str(site), 'epoch_id': epoch_id, 'day': day,
                              'snapshot_sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                              'response_sha256': block.get('response_sha256'),
                              'forecast_retrieved_at': retrieved.isoformat(),
                              'source_device_time_before_response': before.isoformat(),
                              'source_device_time_after_response': after.isoformat(),
                              'target_device_time': target.isoformat(),
                              'ha_device_time': device.isoformat(), 'ha_received_at': received.isoformat(),
                              'forecast_irradiance_wm2': irradiance, 'observed_ha_pv_w': observed,
                              'observation_source': 'home_assistant_read_only_snapshot',
                              'irradiance_is_independent_truth': False}
                    calibration_item = calibration.get(str(site))
                    scale = None
                    if isinstance(calibration_item, dict):
                        calibration_wall = instant(calibration_item.get('frozen_at'))
                        calibration_device = instant(calibration_item.get('last_training_device_time'))
                        if (calibration_item.get('epoch_id') == epoch_id and
                                calibration_wall is not None and calibration_wall < retrieved and
                                calibration_device is not None and calibration_device < before):
                            scale = calibration_item.get('scale_w_per_wm2')
                    try:
                        scale = float(scale)
                        if not math.isfinite(scale) or scale < 0:
                            raise ValueError()
                    except (TypeError, ValueError):
                        scale = None
                    if scale is not None:
                        record['predicted_pv_w'] = max(0., min(5000., irradiance * scale))
                        record['pv_error_w'] = record['predicted_pv_w'] - observed
                    else:
                        record['predicted_pv_w'] = None
                        record['pv_error_w'] = None
                        counters['missing_frozen_pv_conversion'] += 1
                    pairs.append(record)
    # Repeated captures of one site/target are not independent samples. Keep
    # the earliest causally valid forecast, avoiding best-of-hindsight picks.
    unique = {}
    for pair in sorted(pairs, key=lambda p: p['forecast_retrieved_at']):
        unique.setdefault((pair['site'], pair['target_device_time']), pair)
    pairs = list(unique.values())
    scored = [p for p in pairs if p['pv_error_w'] is not None]
    site_days = {(p['site'], p['target_device_time'][:10]) for p in scored}
    ready = len(scored) >= min_pairs and len(site_days) >= min_days
    error = [p['pv_error_w'] for p in scored]
    return {'mode': 'offline_d3_causal_pv_pairing', 'epoch_id': epoch_id,
            'status': 'evaluation_ready' if ready else 'insufficient_data',
            'd3_complete': False, 'valid_pairs': len(pairs), 'scored_pairs': len(scored),
            'independent_site_days': len(site_days), 'required_pairs': min_pairs,
            'required_site_days': min_days, 'exclusions': dict(counters),
            'pv_mae_w': sum(map(abs, error)) / len(error) if error else None,
            'pv_rmse_w': math.sqrt(sum(x*x for x in error) / len(error)) if error else None,
            'error_scope': 'API-irradiance-driven PV prediction vs later independent HA PV; includes irradiance-to-PV conversion error, not irradiance forecast accuracy',
            'paired_observations': pairs, 'ha_connections': 0, 'device_writes': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshots', type=Path, nargs='+', required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--epoch-id', required=True)
    parser.add_argument('--calibration', type=Path, help='optional frozen per-site PV scale JSON with epoch_id, frozen_at and last_training_device_time')
    parser.add_argument('--min-pairs', type=int, default=24)
    parser.add_argument('--min-days', type=int, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    scales = json.loads(args.calibration.read_text(encoding='utf-8')) if args.calibration else None
    report = analyze(args.snapshots, args.cache_dir, epoch_id=args.epoch_id,
                     calibration=scales, min_pairs=args.min_pairs, min_days=args.min_days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('status', 'valid_pairs', 'scored_pairs', 'exclusions')}, ensure_ascii=False))
