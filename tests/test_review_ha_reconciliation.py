from scripts.online.review_ha_reconciliation import review


def fixture(mode="self_consumption", tick="2026-10-17 08:30:00",
            mode_updated="2026-10-16T03:15:00+00:00"):
    return {"captured_at": "2026-10-16T03:16:00+00:00", "sites": {"1": {
        "mode": {"entity_id": "select.anker_solix_solarbank_max_ac_1502_mode",
                 "state": mode, "last_updated": mode_updated},
        "device_time": {"entity_id": "sensor.anker_solix_solarbank_max_ac_1502_device_time",
                        "state": tick}}}}


def test_review_requires_matching_current_incident_and_advancing_readback():
    journal = {"version": 1, "pending": {"1": {
        "incident_id": "current", "suffix": "anker_solix_solarbank_max_ac_1502",
        "created_at": "2026-10-16T03:14:15+00:00"}}}
    before = fixture(tick="2026-10-17 08:15:00")
    result = review(journal, before, fixture())
    assert result["all_readback_candidates"] is True
    assert result["sites"]["1"]["incident_id"] == "current"
    assert result["operator_clearance_performed"] is False

    stale = review(journal, before, fixture(mode="third_party_control",
                                            tick="2026-10-17 08:15:00",
                                            mode_updated="2026-10-16T03:14:06+00:00"))
    assert stale["all_readback_candidates"] is False
    assert stale["sites"]["1"]["checks"]["device_time_advanced"] is False
    assert stale["sites"]["1"]["checks"]["mode_updated_after_incident"] is False
