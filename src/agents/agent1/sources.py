"""
Fontes de dados do Agente 1: Open Finance Brasil (fase 2 — operações de
crédito) e Serasa.

Os clientes devolvem payloads crus; a normalização para RawDebt fica em
funções puras para ser testada com os fixtures do sandbox.

Formato Open Finance esperado: visão agregada por contrato, combinando os
endpoints da transmissora (v2):
    /loans | /financings | /unarranged-accounts-overdraft  →  /contracts/{id}
                                                           →  /scheduled-instalments
                                                           →  /payments
    /credit-cards-accounts                                 →  /bills
A agregação é feita pelo receptor Open Finance do BB; aqui só lemos o resultado.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Optional, Protocol

import httpx

from src.api.models import ProductType

from . import cet as cet_calc
from .config import open_finance_sandbox, require_env, serasa_sandbox
from .domain import DebtSource, RawDebt, SerasaReport

# productSubType Open Finance → ProductType do contrato da API
_OF_SUBTYPE_MAP: dict[str, ProductType] = {
    "CREDITO_PESSOAL_SEM_CONSIGNACAO": ProductType.PERSONAL_LOAN,
    "CREDITO_PESSOAL_COM_CONSIGNACAO": ProductType.PAYROLL_LOAN,
    "CHEQUE_ESPECIAL": ProductType.OVERDRAFT,
    "ADIANTAMENTO_A_DEPOSITANTES": ProductType.OVERDRAFT,
    "CARTAO_CREDITO_ROTATIVO": ProductType.CREDIT_CARD,
    "CARTAO_CREDITO_PARCELADO": ProductType.CREDIT_CARD,
    "AQUISICAO_BENS_VEICULOS_AUTOMOTORES": ProductType.VEHICLE_LOAN,
    "FINANCIAMENTO_HABITACIONAL_SFH": ProductType.MORTGAGE,
    "FINANCIAMENTO_HABITACIONAL_EXCETO_SFH": ProductType.MORTGAGE,
}

_SERASA_NATURE_MAP: dict[str, ProductType] = {
    "CARTAO_CREDITO": ProductType.CREDIT_CARD,
    "CHEQUE_ESPECIAL": ProductType.OVERDRAFT,
    "EMPRESTIMO": ProductType.PERSONAL_LOAN,
    "CONSIGNADO": ProductType.PAYROLL_LOAN,
    "FINANCIAMENTO_VEICULO": ProductType.VEHICLE_LOAN,
    "FINANCIAMENTO_IMOBILIARIO": ProductType.MORTGAGE,
}

_REVOLVING = {ProductType.CREDIT_CARD, ProductType.OVERDRAFT}

logger = logging.getLogger(__name__)


class SourceUnavailableError(RuntimeError):
    """Fonte externa fora do ar ou respondeu com erro."""


class InvalidSourceDataError(SourceUnavailableError):
    """
    Fonte respondeu fora do formato esperado (campo ausente, tipo inválido).
    Tratada como indisponibilidade: melhor um retrato marcado como incompleto
    do que uma dívida descartada em silêncio.
    """


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def _money(value: Any) -> Optional[float]:
    return None if value in (None, "") else float(value)


def _date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _monthly_rate(interest_rates: list[dict]) -> Optional[float]:
    """Primeira taxa pré-fixada do contrato, convertida para mensal."""
    for rate in interest_rates:
        value = _money(rate.get("preFixedRate"))
        if value is None:
            continue
        if rate.get("taxPeriodicity") == "AA":
            return cet_calc.annual_to_monthly(value)
        return value
    return None


def _map_product(table: dict[str, ProductType], code: str, source: str) -> ProductType:
    product = table.get(code)
    if product is None:
        # Sem mapeamento, a dívida cai na estimativa de crédito pessoal — pode
        # distorcer a priorização. O aviso permite incluir o código na tabela.
        logger.warning("%s: produto desconhecido %r tratado como PERSONAL_LOAN", source, code)
        return ProductType.PERSONAL_LOAN
    return product


def normalize_open_finance_contract(payload: dict) -> RawDebt:
    try:
        return _normalize_open_finance_contract(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidSourceDataError(f"Open Finance: contrato fora do formato ({exc!r})") from exc


def _normalize_open_finance_contract(payload: dict) -> RawDebt:
    product = _map_product(_OF_SUBTYPE_MAP, payload.get("productSubType", ""), "Open Finance")
    instalments = payload.get("instalments") or {}
    payments = payload.get("payments") or {}
    bill = payload.get("bill") or {}

    outstanding = _money(payments.get("contractOutstandingBalance")) or _money(bill.get("billTotalAmount")) or 0.0
    monthly_rate = _monthly_rate(payload.get("interestRates") or [])
    installment_amount = _money(instalments.get("instalmentAmount"))

    if product == ProductType.CREDIT_CARD:
        monthly_payment = _money(bill.get("billMinimumAmount")) or 0.0
    elif product == ProductType.OVERDRAFT:
        # Sem parcela: o custo mensal mínimo é o juro sobre o saldo utilizado.
        monthly_payment = outstanding * (monthly_rate or 0.0)
    else:
        monthly_payment = installment_amount or 0.0

    debt = RawDebt(
        source=DebtSource.OPEN_FINANCE,
        institution=payload["institution"],
        contract_id=payload["contractId"],
        product_type=product,
        outstanding_balance=outstanding,
        monthly_payment=monthly_payment,
        remaining_installments=int(instalments.get("dueInstalments") or 0),
        cet_reported=_money(payload.get("CET")),
        days_overdue=int(payload.get("daysOverdue") or 0),
    )

    if product in _REVOLVING:
        debt.monthly_rate = monthly_rate
    else:
        debt.contract_amount = _money(payload.get("contractAmount"))
        debt.upfront_charges = sum(_money(f.get("feeAmount")) or 0.0 for f in payload.get("contractedFees") or [])
        debt.installment_amount = installment_amount
        debt.total_installments = instalments.get("totalNumberOfInstalments")
        debt.release_date = _date(payload.get("contractDate"))
        debt.first_due_date = _date(payload.get("firstInstalmentDueDate"))
    return debt


def normalize_serasa_report(payload: dict, *, today: Optional[date] = None) -> SerasaReport:
    try:
        return _normalize_serasa_report(payload, today or date.today())
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidSourceDataError(f"Serasa: relatório fora do formato ({exc!r})") from exc


def _normalize_serasa_report(payload: dict, today: date) -> SerasaReport:
    records = []
    for item in payload.get("negative_records") or []:
        occurred = _date(item.get("occurrence_date"))
        records.append(RawDebt(
            source=DebtSource.SERASA,
            institution=item["creditor"],
            contract_id=item["contract"],
            product_type=_map_product(_SERASA_NATURE_MAP, item.get("nature", ""), "Serasa"),
            outstanding_balance=float(item["amount"]),
            is_negativado=True,
            days_overdue=(today - occurred).days if occurred else 0,
        ))
    return SerasaReport(score_band=payload.get("score_band"), negative_records=records)


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------

class OpenFinanceClient(Protocol):
    def fetch_contracts(self, consent: dict) -> list[dict]: ...


class SerasaClient(Protocol):
    def fetch_report(self, pseudonym_id: str) -> dict: ...


class SandboxOpenFinanceClient:
    """Fixtures no formato Open Finance, filtrados pelas instituições consentidas."""

    is_sandbox = True

    def fetch_contracts(self, consent: dict) -> list[dict]:
        from .sandbox_data import OPEN_FINANCE_CONTRACTS

        allowed = set(consent.get("institutions") or [])
        return [c for c in OPEN_FINANCE_CONTRACTS if c["institution"] in allowed]


class SandboxSerasaClient:
    is_sandbox = True

    def fetch_report(self, pseudonym_id: str) -> dict:
        from .sandbox_data import SERASA_REPORT

        return SERASA_REPORT


class _HttpClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _get(self, path: str) -> Any:
        try:
            response = httpx.get(
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"{self._base_url}: {exc}") from exc
        except ValueError as exc:  # corpo não-JSON
            raise InvalidSourceDataError(f"{self._base_url}: resposta não é JSON") from exc


class HttpOpenFinanceClient(_HttpClient):
    """
    Receptor Open Finance do BB. O token do consentimento (FAPI) fica com o
    receptor — o agente só referencia o consent_id.
    """

    def fetch_contracts(self, consent: dict) -> list[dict]:
        return self._get(f"/consents/{consent['consent_id']}/credit-operations")["data"]


class HttpSerasaClient(_HttpClient):
    """
    Integração Serasa via camada de re-identificação (M4): o agente envia o
    pseudonym_id e o serviço resolve o CPF dentro do perímetro do HSM.
    """

    def fetch_report(self, pseudonym_id: str) -> dict:
        return self._get(f"/reports/{pseudonym_id}")


def build_open_finance_client() -> OpenFinanceClient:
    if open_finance_sandbox():
        return SandboxOpenFinanceClient()
    require_env("OPEN_FINANCE_BASE_URL", "OPEN_FINANCE_API_KEY",
                hint="Para simular esta fonte, defina OPEN_FINANCE_SANDBOX=true.")
    return HttpOpenFinanceClient(os.environ["OPEN_FINANCE_BASE_URL"], os.environ["OPEN_FINANCE_API_KEY"])


def build_serasa_client() -> SerasaClient:
    if serasa_sandbox():
        return SandboxSerasaClient()
    require_env("SERASA_BASE_URL", "SERASA_API_KEY",
                hint="Para simular esta fonte, defina SERASA_SANDBOX=true.")
    return HttpSerasaClient(os.environ["SERASA_BASE_URL"], os.environ["SERASA_API_KEY"])
