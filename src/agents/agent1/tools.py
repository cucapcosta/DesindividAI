"""
As 4 tools do Agente 1.

Os dados pesados (contratos, cronogramas) NÃO trafegam pelo LLM: cada tool
grava seu resultado no RunContext da execução e devolve ao modelo apenas um
resumo compacto em JSON. Isso:
    - mantém valores financeiros fora do texto gerado (sem alucinação de números)
    - reduz tokens
    - impede que o modelo injete/edite dívidas entre uma etapa e outra

Os IDs recebidos do modelo são conferidos contra o contexto: mesmo sob prompt
injection, o agente não consegue consultar dados de outro titular.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from . import analysis
from .cet import to_percent
from .domain import AnalyzedDebt, RawDebt, SerasaReport
from .repository import Repository
from .sources import (
    OpenFinanceClient,
    SerasaClient,
    SourceUnavailableError,
    normalize_open_finance_contract,
    normalize_serasa_report,
)

SNAPSHOT_SCHEMA_VERSION = 1
# Retenção do snapshot: 90 dias após o fim do consentimento (docs/m1-consent-lifecycle.md)
SNAPSHOT_RETENTION_AFTER_CONSENT = timedelta(days=90)


class ToolInputError(ValueError):
    """Chamada inválida — a mensagem é devolvida ao LLM para que ele corrija."""


@dataclass
class RunContext:
    consent: dict
    repository: Repository
    open_finance: OpenFinanceClient
    serasa: SerasaClient
    model_id: Optional[str] = None

    open_finance_debts: Optional[list[RawDebt]] = None
    serasa_report: Optional[SerasaReport] = None
    serasa_available: bool = True
    analyzed_debts: Optional[list[AnalyzedDebt]] = None
    snapshot: Optional[dict] = None
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def consent_id(self) -> str:
        return self.consent["consent_id"]

    @property
    def pseudonym_id(self) -> str:
        return self.consent["pseudonym_id"]


def _traced(name: str, ctx: RunContext, fn: Callable[[], dict]) -> dict:
    started = time.perf_counter()
    entry: dict[str, Any] = {"tool": name, "at": datetime.now(timezone.utc).isoformat()}
    try:
        result = fn()
        entry["ok"] = True
        return result
    except Exception as exc:
        entry["ok"] = False
        entry["error"] = type(exc).__name__
        raise
    finally:
        entry["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        ctx.tool_calls.append(entry)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def get_open_finance_debts(ctx: RunContext, consent_id: str) -> dict:
    def run() -> dict:
        if consent_id != ctx.consent_id:
            raise ToolInputError("consent_id não corresponde ao consentimento desta sessão")
        payloads = ctx.open_finance.fetch_contracts(ctx.consent)
        ctx.open_finance_debts = [normalize_open_finance_contract(p) for p in payloads]
        return {
            "contracts_found": len(ctx.open_finance_debts),
            "institutions": sorted({d.institution for d in ctx.open_finance_debts}),
            "products": sorted({d.product_type.value for d in ctx.open_finance_debts}),
        }

    return _traced("get_open_finance_debts", ctx, run)


def get_serasa_data(ctx: RunContext, pseudonym_id: str) -> dict:
    def run() -> dict:
        if pseudonym_id != ctx.pseudonym_id:
            raise ToolInputError("pseudonym_id não corresponde ao titular desta sessão")
        try:
            ctx.serasa_report = normalize_serasa_report(ctx.serasa.fetch_report(pseudonym_id))
        except SourceUnavailableError:
            # Serasa complementa o Open Finance; sem ele o retrato fica
            # incompleto, mas ainda é útil. O snapshot registra a lacuna.
            ctx.serasa_available = False
            ctx.serasa_report = SerasaReport()
            return {"available": False}
        return {
            "available": True,
            "score_band": ctx.serasa_report.score_band,
            "negative_records": len(ctx.serasa_report.negative_records),
        }

    return _traced("get_serasa_data", ctx, run)


def calculate_cet(ctx: RunContext) -> dict:
    def run() -> dict:
        if ctx.open_finance_debts is None:
            raise ToolInputError("chame get_open_finance_debts antes de calculate_cet")
        serasa_records = ctx.serasa_report.negative_records if ctx.serasa_report else []
        merged = analysis.merge_serasa(ctx.open_finance_debts, serasa_records)
        ctx.analyzed_debts = analysis.prioritize(analysis.analyze_debt(d) for d in merged)
        return {
            "debts": [
                {
                    "rank": d.priority_rank,
                    "institution": d.institution,
                    "product_type": d.product_type.value,
                    "cet_annual_pct": to_percent(d.cet_annual),
                    "cet_method": d.cet_method.value,
                    "negativado": d.is_negativado,
                }
                for d in ctx.analyzed_debts
            ],
            "serasa_included": ctx.serasa_report is not None,
        }

    return _traced("calculate_cet", ctx, run)


def persist_snapshot(ctx: RunContext) -> dict:
    def run() -> dict:
        if ctx.analyzed_debts is None:
            raise ToolInputError("chame calculate_cet antes de persist_snapshot")
        if ctx.snapshot is not None:
            return {"snapshot_id": ctx.snapshot["snapshot_id"], "already_persisted": True}

        ctx.snapshot = build_snapshot(ctx)
        ctx.repository.save_snapshot(ctx.snapshot)
        return {
            "snapshot_id": ctx.snapshot["snapshot_id"],
            "debts_count": ctx.snapshot["debts_count"],
            "total_outstanding": ctx.snapshot["total_outstanding"],
        }

    return _traced("persist_snapshot", ctx, run)


def build_snapshot(ctx: RunContext) -> dict:
    debts = ctx.analyzed_debts or []
    now = datetime.now(timezone.utc)
    consent_expires = datetime.fromisoformat(ctx.consent["expires_at"])
    return {
        "type": "financial_snapshot",
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": str(uuid.uuid4()),
        "pseudonym_id": ctx.pseudonym_id,
        "consent_id": ctx.consent_id,
        "generated_at": now.isoformat(),
        "expires_at": (consent_expires + SNAPSHOT_RETENTION_AFTER_CONSENT).isoformat(),
        "total_outstanding": round(sum(d.outstanding_balance for d in debts), 2),
        "total_monthly_payment": round(sum(d.monthly_payment for d in debts), 2),
        "weighted_avg_cet": to_percent(analysis.weighted_average_cet(debts)),
        "debts_count": len(debts),
        "credit_score_band": ctx.serasa_report.score_band if ctx.serasa_report else None,
        "sources": {
            "open_finance": {
                "institutions": sorted({d.institution for d in ctx.open_finance_debts or []}),
                "sandbox": getattr(ctx.open_finance, "is_sandbox", False),
            },
            "serasa": {
                "available": ctx.serasa_report is not None and ctx.serasa_available,
                "sandbox": getattr(ctx.serasa, "is_sandbox", False),
            },
        },
        "debts": [d.to_document() for d in debts],
        "agent": {
            "name": "agent1-analysis-prioritization",
            "model_id": ctx.model_id,
            "tool_calls": list(ctx.tool_calls),
        },
    }


# ---------------------------------------------------------------------------
# Adaptador LangChain
# ---------------------------------------------------------------------------

TOOL_DESCRIPTIONS = {
    "get_open_finance_debts": (
        "Coleta os contratos de crédito do cliente nas instituições autorizadas via "
        "Open Finance Brasil. Recebe o consent_id da sessão. Deve ser a primeira tool chamada."
    ),
    "get_serasa_data": (
        "Consulta restrições (negativações) e faixa de score do cliente no Serasa. "
        "Recebe o pseudonym_id da sessão. Chamar depois de get_open_finance_debts."
    ),
    "calculate_cet": (
        "Consolida as dívidas coletadas, calcula o CET anual de cada uma "
        "(BCB Res. 3.517/2007) e ordena por prioridade de quitação. Não recebe argumentos."
    ),
    "persist_snapshot": (
        "Pseudonimiza e grava o retrato financeiro priorizado. Retorna o snapshot_id. "
        "Deve ser a última tool chamada. Não recebe argumentos."
    ),
}


def build_langchain_tools(ctx: RunContext) -> list:
    from langchain_core.tools import StructuredTool, ToolException

    def invoke(fn: Callable[..., dict], **kwargs: Any) -> dict:
        try:
            return fn(ctx, **kwargs)
        except ToolInputError as exc:
            raise ToolException(str(exc)) from exc

    # Assinaturas explícitas para o LangChain inferir o schema de cada tool.
    def _get_open_finance_debts(consent_id: str) -> dict:
        return invoke(get_open_finance_debts, consent_id=consent_id)

    def _get_serasa_data(pseudonym_id: str) -> dict:
        return invoke(get_serasa_data, pseudonym_id=pseudonym_id)

    def _calculate_cet() -> dict:
        return invoke(calculate_cet)

    def _persist_snapshot() -> dict:
        return invoke(persist_snapshot)

    functions = {
        "get_open_finance_debts": _get_open_finance_debts,
        "get_serasa_data": _get_serasa_data,
        "calculate_cet": _calculate_cet,
        "persist_snapshot": _persist_snapshot,
    }
    return [
        StructuredTool.from_function(
            func=fn, name=name, description=TOOL_DESCRIPTIONS[name], handle_tool_error=True
        )
        for name, fn in functions.items()
    ]
