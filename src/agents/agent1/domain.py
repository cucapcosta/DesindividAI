"""
Modelos internos do Agente 1.

RawDebt       → dívida normalizada logo após a coleta. Ainda contém
                identificadores em claro (contract_id) — nunca é persistida.
AnalyzedDebt  → dívida com CET calculado e pseudonimizada; é o que vai para
                o snapshot em financial_snapshots.

Unidades: internamente as taxas são frações (0.25 = 25% a.a.). No documento
persistido (to_document) os CETs viram % a.a., a mesma unidade da API e a que o
Motor de Simulação (Agente 2) consome.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from src.api.models import ProductType

from .cet import to_percent


class DebtSource(str, Enum):
    OPEN_FINANCE = "open_finance"
    SERASA = "serasa"


class CETMethod(str, Enum):
    CASH_FLOW = "cash_flow"          # Res. 3.517 sobre o cronograma do contrato
    REVOLVING = "revolving"          # taxa mensal + IOF anualizados
    REPORTED = "reported"            # CET informado pela instituição (Open Finance)
    MARKET_ESTIMATE = "market_estimate"  # média de mercado BCB por produto (Serasa)


@dataclass
class RawDebt:
    source: DebtSource
    institution: str
    contract_id: str
    product_type: ProductType
    outstanding_balance: float
    monthly_payment: float = 0.0
    remaining_installments: int = 0

    # Campos para CET por fluxo de caixa (contratos parcelados)
    contract_amount: Optional[float] = None
    upfront_charges: float = 0.0          # tarifas + IOF + seguros na contratação
    installment_amount: Optional[float] = None
    total_installments: Optional[int] = None
    release_date: Optional[date] = None
    first_due_date: Optional[date] = None

    # Campo para CET de crédito rotativo
    monthly_rate: Optional[float] = None

    cet_reported: Optional[float] = None  # fração a.a., quando a instituição informa

    is_negativado: bool = False           # possui restrição ativa no Serasa
    days_overdue: int = 0


@dataclass
class AnalyzedDebt:
    debt_id: str
    contract_ref: str                     # HMAC do contrato — substitui contract_id
    source: DebtSource
    institution: str
    product_type: ProductType
    outstanding_balance: float
    monthly_payment: float
    remaining_installments: int
    cet_annual: float                     # fração a.a.
    cet_method: CETMethod
    cet_reported: Optional[float] = None
    cet_divergence: bool = False
    is_negativado: bool = False
    days_overdue: int = 0
    priority_rank: int = 0

    def to_document(self) -> dict:
        doc = asdict(self)
        doc["source"] = self.source.value
        doc["product_type"] = self.product_type.value
        doc["cet_method"] = self.cet_method.value
        doc["cet_annual"] = to_percent(self.cet_annual)
        if self.cet_reported is not None:
            doc["cet_reported"] = to_percent(self.cet_reported)
        return doc


@dataclass
class SerasaReport:
    score_band: Optional[str] = None      # faixa, nunca o score exato
    negative_records: list[RawDebt] = field(default_factory=list)
    # False quando a consulta falhou: o retrato segue só com o Open Finance e
    # o snapshot registra a lacuna em sources.serasa.available.
    available: bool = True
