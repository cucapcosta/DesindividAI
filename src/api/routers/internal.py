"""
Endpoints internos — M4, Fase 7.

Protegidos por X-Internal-Token (hmac.compare_digest contra INTERNAL_API_TOKEN).
Nunca expostos ao IBM API Connect / Open Finance OAuth.

Endpoints:
    POST /internal/erase/{pseudonym_id}    — LGPD Art. 18 (apagamento do titular)
    POST /internal/retention/run           — job de retenção (dry_run=true por padrão)
"""

from __future__ import annotations

import hmac
import os
import re
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse

from src.api.models import ErrorResponse

router = APIRouter(prefix="/internal", tags=["Internal"])

_PSEUDONYM_ID_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Helpers para respostas de erro (devolvem JSONResponse como exceção HTTP)
# ---------------------------------------------------------------------------

def _json_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=code, message=message).model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# Dependência de autenticação
# ---------------------------------------------------------------------------

def require_internal_token(
    x_internal_token: str | None = Header(None),
) -> None:
    """
    Valida o header X-Internal-Token contra INTERNAL_API_TOKEN (lido a cada chamada).

    - Token não configurado (env vazio/ausente) → 503 INTERNAL_API_DISABLED
    - Header ausente ou valor diferente → 401 UNAUTHORIZED
    """
    configured_token = os.getenv("INTERNAL_API_TOKEN", "")
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTERNAL_API_DISABLED", "message": "INTERNAL_API_TOKEN não configurado."},
        )
    if x_internal_token is None or not hmac.compare_digest(
        configured_token.encode("utf-8"),
        x_internal_token.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Token ausente ou inválido."},
        )


# ---------------------------------------------------------------------------
# Dependências de infraestrutura (sobrescrevíveis via app.dependency_overrides)
# ---------------------------------------------------------------------------

def get_store():
    from src.agents.agent1.config import ConfigurationError
    from src.privacy.retention_store import get_retention_store

    try:
        return get_retention_store()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "NOT_CONFIGURED", "message": str(exc)},
        )


def get_vault():
    from src.agents.agent1.config import ConfigurationError
    from src.privacy.identity_vault import get_identity_vault

    try:
        return get_identity_vault()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "NOT_CONFIGURED", "message": str(exc)},
        )


def get_audit():
    from src.agents.agent1.config import ConfigurationError
    from src.privacy.audit import get_audit_logger

    try:
        return get_audit_logger()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "NOT_CONFIGURED", "message": str(exc)},
        )


# ---------------------------------------------------------------------------
# POST /internal/erase/{pseudonym_id}
# ---------------------------------------------------------------------------

@router.post(
    "/erase/{pseudonym_id}",
    summary="Apagar dados do titular (LGPD Art. 18)",
    include_in_schema=False,
    responses={
        200: {"description": "Relatório de apagamento"},
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def erase_subject_endpoint(
    pseudonym_id: str = Path(
        ...,
        description="pseudonym_id do titular — 64 hex chars (SHA-256). CPFs são rejeitados (422).",
    ),
    _token: None = Depends(require_internal_token),
    store=Depends(get_store),
    vault=Depends(get_vault),
    audit=Depends(get_audit),
):
    """
    Apaga todos os dados do titular: revoga consentimentos, remove snapshots e
    destrói o cofre de identidade (crypto-shredding).

    Idempotente: segunda chamada retorna contagens zero.

    Se o evento inicial de auditoria não puder ser gravado (AuditWriteError),
    retorna 503 AUDIT_UNAVAILABLE e nenhum dado é apagado.
    """
    # Validação manual — sem ecoar o valor recebido na resposta de erro
    if not _PSEUDONYM_ID_RE.match(pseudonym_id):
        return _json_error(
            422,
            "INVALID_PSEUDONYM_ID",
            "pseudonym_id deve ter exatamente 64 caracteres hexadecimais.",
        )

    from src.privacy.audit import AuditWriteError
    from src.privacy.erasure import erase_subject

    try:
        report = erase_subject(
            pseudonym_id,
            store=store,
            vault=vault,
            audit=audit,
        )
    except AuditWriteError:
        return _json_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AUDIT_UNAVAILABLE",
            "Trilha de auditoria indisponível; operação abortada.",
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content=asdict(report))


# ---------------------------------------------------------------------------
# POST /internal/retention/run
# ---------------------------------------------------------------------------

@router.post(
    "/retention/run",
    summary="Executar job de retenção",
    include_in_schema=False,
    responses={
        200: {"description": "Relatório de retenção"},
        401: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def retention_run_endpoint(
    dry_run: bool = Query(
        default=True,
        description="Se true (padrão), calcula mas NÃO apaga nada (modo seguro).",
    ),
    _token: None = Depends(require_internal_token),
    store=Depends(get_store),
    vault=Depends(get_vault),
    audit=Depends(get_audit),
):
    """
    Executa o job de retenção. Por segurança, dry_run=true é o padrão.
    Passe dry_run=false para aplicar as remoções efetivamente.
    """
    from src.privacy.retention import run_retention

    report = run_retention(
        dry_run=dry_run,
        store=store,
        vault=vault,
        audit=audit.record_event,
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content=asdict(report))
