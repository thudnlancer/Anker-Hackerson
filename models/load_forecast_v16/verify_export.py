"""Export consistency checks for the V16 MILP deployment copy.

Checks:
1. bundle.joblib sha256 matches model_freeze.json.
2. Behavior probes: constant histories 0/10/30/50/100 W -> 12 outputs equal input
   (low-load follow region, lo=0.204 kW).
3. Parity with the original frozen pipeline
   (foundation-model-benchmark/src/v16 Engine.rollout, candidate V12_bolt25)
   on fixed-seed random histories, atol 1e-5 kW.

Run with the benchmark environment:
    ../../foundation-model-benchmark/.venv/bin/python verify_export.py
Exit code 0 on PASS, 1 on FAIL. Read-only; does not modify any experiment files.
"""
import sys
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
SRC = BASE.parent.parent / 'foundation-model-benchmark' / 'src'
sys.path.insert(0, str(SRC / 'v16'))
sys.path.insert(0, str(SRC))

from inference import FREEZE, Engine, _check_integrity  # noqa: E402


def main():
    failures = []

    # 1. integrity
    try:
        _check_integrity()
        print('[PASS] bundle.joblib sha256 matches model_freeze.json:', FREEZE['bundle_sha256'])
    except Exception as e:  # noqa: BLE001
        failures.append(f'sha256: {e}')

    engine = Engine()

    # 2. behavior probes (constants within the low-load follow region)
    levels = np.array([0, 10, 30, 50, 100], dtype='float32')
    seed = np.repeat((levels / 1000)[:, None], 18, axis=1)
    got = engine.rollout(seed)
    want = np.repeat((levels / 1000)[:, None], 12, axis=1)
    try:
        np.testing.assert_allclose(got, want, atol=1e-5)
        print('[PASS] behavior probes 0/10/30/50/100 W constant -> constant')
    except AssertionError as e:
        failures.append(f'behavior probes: {e}')

    # 3. parity with original frozen pipeline
    import joblib
    from core import RUN, CONFIGS, Engine as RefEngine, Pool
    cid = FREEZE['candidate']
    assert cid == 'V12_bolt25' and CONFIGS[cid]['features'] == 18
    ref = RefEngine(Pool(), joblib.load(RUN / FREEZE['bundle']), RUN / 'models/final')
    rng = np.random.default_rng(20261017)
    x = rng.uniform(0.0, 5.0, size=(32, 18)).astype('float32')
    want = ref.rollout(cid, x)
    got = engine.rollout(x)
    try:
        np.testing.assert_allclose(got, want, atol=1e-5)
        print(f'[PASS] parity vs src/v16 Engine.rollout on 32 random origins (max abs diff '
              f'{float(np.abs(got - want).max()):.3e} kW)')
    except AssertionError as e:
        failures.append(f'parity: {e}')

    if failures:
        print('[FAIL]', len(failures), 'check(s) failed')
        for f in failures:
            print('  -', f)
        sys.exit(1)
    print('[PASS] all checks passed')


if __name__ == '__main__':
    main()
