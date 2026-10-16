import audit_milp_batch
from scripts.analysis.audit_milp_batch import audit


def test_legacy_milp_audit_entrypoint():
    assert audit_milp_batch.audit is audit
