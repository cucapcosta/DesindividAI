"""
Endpoint de dívidas — coleta e priorização por CET.
Responsável: M2

Aciona o Agente 1 (src/agents/agent1) — ver docs/m2-agent1.md

Processamento: a requisição espera o agente por até DEBTS_SYNC_TIMEOUT_S
(padrão 25 s). Se ele ainda não terminou, responde 202 PROCESSING e a execução
continua em segundo plano; a próxima chamada com o mesmo consent_id recebe o
resultado. Jobs ficam em memória do processo — com vários workers, o API
Connect deve manter afinidade por consent_id.
"""

import asyncio
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from src.agents.agent1 import run_agent1
from src.agents.agent1.agent import AgentExecutionError
from src.agents.agent1.config import ConfigurationError
from src.agents.agent1.repository import get_repository
from src.agents.agent1.sources import SourceUnavailableError
from src.api.models import Debt, DebtListResponse, ErrorResponse

router = APIRouter(prefix="/debts", tags=["Debts"])

REQUIRED_SCOPE = "debts:read"

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent1")
_jobs: dict[str, Future] = {}
_jobs_lock = threading.Lock()


def _sync_timeout() -> float:
    return float(os.getenv("DEBTS_SYNC_TIMEOUT_S", "25"))


def _job_for(consent: dict, repository) -> Future:
    """Reaproveita a execução em andamento para o consentimento, ou inicia uma."""
    with _jobs_lock:
        job = _jobs.get(consent["consent_id"])
        if job is None:
            job = _executor.submit(run_agent1, consent, repository=repository)
            _jobs[consent["consent_id"]] = job
        return job


def _release(consent_id: str, job: Future) -> None:
    with _jobs_lock:
        if _jobs.get(consent_id) is job:
            del _jobs[consent_id]


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=code, message=message).model_dump(mode="json"),
    )


def _consent_error(consent: dict | None) -> JSONResponse | None:
    if consent is None:
        return _error(status.HTTP_404_NOT_FOUND, "CONSENT_NOT_FOUND", "Consentimento não encontrado.")
    if consent.get("status") != "AUTHORISED":
        return _error(status.HTTP_403_FORBIDDEN, "CONSENT_NOT_AUTHORISED",
                      f"Consentimento com status {consent.get('status')}.")
    if REQUIRED_SCOPE not in (consent.get("scopes") or []):
        return _error(status.HTTP_403_FORBIDDEN, "INSUFFICIENT_SCOPE",
                      f"Consentimento não inclui o escopo {REQUIRED_SCOPE}.")
    if datetime.fromisoformat(consent["expires_at"]) <= datetime.now(timezone.utc):
        return _error(status.HTTP_403_FORBIDDEN, "CONSENT_EXPIRED", "Consentimento expirado.")
    return None


def to_response(snapshot: dict) -> DebtListResponse:
    return DebtListResponse(
        pseudonym_id=snapshot["pseudonym_id"],
        snapshot_id=snapshot["snapshot_id"],
        total_outstanding=snapshot["total_outstanding"],
        generated_at=snapshot["generated_at"],
        debts=[
            Debt(
                debt_id=d["debt_id"],
                institution=d["institution"],
                product_type=d["product_type"],
                outstanding_balance=d["outstanding_balance"],
                cet_annual=d["cet_annual"],
                monthly_payment=d["monthly_payment"],
                remaining_installments=d["remaining_installments"],
                priority_rank=d["priority_rank"],
            )
            for d in snapshot["debts"]
        ],
    )


@router.get(
    "",
    response_model=DebtListResponse,
    status_code=status.HTTP_200_OK,
    summary="Listar dívidas consolidadas e priorizadas por CET",
    responses={
        202: {"model": ErrorResponse, "description": "Processamento em andamento"},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse, "description": "Granite não completou a sequência de tools"},
        503: {"model": ErrorResponse, "description": "Fonte indisponível ou serviço não configurado"},
    },
)
async def get_debts(consent_id: UUID):
    """
    Aciona o Agente 1 que coleta dívidas via Open Finance + Serasa,
    calcula o CET de cada uma e retorna o retrato financeiro priorizado.
    """
    try:
        repository = get_repository()
    except ConfigurationError as exc:
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "NOT_CONFIGURED", str(exc))
    consent = await run_in_threadpool(repository.get_consent, str(consent_id))
    if (error := _consent_error(consent)) is not None:
        return error

    job = _job_for(consent, repository)
    try:
        # shield: o timeout encerra só a espera, não a execução do agente.
        await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(job)), timeout=_sync_timeout())
    except asyncio.TimeoutError:
        return _error(status.HTTP_202_ACCEPTED, "PROCESSING",
                      "Retrato financeiro em processamento — consulte novamente em alguns segundos.")
    except Exception:
        pass  # o erro do agente é tratado abaixo via job.result()
    _release(consent["consent_id"], job)

    try:
        snapshot = job.result()
    except SourceUnavailableError as exc:
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "SOURCE_UNAVAILABLE", str(exc))
    except ConfigurationError as exc:
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "NOT_CONFIGURED", str(exc))
    except AgentExecutionError as exc:
        return _error(status.HTTP_502_BAD_GATEWAY, "AGENT_FAILED", str(exc))

    return to_response(snapshot)
