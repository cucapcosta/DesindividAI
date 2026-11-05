import pytest

from src.simulation.engine import CONSOLIDATED_DEBT_ID, MAX_MONTHS, simulate
from tests.conftest import DEBTS


def by_id(scenarios):
    return {s["scenario_id"]: s for s in scenarios}


def test_returns_three_scenarios_in_order():
    out = simulate(DEBTS, 1500)
    assert [s["scenario_id"] for s in out] == ["aggressive", "consolidation", "minimum_viable"]


def test_aggressive_beats_minimum_viable():
    s = by_id(simulate(DEBTS, 1500))
    assert s["aggressive"]["total_interest_paid"] < s["minimum_viable"]["total_interest_paid"]
    assert s["aggressive"]["total_months"] <= s["minimum_viable"]["total_months"]
    assert s["aggressive"]["total_savings_vs_minimum"] > 0


def test_budget_is_never_exceeded():
    for scenario in simulate(DEBTS, 1500):
        for month in scenario["monthly_plan"]:
            assert sum(p["amount"] for p in month["payments"]) <= 1500 + 0.05


def test_first_month_extra_goes_to_highest_cet():
    first = by_id(simulate(DEBTS, 1500))["aggressive"]["monthly_plan"][0]["payments"]
    paid = {p["debt_id"]: p["amount"] for p in first}
    assert paid["aaaaaaaa-0000-0000-0000-000000000001"] == pytest.approx(1500 - 400 - 500, abs=0.01)


def test_consolidation_ports_only_debts_above_bb_rate():
    out = by_id(simulate(DEBTS, 1500, bb_cet_annual=30))["consolidation"]
    ids = {p["debt_id"] for m in out["monthly_plan"] for p in m["payments"]}
    assert CONSOLIDATED_DEBT_ID in ids
    assert "aaaaaaaa-0000-0000-0000-000000000001" not in ids
    assert "aaaaaaaa-0000-0000-0000-000000000003" in ids  # CET 20% < 30%: não portada


def test_consolidation_without_candidates_equals_aggressive():
    s = by_id(simulate(DEBTS, 1500, bb_cet_annual=500))
    assert s["consolidation"]["total_interest_paid"] == s["aggressive"]["total_interest_paid"]
    assert "equivale ao agressivo" in s["consolidation"]["description"]


def test_unpayable_debt_flagged_and_capped():
    debts = [{**DEBTS[0], "monthly_payment": 10.0}]
    out = simulate(debts, 20)
    assert out[0]["total_months"] == MAX_MONTHS
    assert "não quita" in out[0]["description"]


def test_invalid_inputs():
    with pytest.raises(ValueError):
        simulate(DEBTS, 0)
    with pytest.raises(ValueError):
        simulate([{**DEBTS[0], "outstanding_balance": 0}], 100)


def test_absurd_installments_are_capped():
    out = simulate([{**DEBTS[0], "remaining_installments": 10**9}], 1500)
    assert all(s["total_months"] <= MAX_MONTHS for s in out)


def test_budget_below_minimums_pays_highest_cet_first():
    out = by_id(simulate(DEBTS, 400))
    month1 = out["aggressive"]["monthly_plan"][0]["payments"]
    paid = {p["debt_id"]: p["amount"] for p in month1}
    assert paid.get("aaaaaaaa-0000-0000-0000-000000000001", 0.0) == pytest.approx(300.0, abs=0.01)
    assert paid.get("aaaaaaaa-0000-0000-0000-000000000002", 0.0) == pytest.approx(100.0, abs=0.01)
    assert paid.get("aaaaaaaa-0000-0000-0000-000000000003", 0.0) == pytest.approx(0.0, abs=0.01)
    assert sum(p["amount"] for p in month1) <= 400.01
