"""
Endpoint de dívidas — coleta e priorização por CET.
Responsável: M2

Aciona o Agente 1 (src/agents/agent1) — ver docs/m2-agent1.md

Processamento: a requisição espera o agente por até DEBTS_SYNC_TIMEOUT_S
(padrão 25 s). Se ele ainda não terminou, responde 202 PROCESSING e a execução
continua em segundo plano; a próxima chamada com o mesmo consent_id recebe o
resultado. Um resultado não buscado expira após DEBTS_RESULT_TTL_S (padrão
60 s) — a chamada seguinte dispara uma nova coleta em vez de devolver dados
velhos. Jobs ficam em memória do processo — com vários workers, o API
Connect deve manter afinidade por consent_id.
"""

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from src.agents.agent1 import run_agent1
from src.agents.agent1.agent import AgentExecutionError
from src.agents.agent1.config import ConfigurationError
from src.agents.agent1.dates import parse_utc
from src.agents.agent1.repository import get_repository
from src.agents.agent1.sources import SourceUnavailableError
from src.api.models import DebtListResponse, ErrorResponse

logger = logging.getLogger(__name__)

REQUIRED_SCOPE = "debts:read"


@dataclass
class _Job:
    future: Future
    finished_at: Optional[float] = None   # time.monotonic() ao terminar


_executor: Optional[ThreadPoolExecutor] = None
_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _jobs_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=int(os.getenv("DEBTS_MAX_WORKERS", "4")),
                thread_name_prefix="agent1",
            )
        return _executor


@asynccontextmanager
async def _lifespan(_app):
    yield
    global _executor
    with _jobs_lock:
        executor, _executor = _executor, None
        _jobs.clear()
    if executor is not None:
        # Execuções em andamento terminam; as que ainda estão na fila são descartadas.
        executor.shutdown(wait=False, cancel_futures=True)


router = APIRouter(prefix="/debts", tags=["Debts"], lifespan=_lifespan)


def _sync_timeout() -> float:
    return float(os.getenv("DEBTS_SYNC_TIMEOUT_S", "25"))


def _result_ttl() -> float:
    return float(os.getenv("DEBTS_RESULT_TTL_S", "60"))


def _sweep_expired(now: float) -> None:
    """Descarta resultados não buscados dentro do TTL. Chamar com _jobs_lock."""
    ttl = _result_ttl()
    for consent_id, job in list(_jobs.items()):
        if job.finished_at is not None and now - job.finished_at > ttl:
            del _jobs[consent_id]


def _job_for(consent: dict, repository) -> Future:
    """Reaproveita a execução em andamento (ou recente) para o consentimento, ou inicia uma."""
    executor = _get_executor()
    with _jobs_lock:
        _sweep_expired(time.monotonic())
        job = _jobs.get(consent["consent_id"])
        if job is None:
            job = _Job(executor.submit(run_agent1, consent, repository=repository))
            _jobs[consent["consent_id"]] = job

            def finished(_future: Future, job: _Job = job) -> None:
                job.finished_at = time.monotonic()

            job.future.add_done_callback(finished)
        return job.future


def _release(consent_id: str, future: Future) -> None:
    with _jobs_lock:
        job = _jobs.get(consent_id)
        if job is not None and job.future is future:
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
    try:
        expires_at = parse_utc(consent["expires_at"])
    except (KeyError, TypeError, ValueError):
        return _error(status.HTTP_403_FORBIDDEN, "CONSENT_NOT_AUTHORISED",
                      "Consentimento sem data de expiração válida.")
    if expires_at <= datetime.now(timezone.utc):
        return _error(status.HTTP_403_FORBIDDEN, "CONSENT_EXPIRED", "Consentimento expirado.")
    return None


def to_response(snapshot: dict) -> DebtListResponse:
    # Campos extras do snapshot (cet_method, contract_ref…) são ignorados.
    return DebtListResponse.model_validate(snapshot)


@router.get(
    "",
    response_model=DebtListResponse,
    status_code=status.HTTP_200_OK,
    summary="Listar dívidas consolidadas e priorizadas por CET",
    responses={
        202: {"model": ErrorResponse, "description": "Processamento em andamento"},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse, "description": "Erro inesperado (INTERNAL_ERROR)"},
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
        return await _get_debts(str(consent_id))
    except Exception:
        # Último recurso: erro sem tratamento específico vira um ErrorResponse
        # com código, em vez de um 500 sem corpo no contrato.
        logger.exception("GET /debts falhou para o consentimento %s", consent_id)
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR",
                      "Erro inesperado ao gerar o retrato financeiro.")


async def _get_debts(consent_id: str):
    try:
        repository = get_repository()
    except ConfigurationError as exc:
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "NOT_CONFIGURED", str(exc))
    consent = await run_in_threadpool(repository.get_consent, consent_id)
    if (error := _consent_error(consent)) is not None:
        return error

    job = _job_for(consent, repository)
    # asyncio.wait não cancela o future no timeout nem propaga a exceção do
    # agente: o timeout encerra só a espera, e o erro é tratado via job.result().
    waiter = asyncio.wrap_future(job)
    # Marca a exceção como lida (evita "Future exception was never retrieved"
    # quando a espera expira e o erro chega depois).
    waiter.add_done_callback(lambda f: f.cancelled() or f.exception())
    done, _ = await asyncio.wait([waiter], timeout=_sync_timeout())
    if not done:
        return _error(status.HTTP_202_ACCEPTED, "PROCESSING",
                      "Retrato financeiro em processamento — consulte novamente em alguns segundos.")
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
