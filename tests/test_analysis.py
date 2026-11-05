from src.api.models import ProductType
from src.agents.agent1 import analysis
from src.agents.agent1.domain import CETMethod, DebtSource, RawDebt


def raw(**overrides) -> RawDebt:
    base = dict(
        source=DebtSource.OPEN_FINANCE, institution="bb", contract_id="C1",
        product_type=ProductType.PERSONAL_LOAN, outstanding_balance=1_000.0,
    )
    return RawDebt(**{**base, **overrides})


def test_cash_flow_method_preferred_when_schedule_available():
    debt = raw(contract_amount=10_000, upfront_charges=0, installment_amount=945.596,
               total_installments=12, cet_reported=0.5)
    analyzed = analysis.analyze_debt(debt)
    assert analyzed.cet_method == CETMethod.CASH_FLOW
    assert analyzed.cet_divergence  # informado 50% vs calculado ~26,8%


def test_fallback_chain_revolving_reported_market():
    assert analysis.compute_cet(raw(monthly_rate=0.05))[1] == CETMethod.REVOLVING
    assert analysis.compute_cet(raw(cet_reported=0.3)) == (0.3, CETMethod.REPORTED)
    rate, method = analysis.compute_cet(raw(product_type=ProductType.OVERDRAFT))
    assert method == CETMethod.MARKET_ESTIMATE
    assert rate == analysis.MARKET_CET_ESTIMATES[ProductType.OVERDRAFT]


def test_analyzed_debt_has_no_clear_contract_id_and_stable_debt_id():
    a = analysis.analyze_debt(raw(contract_id="SECRET-123", cet_reported=0.3))
    b = analysis.analyze_debt(raw(contract_id="SECRET-123", cet_reported=0.3))
    assert "SECRET-123" not in str(a.to_document())
    assert a.debt_id == b.debt_id


def test_prioritize_by_cet_desc_then_negativado_then_smaller_balance():
    debts = [
        analysis.analyze_debt(raw(contract_id="low", cet_reported=0.2)),
        analysis.analyze_debt(raw(contract_id="tie-big", cet_reported=0.9, outstanding_balance=5_000)),
        analysis.analyze_debt(raw(contract_id="tie-small", cet_reported=0.9, outstanding_balance=500)),
        analysis.analyze_debt(raw(contract_id="tie-neg", cet_reported=0.9, outstanding_balance=9_000,
                                  is_negativado=True)),
        analysis.analyze_debt(raw(contract_id="high", cet_reported=3.0)),
    ]
    ranked = analysis.prioritize(debts)
    balances = [(round(d.cet_annual, 2), d.is_negativado, d.outstanding_balance) for d in ranked]
    assert balances == [(3.0, False, 1_000), (0.9, True, 9_000), (0.9, False, 500),
                        (0.9, False, 5_000), (0.2, False, 1_000)]
    assert [d.priority_rank for d in ranked] == [1, 2, 3, 4, 5]


def test_merge_serasa_marks_known_contract_and_adds_unknown():
    of = [raw(institution="nubank", product_type=ProductType.CREDIT_CARD, outstanding_balance=4_000)]
    serasa = [
        raw(source=DebtSource.SERASA, institution="nubank", product_type=ProductType.CREDIT_CARD,
            outstanding_balance=4_100, is_negativado=True, days_overdue=40),
        raw(source=DebtSource.SERASA, institution="loja", contract_id="L1", is_negativado=True),
    ]
    merged = analysis.merge_serasa(of, serasa)
    assert len(merged) == 2
    assert merged[0].is_negativado and merged[0].days_overdue == 40
    assert merged[1].institution == "loja"


def test_market_estimates_cover_every_product_type():
    assert set(analysis.MARKET_CET_ESTIMATES) == set(ProductType)
    assert all(0 < rate < 20 for rate in analysis.MARKET_CET_ESTIMATES.values())  # fração a.a.
