#!/bin/bash
set -e
cd ~/solarbank-experiment
.venv311/bin/python -c "import scipy, pandas, numpy, requests; print('deps ok')"
.venv311/bin/python -m pytest tests/test_price_anchor.py tests/test_config_solver_seconds.py -q 2>&1 | tail -3
