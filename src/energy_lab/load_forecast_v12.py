"""Prediction-layer plugin: frozen L12_low_follow load forecaster (V12).

Vendors the self-contained export package in ``models/load_forecast_v12/``
(model.joblib + model_freeze.json + inference.py, sha256-pinned) and exposes
it through a lazy class interface used by offline replay / live assembly:

    forecaster = LoadForecastV12()            # loads and hash-checks once
    future_w = forecaster.forecast_w(history) # 18 W samples -> 12 x 15-min W

The frozen inference contract lives in the vendored inference.py; this adapter
only resolves the model location and caches the bundle.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "load_forecast_v12"
INFERENCE_PATH = MODEL_DIR / "inference.py"


def _load_inference_module():
    spec = importlib.util.spec_from_file_location("load_forecast_v12_inference",
                                                  INFERENCE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load vendored inference at {INFERENCE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LoadForecastV12:
    """Lazy, hash-verified wrapper around the frozen V12 bundle."""

    HISTORY_POINTS = 18
    DEFAULT_STEPS = 12

    def __init__(self) -> None:
        self._bundle = None
        self._inference = None

    def _ensure_loaded(self) -> None:
        if self._bundle is None:
            self._inference = _load_inference_module()
            self._bundle = self._inference.load_bundle()

    def forecast_w(self, history_w, steps: int = DEFAULT_STEPS) -> np.ndarray:
        """18 non-negative W samples (oldest -> newest) -> `steps` future W."""
        self._ensure_loaded()
        _, future = self._inference.forecast(self._bundle, history_w, steps=steps)
        return np.asarray(future, dtype=float)
