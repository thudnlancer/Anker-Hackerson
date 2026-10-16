"""The legacy import and CLI both resolve to the structured analysis module."""

import audit_strategy_results
from scripts.analysis.audit_strategy_results import audit


def test_legacy_strategy_audit_entrypoint():
    assert audit_strategy_results.audit is audit
