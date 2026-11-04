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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional

MAX_MONTHS = 600
DEFAULT_BB_CET_ANNUAL = 30.0
EPS = 0.005
CONSOLIDATED_DEBT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "desendividai:bb-consolidation"))


def monthly_rate(cet_annual: float) -> float:
    return (1 + cet_annual / 100) ** (1 / 12) - 1


@dataclass
class _Loan:
    id: str
    balance: float
    cet: float
    min_payment: float
    installments: int

    @property
    def rate(self) -> float:
        return monthly_rate(self.cet)


Allocator = Callable[[list[_Loan]], dict[str, float]]


def _loans(debts: list[dict]) -> list[_Loan]:
    return [
        _Loan(
            id=str(d["debt_id"]),
            balance=float(d["outstanding_balance"]),
            cet=float(d["cet_annual"]),
            min_payment=float(d["monthly_payment"]),
            installments=int(d.get("remaining_installments") or 1),
        )
        for d in debts
        if float(d["outstanding_balance"]) > EPS
    ]


def _run(loans: list[_Loan], allocate: Allocator, max_months: int = MAX_MONTHS):
    """Simula mês a mês. Retorna (plano, juros totais, quitou?)."""
    plan: list[dict[str, float]] = []
    interest = 0.0
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


def _minimum_only(active: list[_Loan]) -> dict[str, float]:
    return {l.id: min(l.min_payment, l.balance) for l in active}


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


def _scenario(scenario_id: str, label: str, description: str, run, baseline_interest: float) -> dict:
    plan, interest, paid_off = run
    if not paid_off:
        description += f" Atenção: não quita em {MAX_MONTHS // 12} anos com esse orçamento."
    return {
        "scenario_id": scenario_id,
        "label": label,
        "description": description,
        "total_months": len(plan),
        "total_interest_paid": round(interest, 2),
        "total_savings_vs_minimum": round(baseline_interest - interest, 2),
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
    if monthly_budget <= 0:
        raise ValueError("monthly_budget deve ser maior que zero.")
    if not any(float(d["outstanding_balance"]) > EPS for d in debts):
        raise ValueError("Nenhuma dívida com saldo em aberto.")
    bb_cet = DEFAULT_BB_CET_ANNUAL if bb_cet_annual is None else bb_cet_annual

    # 1) Agressivo
    aggressive = _run(_loans(debts), _allocator(monthly_budget, lambda ordered: [l.id for l in ordered]))

    # 2) Consolidação
    loans, ported = _consolidate(_loans(debts), bb_cet)
    if ported:
        merged = loans[0]
        c_desc = (
            f"Porta {len(ported)} dívida(s) com CET acima de {bb_cet:g}% a.a. para uma linha BB "
            f"a {bb_cet:g}% a.a. em {merged.installments} parcelas (parcela inicial "
            f"R$ {merged.min_payment:.2f}); o excedente do orçamento antecipa a dívida de maior CET. "
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
    # Baseline: só os mínimos contratados, no mesmo horizonte para todos os cenários
    # (o maior entre o prazo contratado e o do cenário mais longo). Dívidas cujo mínimo não cobre
    # os juros (ex.: cartão rotativo) nunca quitam; o horizonte limita o baseline.
    horizon = max([len(r[0]) for r in runs] + [l.installments for l in _loans(debts)] + [12])
    _, baseline_interest, _ = _run(_loans(debts), _minimum_only, horizon)

    return [
        _scenario(
            "aggressive", "Agressivo",
            "Paga os mínimos de todas as dívidas e direciona todo o orçamento restante à dívida de maior CET.",
            aggressive, baseline_interest,
        ),
        _scenario("consolidation", "Consolidação", c_desc, consolidation, baseline_interest),
        _scenario(
            "minimum_viable", "Mínimo viável",
            "Paga apenas os mínimos e usa o excedente só para quitar a dívida mais cara.",
            minimum, baseline_interest,
        ),
    ]
