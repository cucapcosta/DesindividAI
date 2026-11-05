"""
Lógica determinística do Agente 1: consolidação Open Finance + Serasa,
cálculo de CET e priorização.

Nada aqui depende do LLM — o Granite decide *quando* chamar cada etapa,
mas todos os números saem destas funções, testáveis e auditáveis.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from src.api.models import ProductType
from src.privacy.pseudonymization import pseudonymize_contract

from . import cet as cet_calc
from .domain import AnalyzedDebt, CETMethod, RawDebt

logger = logging.getLogger(__name__)

# Divergência tolerada entre CET informado e calculado (pontos percentuais / 100).
CET_DIVERGENCE_THRESHOLD = 0.01

MARKET_RATES_FILE = Path(__file__).with_name("market_rates.json")


def _load_market_estimates() -> dict[ProductType, float]:
    """
    CET estimado por produto (fração a.a.) para dívidas vindas só do Serasa, que
    não trazem taxa. Fonte: taxas médias de juros PF do BCB (SGS), em
    market_rates.json — atualizar com scripts/update_market_rates.py.
    São taxas de juros, não CET: a estimativa é um piso do custo real.
    """
    data = json.loads(MARKET_RATES_FILE.read_text())
    return {
        ProductType(product): entry["annual_rate_pct"] / 100
        for product, entry in data["rates"].items()
    }


MARKET_CET_ESTIMATES: dict[ProductType, float] = _load_market_estimates()

# Namespace fixo para que o mesmo contrato gere sempre o mesmo debt_id,
# permitindo ao Agente 2 comparar snapshots.
_DEBT_ID_NAMESPACE = uuid.UUID("6f1c2a52-58a4-4d0e-9a39-7d2b1c3e9f10")


def compute_cet(debt: RawDebt) -> tuple[float, CETMethod]:
    """Escolhe o método mais preciso disponível para a dívida."""
    if debt.contract_amount and debt.installment_amount and debt.total_installments:
        try:
            rate = cet_calc.cet_from_installments(
                debt.contract_amount - debt.upfront_charges,
                debt.installment_amount,
                debt.total_installments,
                release_date=debt.release_date,
                first_due_date=debt.first_due_date,
            )
            return rate, CETMethod.CASH_FLOW
        except cet_calc.CETCalculationError as exc:
            logger.warning("CET por fluxo de caixa falhou (%s); tentando próximo método", exc)

    if debt.monthly_rate is not None:
        rate = cet_calc.cet_from_monthly_rate(
            debt.monthly_rate, monthly_charges_rate=cet_calc.MONTHLY_IOF_RATE
        )
        return rate, CETMethod.REVOLVING

    if debt.cet_reported is not None:
        return debt.cet_reported, CETMethod.REPORTED

    return MARKET_CET_ESTIMATES[debt.product_type], CETMethod.MARKET_ESTIMATE


def analyze_debt(debt: RawDebt) -> AnalyzedDebt:
    rate, method = compute_cet(debt)
    divergence = (
        method in (CETMethod.CASH_FLOW, CETMethod.REVOLVING)
        and debt.cet_reported is not None
        and abs(rate - debt.cet_reported) > CET_DIVERGENCE_THRESHOLD
    )
    contract_ref = pseudonymize_contract(debt.institution, debt.contract_id)
    return AnalyzedDebt(
        debt_id=str(uuid.uuid5(_DEBT_ID_NAMESPACE, contract_ref)),
        contract_ref=contract_ref,
        source=debt.source,
        institution=debt.institution,
        product_type=debt.product_type,
        outstanding_balance=round(debt.outstanding_balance, 2),
        monthly_payment=round(debt.monthly_payment, 2),
        remaining_installments=debt.remaining_installments,
        cet_annual=rate,
        cet_method=method,
        cet_reported=debt.cet_reported,
        cet_divergence=divergence,
        is_negativado=debt.is_negativado,
        days_overdue=debt.days_overdue,
    )


def merge_serasa(open_finance: list[RawDebt], serasa: Iterable[RawDebt]) -> list[RawDebt]:
    """
    Cruza restrições do Serasa com contratos do Open Finance (pareamento 1:1).

    Cada restrição casa com no máximo um contrato e vice-versa:
        1. mesmo contract_id e instituição (casamento exato);
        2. senão, mesma instituição e produto, saldo mais próximo, entre os
           contratos ainda não pareados.
    O contrato pareado é marcado como negativado. Restrição sem par vira uma
    nova dívida — credores fora do Open Finance (varejo, telecom) ou um segundo
    contrato do mesmo produto que o Open Finance não trouxe.

    Não altera as listas de entrada: devolve cópias.
    """
    merged = [replace(d) for d in open_finance]
    of_count = len(merged)  # restrições sem par anexadas abaixo não são candidatas
    paired: set[int] = set()
    records = list(serasa)

    def mark(index: int, record: RawDebt) -> None:
        paired.add(index)
        debt = merged[index]
        debt.is_negativado = True
        debt.days_overdue = max(debt.days_overdue, record.days_overdue)

    unmatched: list[RawDebt] = []
    for record in records:
        exact = next(
            (i for i, d in enumerate(merged)
             if i not in paired and d.institution == record.institution
             and d.contract_id == record.contract_id),
            None,
        )
        if exact is None:
            unmatched.append(record)
        else:
            mark(exact, record)

    for record in unmatched:
        candidates = [
            i for i, d in enumerate(merged[:of_count])
            if i not in paired and d.institution == record.institution
            and d.product_type == record.product_type
        ]
        if candidates:
            mark(min(candidates, key=lambda i: abs(merged[i].outstanding_balance - record.outstanding_balance)), record)
        else:
            merged.append(replace(record))
    return merged


def prioritize(debts: Iterable[AnalyzedDebt]) -> list[AnalyzedDebt]:
    """
    Ordena por CET decrescente (método avalanche: cada real pago na dívida
    mais cara é o que mais reduz o custo total).

    Desempates, em ordem:
        1. negativada primeiro — restrição ativa bloqueia acesso a crédito mais barato
        2. menor saldo primeiro — quitação mais rápida libera fluxo mensal
    """
    ranked = sorted(
        debts,
        key=lambda d: (-round(d.cet_annual, 6), not d.is_negativado, d.outstanding_balance),
    )
    for position, debt in enumerate(ranked, start=1):
        debt.priority_rank = position
    return ranked


def weighted_average_cet(debts: Iterable[AnalyzedDebt]) -> float:
    debts = list(debts)
    total = sum(d.outstanding_balance for d in debts)
    if not total:
        return 0.0
    return sum(d.cet_annual * d.outstanding_balance for d in debts) / total
