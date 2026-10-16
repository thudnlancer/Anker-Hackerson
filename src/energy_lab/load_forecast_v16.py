"""Prediction-layer plugin: frozen V16 load forecaster (V12_bolt25 hybrid).

Vendors the self-contained export package in ``models/load_forecast_v16/``
(bundle.joblib + model_freeze.json + inference.py, sha256-pinned) and exposes
it through a lazy class interface used by offline replay / live assembly:

    forecaster = LoadForecastV16()            # loads and hash-checks once
    future_w = forecaster.forecast_w(history) # 18 W samples -> 12 x 15-min W

Heavy dependencies (torch + chronos-forecasting) are imported lazily by the
vendored Engine, so importing this module stays cheap.  The chronos-bolt-small
weights (models/load_forecast_v16/bolt_small/model.safetensors, 182MB) are NOT
in git (over GitHub's file limit); provision them per the package README.

Freeze status carried from the export package: development_pass=false,
active_default was 'V12 unchanged', formal_acceptance=false.  Integrated here
by explicit decision; treat downstream numbers accordingly.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "load_forecast_v16"
INFERENCE_PATH = MODEL_DIR / "inference.py"
WEIGHTS_PATH = MODEL_DIR / "bolt_small" / "model.safetensors"


def _load_inference_module():
    spec = importlib.util.spec_from_file_location("load_forecast_v16_inference",
                                                  INFERENCE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load vendored inference at {INFERENCE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LoadForecastV16:
    """Lazy, hash-verified wrapper around the frozen V16 Engine."""

    HISTORY_POINTS = 18
    DEFAULT_STEPS = 12

    def __init__(self) -> None:
        self._engine = None

    def available(self) -> bool:
        """True when weights and heavy dependencies can actually load."""
        if not WEIGHTS_PATH.is_file():
            return False
        try:
            import torch  # noqa: F401
            import chronos  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_loaded(self) -> None:
        if self._engine is None:
            if not WEIGHTS_PATH.is_file():
                raise RuntimeError(
                    f"missing chronos-bolt-small weights at {WEIGHTS_PATH}; "
                    "provision per models/load_forecast_v16/README.md")
            self._engine = _load_inference_module().Engine()

    def forecast_w(self, history_w, steps: int = DEFAULT_STEPS) -> np.ndarray:
        """18 non-negative W samples (oldest -> newest) -> `steps` future W."""
        self._ensure_loaded()
        _, future = self._engine.forecast(history_w, steps=steps)
        return np.asarray(future, dtype=float)
