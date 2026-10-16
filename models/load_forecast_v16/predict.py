"""CLI: 18 oldest-to-newest power values in W -> next point + future 12 points.

Run with the benchmark environment (torch + chronos required):
    ../../foundation-model-benchmark/.venv/bin/python predict.py \
        --history-w 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50
"""
import argparse
import json

from inference import CANDIDATE, FREEZE, Engine


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--history-w', type=float, nargs=18, required=True,
                   help='18 historical 15-min power values in W, oldest -> newest')
    a = p.parse_args()
    engine = Engine()
    next_w, future_w = engine.forecast(a.history_w)
    print(json.dumps(dict(
        candidate=CANDIDATE,
        bundle_sha256=FREEZE['bundle_sha256'],
        input_count=18,
        history_order='oldest_to_newest',
        sample_minutes=15,
        recursive_calls=12,
        next_15min_W=next_w,
        future_12points_W=future_w,
        development_pass=FREEZE['selection']['development_pass'],
        active_default=FREEZE['selection']['active_default'],
        formal_acceptance=FREEZE['selection']['formal_acceptance'],
    ), ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
