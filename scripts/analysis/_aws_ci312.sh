#!/bin/bash
# Full CI-equivalent python job with Python 3.12 + locked deps (uv-managed)
cd ~/solarbank-experiment
UV=~/.local/bin/uv
VENV=.venv312/bin/python
$UV pip install --python $VENV -r explainability-app/requirements.lock -r explainability-app/requirements.shared.lock 2>&1 | tail -2
$UV pip install --python $VENV pytest 2>&1 | tail -1
$UV pip install --python $VENV --no-deps -e explainability-app 2>&1 | tail -1
echo "=== strategy tests ==="
PYTHONPATH=src $VENV -m pytest tests -q 2>&1 | tail -4
echo "=== doc links ==="
$VENV scripts/maintenance/check_doc_links.py 2>&1 | tail -2
echo "=== explainability-app tests ==="
cd ~/solarbank-experiment/explainability-app
PYTHONPATH=src:../src ~/solarbank-experiment/$VENV -m pytest -q 2>&1 | tail -6
echo "=== DONE ==="
