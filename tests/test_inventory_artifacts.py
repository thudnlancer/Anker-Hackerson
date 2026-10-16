from scripts.maintenance.inventory_artifacts import PROTECTED


def test_online_incident_and_followup_evidence_are_protected():
    assert {"local_mock_20261016_run2", "local_mock_20261016_run3",
            "local_mock_20261016_postmerge_readonly",
            "local_mock_20261016_o7_readonly_20261016"} <= PROTECTED
