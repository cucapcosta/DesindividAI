"""
Cálculo do CET (Custo Efetivo Total) — BCB Resolução nº 3.517/2007.

A resolução define o CET como a taxa anual que iguala o valor liberado ao
cliente ao valor presente de todos os pagamentos do contrato:

    FC0 = Σ  FCj / (1 + CET) ^ ((dj - d0) / 365)
         j=1..N

    FC0  = valor liberado ao cliente na data d0
           (valor do contrato − tarifas/IOF/seguros cobrados na contratação)
    FCj  = valor de cada pagamento (parcela + encargos) na data dj
    dj   = data de vencimento do pagamento j
    d0   = data de liberação do crédito

Não existe solução fechada — a taxa é encontrada numericamente (bisseção,
pois o VP é monotonicamente decrescente em CET).

Todas as funções trabalham com taxas em fração decimal (0.25 = 25% a.a.).
A conversão para % a.a. acontece apenas na borda da API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

DAYS_PER_YEAR = 365
# Período médio de um mês em dias, usado quando o cronograma real de datas
# não está disponível. Com base 365 isso equivale exatamente a capitalização
# mensal: (1 + i_mensal) ^ 12 = 1 + CET.
AVG_DAYS_PER_MONTH = DAYS_PER_YEAR / 12

# IOF pessoa física (Decreto 6.306/2007): 0,0082% ao dia. A alíquota fixa de
# 0,38% incide uma vez na contratação — para parcelados ela já vem em
# contractedFees; no rotativo entra só o componente diário (× 30 dias).
IOF_DAILY_RATE = 0.000082
MONTHLY_IOF_RATE = IOF_DAILY_RATE * 30

# Bisseção no intervalo [0, _MAX_RATE]: CET negativo é rejeitado antes
# (soma dos pagamentos < valor liberado).
_MAX_RATE = 1_000.0  # 100.000% a.a. — teto de segurança para o rotativo
_TOLERANCE = 1e-10
_MAX_ITERATIONS = 300


class CETCalculationError(ValueError):
    """Dados insuficientes ou inconsistentes para calcular o CET."""


@dataclass(frozen=True)
class CashFlow:
    days_from_release: float
    amount: float


def _present_value(flows: Sequence[CashFlow], annual_rate: float) -> float:
    return sum(
        f.amount / (1 + annual_rate) ** (f.days_from_release / DAYS_PER_YEAR)
        for f in flows
    )


def cet_from_cash_flows(released_amount: float, flows: Sequence[CashFlow]) -> float:
    """
    Resolve a equação da Res. 3.517/2007 por bisseção.

    Retorna o CET anual em fração decimal.
    """
    if released_amount <= 0:
        raise CETCalculationError("valor liberado deve ser positivo")
    if not flows:
        raise CETCalculationError("cronograma de pagamentos vazio")
    if any(f.days_from_release <= 0 or f.amount < 0 for f in flows):
        raise CETCalculationError("pagamentos devem ter data posterior à liberação e valor não-negativo")

    total_paid = sum(f.amount for f in flows)
    if total_paid < released_amount:
        # Pagar menos do que foi liberado implicaria CET negativo — indica
        # dados truncados (ex.: cronograma parcial), não um contrato real.
        raise CETCalculationError("soma dos pagamentos menor que o valor liberado")

    def excess_pv(rate: float) -> float:
        return _present_value(flows, rate) - released_amount

    low, high = 0.0, _MAX_RATE
    if excess_pv(high) > 0:
        raise CETCalculationError("CET acima do teto calculável")

    for _ in range(_MAX_ITERATIONS):
        mid = (low + high) / 2
        if excess_pv(mid) > 0:
            low = mid
        else:
            high = mid
        if high - low < _TOLERANCE:
            break
    return (low + high) / 2


def cet_from_installments(
    released_amount: float,
    installment_amount: float,
    installments: int,
    *,
    release_date: date | None = None,
    first_due_date: date | None = None,
) -> float:
    """
    CET de um contrato parcelado com parcelas mensais iguais (Price).

    Se as datas forem informadas, usa a contagem real de dias até o primeiro
    vencimento e meses médios a partir daí; senão assume 1 mês médio por parcela.
    """
    if installments <= 0:
        raise CETCalculationError("número de parcelas deve ser positivo")

    first_offset = AVG_DAYS_PER_MONTH
    if release_date and first_due_date:
        first_offset = (first_due_date - release_date).days
        if first_offset <= 0:
            raise CETCalculationError("primeiro vencimento deve ser posterior à liberação")

    flows = [
        CashFlow(first_offset + k * AVG_DAYS_PER_MONTH, installment_amount)
        for k in range(installments)
    ]
    return cet_from_cash_flows(released_amount, flows)


def cet_from_monthly_rate(monthly_rate: float, *, monthly_charges_rate: float = 0.0) -> float:
    """
    CET de crédito rotativo (cartão, cheque especial), que não tem cronograma.

    Soma juros + encargos mensais (IOF, tarifas proporcionais) e anualiza com
    capitalização composta: CET = (1 + i_total) ^ 12 − 1.
    """
    if monthly_rate < 0 or monthly_charges_rate < 0:
        raise CETCalculationError("taxas mensais não podem ser negativas")
    return (1 + monthly_rate + monthly_charges_rate) ** 12 - 1


def annual_to_monthly(annual_rate: float) -> float:
    return (1 + annual_rate) ** (1 / 12) - 1


def to_percent(rate: float) -> float:
    """Fração decimal → % com 2 casas (formato do contrato OpenAPI)."""
    return round(rate * 100, 2)
