"""Frozen L12_low_follow inference, self-contained for MILP integration.

Contract (must not change without a new freeze):
- Input: 18 historical 15-minute power samples, oldest -> newest, unit W.
  Internally converted to kW float32; feature order power_lag_18..power_lag_1.
- Output: next 15-minute point (1 scalar) and recursive future 12 points (3 h).
- Post-processing per step: max(raw,0) -> low-load follow blend -> max(.,0).
  lo=training_min_kW, hi=training_P10_kW, w=clip((hi-latest)/(hi-lo),0,1),
  out=(1-w)*raw+w*latest. Recursion feeds back own predictions only.
"""
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np

BASE = Path(__file__).resolve().parent
MODEL_PATH = BASE / 'model.joblib'
FREEZE_PATH = BASE / 'model_freeze.json'

FREEZE = json.loads(FREEZE_PATH.read_text())


def _check_integrity():
    digest = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
    if digest != FREEZE['model_sha256']:
        raise RuntimeError(f'model.joblib sha256 {digest} != frozen {FREEZE["model_sha256"]}')


def load_bundle():
    _check_integrity()
    bundle = joblib.load(MODEL_PATH)
    assert bundle['input_features'] == 18 and bundle['output_targets'] == 1
    assert bundle['target'] == 'direct' and bundle['follow'] is True
    return bundle


def _scalar(bundle, x):
    """x: (n,18) float32 kW, oldest -> newest. Returns (n,) kW, one step ahead."""
    assert x.ndim == 2 and x.shape[1] == 18 and np.isfinite(x).all()
    raw = bundle['model'].predict(x)
    if bundle['target'] == 'residual_latest':
        raw = raw + x[:, -1]
    raw = np.maximum(raw, 0)
    if bundle['follow']:
        lo = bundle['training_min_kW']
        hi = bundle['training_P10_kW']
        assert hi > lo
        weight = np.clip((hi - x[:, -1].astype(float)) / (hi - lo), 0, 1)
        raw = (1 - weight) * raw + weight * x[:, -1]
    return np.maximum(raw, 0)


def _rollout(bundle, x, steps=12):
    buf = np.asarray(x, dtype='float32').copy()
    out = []
    for _ in range(steps):
        z = _scalar(bundle, buf)
        out.append(z)
        buf = np.column_stack([buf[:, 1:], z]).astype('float32')
    return np.column_stack(out)


def forecast(bundle, history_w, steps=12):
    """history_w: sequence of 18 power values in W, oldest -> newest.

    Returns (next_W, future_W): next 15-min point and `steps` future points, both in W.
    """
    x = np.asarray(history_w, dtype='float32') / 1000.0
    if x.shape != (18,) or not np.isfinite(x).all() or (x < 0).any():
        raise ValueError('history_w must be 18 finite non-negative values in W, oldest -> newest')
    out = _rollout(bundle, x[None, :], steps=steps)[0] * 1000.0
    return float(out[0]), out.tolist()
