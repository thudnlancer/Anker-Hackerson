#!/bin/bash
# CI-equivalent verification on Linux (matches .github/workflows/explainability.yml python job)
set -e
cd ~/solarbank-experiment
source .venv311/bin/activate
export PYTHONPATH=src
echo "=== git state ==="
git log --oneline -1
git status --porcelain | head -20
echo "=== strategy tests (python -m pytest tests -q) ==="
python -m pytest tests -q 2>&1 | tail -5
echo "=== doc links ==="
python scripts/maintenance/check_doc_links.py 2>&1 | tail -3
echo "=== explainability-app tests ==="
cd explainability-app
PYTHONPATH=src:../src python -m pytest -q 2>&1 | tail -5
cd ..
echo "=== deployment preflight ==="
python scripts/maintenance/deployment_preflight.py --expected-commit "$(git rev-parse HEAD)" --output outputs/ci_deployment_preflight.json 2>&1 | tail -5
echo "=== ALL CI-EQUIVALENT CHECKS DONE ==="
