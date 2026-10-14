from energy_explain.pv_accounting import account_rows


def row(**changes):
    value = {"site": "1", "time": "2031-01-01T00:00:00Z", "arm": "zero_flow",
             "pv_w": 3000, "load_w": 1000, "power_w": 1000, "flow": "charge",
             "sell": -0.1, "soc_before": 50, "soc_after": 60, "grid_export_kwh": 1,
             "negative_export_loss_eur": 0.1}
    value.update(changes)
    return value


def test_allocation_conserves_pv_and_labels_provenance():
    result = account_rows([row(), row(arm="self_consumption", time="2031-01-01T01:00:00Z")])[0]
    totals = result["totals"]
    assert totals["pv_generated_kwh"] == 3
    assert totals["direct_self_use_kwh"] == 1
    assert totals["pv_to_battery_kwh"] == 1
    assert totals["pv_to_grid_kwh"] == 1
    assert totals["negative_export_loss_eur"] == 0.1
    assert result["curtailment_status"] == "not_measured"
    assert result["bottlenecks"]["negative_price_export_steps"] == 1
    assert result["bottlenecks"]["charge_limit_steps"] is None


def test_export_can_include_battery_energy_without_misattribution():
    report = account_rows([row(flow="discharge", power_w=2000, pv_w=1000,
                               grid_export_kwh=2, negative_export_loss_eur=0.2)])[0]
    assert report["totals"]["pv_to_grid_kwh"] == 0
    assert report["totals"]["grid_export_kwh"] == 2


def test_missing_trace_arm_returns_no_days():
    assert account_rows([row(arm="self_consumption")]) == []


def test_mismatched_trace_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        account_rows([row(grid_export_kwh=2, negative_export_loss_eur=0.2)])
