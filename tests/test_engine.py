import pytest

from src.agents.agent1.analysis import analyze_debt
from src.agents.agent1.domain import DebtSource, RawDebt
from src.api.models import ProductType
from src.simulation.engine import (
    CONSOLIDATED_DEBT_ID, DEFAULT_TERM_MONTHS, DIVERGENCE_FACTOR, MAX_MONTHS, minimum_payments_total, simulate,
)
from tests.conftest import DEBTS

# Dívidas cujo mínimo cobre os juros do mês (CET 200% a.a. ≈ 9,6% a.m. → juros de R$ 480 no cartão).
AMORTIZING = [{**DEBTS[0], "monthly_payment": 600.0}, DEBTS[1], DEBTS[2]]


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
    assert out[0]["total_months"] <= MAX_MONTHS
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
    out = by_id(simulate(AMORTIZING, 700))  # mínimos: 600 + 400 + 500
    month1 = out["aggressive"]["monthly_plan"][0]["payments"]
    paid = {p["debt_id"]: p["amount"] for p in month1}
    assert paid.get("aaaaaaaa-0000-0000-0000-000000000001", 0.0) == pytest.approx(600.0, abs=0.01)
    assert paid.get("aaaaaaaa-0000-0000-0000-000000000002", 0.0) == pytest.approx(100.0, abs=0.01)
    assert paid.get("aaaaaaaa-0000-0000-0000-000000000003", 0.0) == pytest.approx(0.0, abs=0.01)
    assert sum(p["amount"] for p in month1) <= 700.01


# --- Dívidas que não amortizam no mínimo (pendência do review do PR #1) -------------------------
# Snapshot no formato real do Agente 1: AnalyzedDebt.to_document() de dívidas como as que ele produz.

def _agent1_snapshot_debts() -> list[dict]:
    raw = [
        # só Serasa: monthly_payment = 0 e sem prazo
        RawDebt(DebtSource.SERASA, "banco-a", "c-serasa", ProductType.PERSONAL_LOAN,
                outstanding_balance=3000.0, is_negativado=True),
        # rotativo: pagamento da fatura zerado e remaining_installments = 0
        RawDebt(DebtSource.OPEN_FINANCE, "banco-b", "c-cartao", ProductType.CREDIT_CARD,
                outstanding_balance=4000.0, monthly_rate=0.14),
        # cheque especial: mínimo = saldo × taxa contratada (abaixo dos juros pelo CET, que inclui IOF)
        RawDebt(DebtSource.OPEN_FINANCE, "banco-c", "c-cheque", ProductType.OVERDRAFT,
                outstanding_balance=2000.0, monthly_payment=2000.0 * 0.08, monthly_rate=0.08),
    ]
    return [analyze_debt(d).to_document() for d in raw]


@pytest.mark.parametrize("budget", [1500, 600, 200])
def test_non_amortizing_snapshot_never_diverges(budget):
    debts = _agent1_snapshot_debts()
    total = sum(d["outstanding_balance"] for d in debts)
    for s in simulate(debts, budget):
        assert s["total_months"] <= MAX_MONTHS
        # juros acumulados ficam limitados: saldo interrompido em ~10x + o que foi pago no período
        bound = total * DIVERGENCE_FACTOR * 2 + budget * s["total_months"]
        assert s["total_interest_paid"] <= bound
        assert abs(s["total_savings_vs_minimum"]) <= 2 * bound
        assert "Premissa" in s["description"]


def test_non_amortizing_snapshot_settles_with_adequate_budget():
    out = by_id(simulate(_agent1_snapshot_debts(), 1500))
    assert all("não quita" not in s["description"] for s in out.values())
    assert out["aggressive"]["total_months"] < MAX_MONTHS
    assert out["minimum_viable"]["total_interest_paid"] > 0


def test_budget_below_interest_is_flagged_not_exploding():
    out = by_id(simulate(_agent1_snapshot_debts(), 200))
    for scenario_id in ("aggressive", "minimum_viable"):
        assert "não quita" in out[scenario_id]["description"]
        assert out[scenario_id]["total_months"] < MAX_MONTHS  # interrompido pela proteção
    # a linha BB (30% a.a.) é a única que consegue quitar com R$ 200/mês — e o motor mostra isso
    assert "não quita" not in out["consolidation"]["description"]


def test_no_assumption_note_when_minimum_covers_interest():
    for s in simulate(AMORTIZING, 1500):
        assert "Premissa" not in s["description"]


def test_unknown_term_uses_default_in_consolidation():
    debts = [{**DEBTS[0], "monthly_payment": 0.0, "remaining_installments": 0}, DEBTS[2]]
    consolidation = by_id(simulate(debts, 1500, bb_cet_annual=30))["consolidation"]
    assert f"em {DEFAULT_TERM_MONTHS} parcelas" in consolidation["description"]


@pytest.mark.parametrize("bad", [
    {"debt_id": "x", "cet_annual": 20.0},                                  # sem saldo
    {"debt_id": "x", "outstanding_balance": 100.0},                        # sem CET
    {"debt_id": "x", "outstanding_balance": "abc", "cet_annual": 20.0},    # não numérico
    {"debt_id": "x", "outstanding_balance": 100.0, "cet_annual": -5.0},    # CET negativo
])
def test_malformed_debt_raises_value_error(bad):
    with pytest.raises(ValueError):
        simulate([bad], 1000)


# --- Fix 1: orçamento abaixo dos mínimos --------------------------------------------------

def test_minimum_payments_total_correct():
    # AMORTIZING: mínimos são 600, 400, 500 → total = 1500
    total = minimum_payments_total(AMORTIZING)
    assert total == pytest.approx(1500.0, abs=0.01)


def test_budget_below_minimums_warns_in_all_three_scenarios():
    # AMORTIZING tem mínimos de 1500; orçamento 1000 está abaixo
    scenarios = by_id(simulate(AMORTIZING, 1000))
    for sid in ("aggressive", "consolidation", "minimum_viable"):
        desc = scenarios[sid]["description"]
        assert "Atenção: o orçamento" in desc, f"Aviso ausente no cenário {sid}"
        assert "R$ 1000.00" in desc
        assert "R$ 1500.00" in desc
        assert "renegociar" in desc


def test_budget_sufficient_no_warning():
    # Orçamento 1500 cobre exatamente os mínimos de AMORTIZING (1500); não deve avisar
    scenarios = by_id(simulate(AMORTIZING, 1500))
    for sid in ("aggressive", "consolidation", "minimum_viable"):
        assert "Atenção: o orçamento" not in scenarios[sid]["description"]


def test_budget_slightly_below_warns_with_n_short():
    # Com 1000, o motor paga: dívida 600 (maior CET) em cheio, dívida 400 parcialmente (400),
    # dívida 500 fica sem pagamento. N_short >= 1.
    scenarios = by_id(simulate(AMORTIZING, 1000))
    desc = scenarios["aggressive"]["description"]
    # N >= 1 dívida(s) sem mínimo
    assert "dívida(s) ficariam" in desc
    # Nenhum nome de instituição deve aparecer (segurança contra injeção)
    for name in ("itau", "bb", "caixa"):
        assert name not in desc.lower()
