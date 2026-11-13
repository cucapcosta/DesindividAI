"""
Motor de Simulação — lógica financeira pura (sem LLM, só biblioteca padrão).

Roda localmente ou empacotado como AI Service no watsonx.ai (ver ai_service.py).
Entrada e saída são dicts simples no formato do contrato OpenAPI
(Debt -> SimulationScenario), para que o mesmo código sirva aos dois modos.

Cenários (PLANO.md, Sub-Tarefa 3):
    aggressive      orçamento inteiro: mínimos + todo o excedente na dívida de maior CET
    consolidation   porta as dívidas com CET acima da linha BB para um único
                    financiamento BB; excedente vai para a dívida de maior CET
    minimum_viable  só pagamentos mínimos + excedente apenas na dívida mais cara
                    (quando ela quita, o excedente deixa de ser usado)

Premissas: juros mensais = (1 + CET a.a.)^(1/12) - 1; juros incidem antes do
pagamento do mês; sem tarifas de portabilidade; a taxa da linha BB é uma
premissa configurável (BB_CONSOLIDATION_CET_ANNUAL), não uma oferta real.

Dívidas que não amortizam no mínimo (pagamento ausente ou abaixo dos juros do
mês — ex.: dívida só do Serasa, rotativo, cheque especial): o motor assume um
pagamento fixo de juros iniciais + ASSUMED_MIN_PRINCIPAL_PCT do saldo e declara
a premissa na descrição de cada cenário. Prazo ausente (remaining_installments
<= 0) vira DEFAULT_TERM_MONTHS. Se mesmo assim o saldo total passar de
DIVERGENCE_FACTOR vezes o inicial (orçamento menor que os juros), a simulação
para e o cenário é marcado como "não quita" — os valores nunca explodem.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Optional

MAX_MONTHS = 600
DEFAULT_BB_CET_ANNUAL = 30.0
EPS = 0.005
# Premissas para dívidas sem mínimo utilizável (ver docstring do módulo).
ASSUMED_MIN_PRINCIPAL_PCT = 0.01
DEFAULT_TERM_MONTHS = 36
DIVERGENCE_FACTOR = 10.0
CONSOLIDATED_DEBT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "desendividai:bb-consolidation"))


def format_brl(value: float) -> str:
    """Formata em reais no padrão brasileiro: R$ 1.234,56 (negativo: -R$ 1.234,56)."""
    cents = int((abs(Decimal(str(value))) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    negative = value < 0 and cents > 0
    brl = f"R$ {cents // 100:,}".replace(",", ".") + f",{cents % 100:02d}"
    return f"-{brl}" if negative else brl


def monthly_rate(cet_annual: float) -> float:
    return (1 + cet_annual / 100) ** (1 / 12) - 1


@dataclass
class _Loan:
    id: str
    balance: float
    cet: float
    min_payment: float
    installments: int
    min_assumed: bool = False  # mínimo estimado pelo motor, não informado no snapshot

    @property
    def rate(self) -> float:
        return monthly_rate(self.cet)


Allocator = Callable[[list[_Loan]], dict[str, float]]


def _parse_loan(d: dict) -> _Loan:
    """Converte uma dívida do snapshot; dado ausente ou inválido vira ValueError (HTTP 400), nunca 500."""
    try:
        debt_id = str(d["debt_id"])
        balance = float(d["outstanding_balance"])
        cet = float(d["cet_annual"])
        payment = float(d.get("monthly_payment") or 0.0)
        term = int(d.get("remaining_installments") or 0)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        ident = d.get("debt_id", "?") if isinstance(d, dict) else "?"
        raise ValueError(f"Dívida inválida no snapshot ({ident}): campo ausente ou não numérico ({exc}).") from exc
    if not all(math.isfinite(v) for v in (balance, cet, payment)) or balance < 0 or cet < 0:
        raise ValueError(f"Dívida inválida no snapshot ({debt_id}): saldo e CET devem ser finitos e não negativos.")

    installments = min(MAX_MONTHS, term) if term > 0 else DEFAULT_TERM_MONTHS
    payment = max(payment, 0.0)
    rate = monthly_rate(cet)
    assumed = balance > EPS and payment < balance * rate + EPS
    if assumed:
        payment = balance * (rate + ASSUMED_MIN_PRINCIPAL_PCT)
    return _Loan(debt_id, balance, cet, payment, installments, min_assumed=assumed)


def _loans(debts: list[dict]) -> list[_Loan]:
    return [l for l in (_parse_loan(d) for d in debts) if l.balance > EPS]


def minimum_payments_total(debts: list[dict]) -> float:
    """Soma dos pagamentos mínimos de todas as dívidas com saldo > 0 (inclui mínimos estimados pelo motor)."""
    return sum(l.min_payment for l in _loans(debts))


def _run(loans: list[_Loan], allocate: Allocator, max_months: int = MAX_MONTHS):
    """Simula mês a mês. Retorna (plano, juros totais, quitou?)."""
    plan: list[dict[str, float]] = []
    interest = 0.0
    divergence_limit = sum(l.balance for l in loans) * DIVERGENCE_FACTOR
    for _ in range(max_months):
        active = [l for l in loans if l.balance > EPS]
        if not active:
            break
        for l in active:
            accrued = l.balance * l.rate
            l.balance += accrued
            interest += accrued
        payments = allocate(active)
        for l in active:
            l.balance = max(l.balance - payments.get(l.id, 0.0), 0.0)
        plan.append(payments)
        if sum(l.balance for l in loans) > divergence_limit:
            break  # orçamento não cobre os juros: parar em vez de acumular valores absurdos
    return plan, interest, all(l.balance <= EPS for l in loans)


def _allocator(budget: float, extra_target_ids: Callable[[list[_Loan]], list[str]]) -> Allocator:
    """Mínimos primeiro (maior CET antes, se o orçamento não cobrir tudo); excedente nos alvos."""

    def allocate(active: list[_Loan]) -> dict[str, float]:
        ordered = sorted(active, key=lambda l: -l.cet)
        pay = {l.id: 0.0 for l in active}
        remaining = budget
        for l in ordered:
            p = max(min(l.min_payment, l.balance, remaining), 0.0)
            pay[l.id] = p
            remaining -= p
        targets = set(extra_target_ids(ordered))
        for l in ordered:
            if l.id in targets and remaining > EPS:
                p = min(l.balance - pay[l.id], remaining)
                if p > 0:
                    pay[l.id] += p
                    remaining -= p
        return pay

    return allocate


def _minimum_only(budget: float) -> Allocator:
    """Só os mínimos, dentro do orçamento (maior CET primeiro se ele não cobrir tudo)."""
    return _allocator(budget, lambda ordered: [])


def _budget_check(loans: list[_Loan], budget: float) -> tuple[float, int]:
    """(soma dos mínimos do mês 1, nº de dívidas que ficam sem o mínimo) para os empréstimos do cenário."""
    month1 = _minimum_only(budget)(loans)
    min_total = sum(l.min_payment for l in loans)
    n_short = sum(1 for l in loans if month1.get(l.id, 0.0) < min(l.min_payment, l.balance) - EPS)
    return min_total, n_short


def _budget_warning(budget: float, min_total: float, n_short: int) -> str:
    return (
        f" Atenção: o orçamento ({format_brl(budget)}) é menor que a soma dos pagamentos mínimos"
        f" ({format_brl(min_total)}); {n_short} dívida(s) ficariam sem o pagamento mínimo no mês 1,"
        f" o que significa atraso e encargos. Considere renegociar."
    )


def _consolidate(loans: list[_Loan], bb_cet: float) -> tuple[list[_Loan], list[_Loan]]:
    """Substitui as dívidas com CET > linha BB por um único financiamento (PMT price)."""
    ported = [l for l in loans if l.cet > bb_cet]
    rest = [l for l in loans if l.cet <= bb_cet]
    if not ported:
        return loans, []
    principal = sum(l.balance for l in ported)
    n = max(l.installments for l in ported)
    r = monthly_rate(bb_cet)
    pmt = principal / n if r == 0 else principal * r / (1 - (1 + r) ** -n)
    merged = _Loan(CONSOLIDATED_DEBT_ID, principal, bb_cet, pmt, n)
    return [merged] + rest, ported


def _scenario(
    scenario_id: str,
    label: str,
    description: str,
    run,
    baseline_interest: float,
    assumption_note: str = "",
    *,
    budget: float,
    min_total: float,
    n_short: int,
) -> dict:
    plan, interest, paid_off = run
    below = budget < min_total - EPS
    if below:
        description += _budget_warning(budget, min_total, n_short)
    if not paid_off:
        if len(plan) >= MAX_MONTHS:
            description += f" Atenção: não quita em {MAX_MONTHS // 12} anos com esse orçamento."
        else:
            description += (
                f" Atenção: não quita — o saldo cresce mais rápido do que o orçamento paga; "
                f"a simulação foi interrompida no mês {len(plan)}."
            )
    if assumption_note:
        description += " " + assumption_note
    return {
        "scenario_id": scenario_id,
        "label": label,
        "description": description,
        "total_months": len(plan),
        "total_interest_paid": round(interest, 2),
        "total_savings_vs_minimum": round(baseline_interest - interest, 2),
        "paid_off": paid_off,
        "minimum_payments_total": round(min_total, 2),
        "budget_below_minimums": below,
        "monthly_plan": [
            {
                "month": i,
                "payments": [
                    {"debt_id": debt_id, "amount": round(amount, 2)}
                    for debt_id, amount in payments.items()
                    if amount >= 0.01
                ],
            }
            for i, payments in enumerate(plan, start=1)
        ],
    }


def simulate(debts: list[dict], monthly_budget: float, bb_cet_annual: Optional[float] = None) -> list[dict]:
    """Retorna os 3 cenários, sempre na ordem aggressive, consolidation, minimum_viable."""
    if not math.isfinite(monthly_budget) or monthly_budget <= 0:
        raise ValueError("monthly_budget deve ser maior que zero.")
    open_loans = _loans(debts)
    if not open_loans:
        raise ValueError("Nenhuma dívida com saldo em aberto.")
    bb_cet = DEFAULT_BB_CET_ANNUAL if bb_cet_annual is None else bb_cet_annual
    assumed = [l for l in open_loans if l.min_assumed]
    note = (
        f"Premissa: {len(assumed)} dívida(s) sem pagamento mínimo que cubra os juros foram simuladas "
        f"com mínimo fixo de juros iniciais + {ASSUMED_MIN_PRINCIPAL_PCT:.0%} do saldo."
        if assumed else ""
    )

    # Mínimos do mês 1 por cenário (a consolidação troca as dívidas portadas por uma parcela única,
    # então a soma dos mínimos dela difere da soma das dívidas originais).
    checks = {
        "aggressive": _budget_check(_loans(debts), monthly_budget),
        "minimum_viable": _budget_check(_loans(debts), monthly_budget),
    }

    # 1) Agressivo
    aggressive = _run(_loans(debts), _allocator(monthly_budget, lambda ordered: [l.id for l in ordered]))

    # 2) Consolidação
    loans, ported = _consolidate(_loans(debts), bb_cet)
    checks["consolidation"] = _budget_check(loans, monthly_budget)
    if ported:
        merged = loans[0]
        c_desc = (
            f"Porta {len(ported)} dívida(s) com CET acima de {bb_cet:g}% a.a. para uma linha BB "
            f"a {bb_cet:g}% a.a. em {merged.installments} parcelas (parcela inicial "
            f"{format_brl(merged.min_payment)}); o excedente do orçamento antecipa a dívida de maior CET. "
            "Taxa da linha BB é premissa, sujeita à aprovação de crédito."
        )
    else:
        c_desc = (
            f"Nenhuma dívida tem CET acima da linha BB ({bb_cet:g}% a.a.); a portabilidade não traz "
            "ganho, então o plano equivale ao agressivo."
        )
    consolidation = _run(loans, _allocator(monthly_budget, lambda ordered: [l.id for l in ordered]))

    # 3) Mínimo viável
    min_loans = _loans(debts)
    costliest = max(min_loans, key=lambda l: l.cet).id
    minimum = _run(min_loans, _allocator(monthly_budget, lambda ordered: [costliest]))

    runs = [aggressive, consolidation, minimum]
    # Baseline: só os mínimos contratados, limitados ao orçamento (o cliente não paga mais do que tem),
    # no mesmo horizonte para todos os cenários (o maior entre o prazo contratado e o do cenário
    # mais longo). Dívidas cujo mínimo não cobre os juros (ex.: cartão rotativo) nunca quitam; o
    # horizonte limita o baseline. Com orçamento abaixo dos mínimos, o baseline é o mesmo plano
    # sem excedente, e a economia deixa de sair negativa por comparar com um pagamento impossível.
    horizon = max([len(r[0]) for r in runs] + [l.installments for l in _loans(debts)] + [12])
    horizon = min(horizon, MAX_MONTHS)
    _, baseline_interest, _ = _run(_loans(debts), _minimum_only(monthly_budget), horizon)

    def scenario(scenario_id: str, label: str, description: str, run) -> dict:
        min_total, n_short = checks[scenario_id]
        return _scenario(
            scenario_id, label, description, run, baseline_interest, note,
            budget=monthly_budget, min_total=min_total, n_short=n_short,
        )

    return [
        scenario(
            "aggressive", "Agressivo",
            "Paga os mínimos de todas as dívidas e direciona todo o orçamento restante à dívida de maior CET.",
            aggressive,
        ),
        scenario("consolidation", "Consolidação", c_desc, consolidation),
        scenario(
            "minimum_viable", "Mínimo viável",
            "Paga apenas os mínimos e usa o excedente só para quitar a dívida mais cara.",
            minimum,
        ),
    ]
