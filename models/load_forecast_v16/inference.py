"""Frozen V16 candidate V12_bolt25 inference, self-contained for MILP integration.

Candidate = 0.75 * V12 tree (18 lags -> 1) + 0.25 * chronos-bolt-small zero-shot
median, then low-load follow blend. 18 inputs oldest->newest, recursive 12 steps,
own-prediction feedback only. Mirrors foundation-model-benchmark/src/v16 Engine
semantics exactly.

STATUS (from runs/v16/model_freeze.json, must stay in any downstream doc):
development_pass=false, validation flag one/MAE_W (UCI single-step MAE regressed
1.09% vs V12), active_default='V12 unchanged', formal_acceptance=false.
"""
import os

os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np

BASE = Path(__file__).resolve().parent
BUNDLE_PATH = BASE / 'bundle.joblib'
BOLT_PATH = BASE / 'bolt_small'
FREEZE = json.loads((BASE / 'model_freeze.json').read_text())

CANDIDATE = FREEZE['candidate']          # V12_bolt25
ALPHA = float(FREEZE['config']['alpha'])  # 0.25


def _check_integrity():
    digest = hashlib.sha256(BUNDLE_PATH.read_bytes()).hexdigest()
    if digest != FREEZE['bundle_sha256']:
        raise RuntimeError(f'bundle.joblib sha256 {digest} != frozen {FREEZE["bundle_sha256"]}')


def _v12_scalar(v12, x):
    """engine13.scalar semantics for the embedded V12 bundle; x: (n,18) float32 kW."""
    last = x[:, -1].astype(float)
    v = v12['model'].predict(x)
    delta = v if v12['target'] == 'residual_latest' else v - last
    out = np.maximum(last + v12['alpha'] * delta, 0)
    if v12['follow']:
        w = np.clip((v12['training_P10_kW'] - last) / (v12['training_P10_kW'] - v12['training_min_kW']), 0, 1)
        out = (1 - w) * out + w * last
    return np.maximum(out, 0)


class _Bolt:
    """chronos-bolt-small zero-shot, median of next step, clipped at 0."""

    def __init__(self):
        import torch
        from chronos import BaseChronosPipeline
        torch.set_num_threads(4)
        self.torch = torch
        self.pipe = BaseChronosPipeline.from_pretrained(
            str(BOLT_PATH), device_map='cpu', torch_dtype=torch.float32, local_files_only=True)
        self.pipe.model.eval()

    def next(self, x):
        torch = self.torch
        with torch.inference_mode():
            outs = []
            for i in range(0, len(x), 128):
                q = self.pipe.predict(torch.from_numpy(x[i:i + 128].copy()), prediction_length=1)
                outs.append(q[:, self.pipe.quantiles.index(.5), 0].cpu().numpy())
        raw = np.concatenate(outs).astype(np.float32)
        assert raw.shape == (len(x),) and np.isfinite(raw).all()
        return np.maximum(raw, 0)


class Engine:
    """Frozen V12_bolt25: load once per process, then call rollout/forecast."""

    def __init__(self):
        _check_integrity()
        cfg = FREEZE['config']
        assert CANDIDATE == 'V12_bolt25' and cfg['kind'] == 'hybrid' and cfg['expert'] == 'bolt'
        assert cfg['features'] == 18 and cfg['follow'] is True
        self.bundle = joblib.load(BUNDLE_PATH)
        assert self.bundle['v12']['target'] == 'direct' and self.bundle['v12']['follow'] is True
        self.bolt = _Bolt()

    def next(self, x):
        x = np.asarray(x, dtype=np.float32)
        assert x.ndim == 2 and x.shape[1] == 18 and np.isfinite(x).all() and (x >= 0).all()
        last = x[:, -1]
        raw = self.bolt.next(x)
        out = (1 - ALPHA) * _v12_scalar(self.bundle['v12'], x) + ALPHA * raw
        out = np.maximum(out, 0)
        lo = self.bundle['minimum']
        p10 = self.bundle['p10']
        w = np.clip((p10 - last) / max(p10 - lo, 1e-8), 0, 1)
        out = (1 - w) * out + w * last
        return np.maximum(out, 0).astype(np.float32)

    def rollout(self, x, steps=12):
        buf = np.asarray(x, dtype=np.float32).copy()
        out = []
        for _ in range(steps):
            q = self.next(buf)
            out.append(q)
            buf = np.column_stack([buf[:, 1:], q]).astype(np.float32)
        p = np.column_stack(out)
        assert np.isfinite(p).all() and (p >= 0).all()
        return p

    def forecast(self, history_w, steps=12):
        """history_w: 18 power values in W, oldest -> newest. Returns (next_W, [steps] W)."""
        x = np.asarray(history_w, dtype=np.float32)
        if x.shape != (18,) or not np.isfinite(x).all() or (x < 0).any():
            raise ValueError('history_w must be 18 finite non-negative values in W, oldest -> newest')
        p = self.rollout((x / 1000)[None, :], steps=steps)[0] * 1000
        return float(p[0]), p.tolist()
