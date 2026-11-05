from datetime import date

import pytest

from src.agents.agent1 import cet


def test_price_loan_without_fees_equals_compounded_monthly_rate():
    # 10.000 em 12x a 2% a.m. → parcela 945,60; CET = 1,02^12 − 1 = 26,82% a.a.
    rate = cet.cet_from_installments(10_000, 945.596, 12)
    assert rate == pytest.approx(1.02 ** 12 - 1, abs=1e-5)


def test_upfront_fees_increase_cet():
    without_fees = cet.cet_from_installments(10_000, 945.596, 12)
    with_fees = cet.cet_from_installments(10_000 - 350, 945.596, 12)
    assert with_fees > without_fees


def test_actual_days_to_first_due_date_are_used():
    standard = cet.cet_from_installments(10_000, 945.596, 12)
    longer_grace = cet.cet_from_installments(
        10_000, 945.596, 12, release_date=date(2026, 1, 1), first_due_date=date(2026, 3, 1)
    )
    assert longer_grace < standard


def test_cash_flow_matches_resolution_equation():
    flows = [cet.CashFlow(30, 600), cet.CashFlow(61, 600)]
    rate = cet.cet_from_cash_flows(1_000, flows)
    pv = sum(f.amount / (1 + rate) ** (f.days_from_release / 365) for f in flows)
    assert pv == pytest.approx(1_000, abs=1e-6)


def test_revolving_rate_is_compounded_over_12_months():
    assert cet.cet_from_monthly_rate(0.10) == pytest.approx(1.1 ** 12 - 1)
    assert cet.cet_from_monthly_rate(0.10, monthly_charges_rate=0.01) == pytest.approx(1.11 ** 12 - 1)


@pytest.mark.parametrize("released, flows", [
    (0, [cet.CashFlow(30, 100)]),
    (1_000, []),
    (1_000, [cet.CashFlow(30, 500)]),
    (1_000, [cet.CashFlow(0, 1_100)]),
])
def test_invalid_inputs_raise(released, flows):
    with pytest.raises(cet.CETCalculationError):
        cet.cet_from_cash_flows(released, flows)


def test_to_percent():
    assert cet.to_percent(2.345678) == 234.57
