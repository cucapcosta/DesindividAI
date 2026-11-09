"""
Endpoints internos — M4, Fase 7.

Protegidos por X-Internal-Token (hmac.compare_digest contra INTERNAL_API_TOKEN).
Nunca expostos ao IBM API Connect / Open Finance OAuth.

Endpoints:
    POST /internal/erase                   — LGPD Art. 18 VI, pedido por CPF (corpo);
                                             cobre todas as versões da chave
    POST /internal/erase/{pseudonym_id}    — LGPD Art. 18 VI, um pseudônimo
    POST /internal/subject/export          — LGPD Art. 18 II, V e VII (acesso e
                                             portabilidade), por CPF ou pseudonym_id
    POST /internal/retention/run           — job de retenção (dry_run=true por padrão)

CPF só no corpo JSON, nunca na URL. O corpo é lido manualmente para que erros de
validação nunca ecoem o valor recebido (o 422 padrão do FastAPI devolve o input).
Falhas de autenticação viram a linha JSON `internal.auth_failed` no stdout
(logger desendividai.audit), sem persistir no audit_trail.
"""

from __future__ import annotations

import hmac
import os
import re
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from src.api.models import ErrorResponse

from src.privacy.retention_scheduler import retention_lifespan

router = APIRouter(prefix="/internal", tags=["Internal"], lifespan=retention_lifespan)

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

def _route_template(request: Request) -> str:
    """Caminho da rota sem os parâmetros (ex.: /internal/erase/{pseudonym_id}).

    Nunca o caminho real: um CPF colocado na URL por engano não vai para o log.
    """
    route = request.scope.get("route")
    return getattr(route, "path", "") or "unknown"


def require_internal_token(
    request: Request,
    x_internal_token: str | None = Header(None),
) -> None:
    """
    Valida o header X-Internal-Token contra INTERNAL_API_TOKEN (lido a cada chamada).

    - Token não configurado (env vazio/ausente) → 503 INTERNAL_API_DISABLED
    - Header ausente ou valor diferente → 401 UNAUTHORIZED e linha JSON
      `internal.auth_failed` no stdout (reason, method, route; sem o token)
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
        from src.privacy.audit import emit_security_event

        emit_security_event(
            "internal.auth_failed",
            reason="missing_token" if x_internal_token is None else "invalid_token",
            method=request.method,
            route=_route_template(request),
        )
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
# Leitura do corpo com CPF / pseudonym_id (sem ecoar valores)
# ---------------------------------------------------------------------------

async def _read_json_object(request: Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _valid_cpf_digits(value) -> str | None:
    """11 dígitos com dígitos verificadores válidos, ou None. Aceita máscara."""
    from src.privacy.pii_guard import _is_valid_cpf

    if not isinstance(value, str):
        return None
    digits = re.sub(r"[.\-\s]", "", value)
    if not re.fullmatch(r"\d{11}", digits) or not _is_valid_cpf(digits):
        return None
    return digits


def _key_error_response() -> JSONResponse:
    return _json_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "PSEUDONYM_KEY_UNAVAILABLE",
        "Alguma versão da chave de pseudonimização não pôde ser lida; nada foi alterado.",
    )


# ---------------------------------------------------------------------------
# POST /internal/erase  (pedido do titular por CPF)
# ---------------------------------------------------------------------------

@router.post(
    "/erase",
    summary="Apagar dados do titular por CPF (LGPD Art. 18)",
    include_in_schema=False,
)
async def erase_by_cpf_endpoint(
    request: Request,
    _token: None = Depends(require_internal_token),
    store=Depends(get_store),
    vault=Depends(get_vault),
    audit=Depends(get_audit),
):
    """
    Corpo: {"cpf": "..."}. Calcula o pseudonym_id do CPF em todas as versões
    da chave de pseudonimização e apaga cada um (mesmo fluxo do endpoint por
    pseudonym_id). A resposta traz os pseudônimos e as contagens, nunca o CPF.
    """
    from src.privacy.audit import AuditWriteError
    from src.privacy.erasure import erase_subject_by_cpf
    from src.privacy.pseudonymization import PseudonymizationKeyError

    body = await _read_json_object(request)
    cpf = _valid_cpf_digits(body.get("cpf")) if body else None
    if cpf is None:
        return _json_error(422, "INVALID_CPF", "Envie {\"cpf\": \"...\"} com um CPF válido.")

    try:
        report = await run_in_threadpool(
            erase_subject_by_cpf, cpf, store=store, vault=vault, audit=audit
        )
    except PseudonymizationKeyError:
        return _key_error_response()
    except AuditWriteError:
        return _json_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AUDIT_UNAVAILABLE",
            "Trilha de auditoria indisponível; operação abortada. Repita o pedido "
            "(o apagamento é idempotente).",
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content=report.to_dict())


# ---------------------------------------------------------------------------
# POST /internal/subject/export  (acesso e portabilidade)
# ---------------------------------------------------------------------------

@router.post(
    "/subject/export",
    summary="Exportar dados do titular (LGPD Art. 18 II, V e VII)",
    include_in_schema=False,
)
async def export_subject_endpoint(
    request: Request,
    _token: None = Depends(require_internal_token),
    store=Depends(get_store),
    audit=Depends(get_audit),
):
    """
    Corpo: {"cpf": "..."} (todas as versões da chave) ou {"pseudonym_id": "<64 hex>"}.
    Devolve consentimentos, snapshots, decisões, metadados do cofre, histórico
    de auditoria e resumo de compartilhamentos. Nunca devolve o CPF do cofre.
    """
    from src.privacy.audit import AuditWriteError
    from src.privacy.pseudonymization import (
        PseudonymizationKeyError,
        pseudonymize_cpf_all_versions,
    )
    from src.privacy.subject_access import export_subject

    body = await _read_json_object(request) or {}
    pseudonyms: list[tuple[str, str]]
    if "cpf" in body:
        cpf = _valid_cpf_digits(body.get("cpf"))
        if cpf is None:
            return _json_error(422, "INVALID_CPF", "CPF inválido.")
        try:
            pseudonyms = pseudonymize_cpf_all_versions(cpf)
        except PseudonymizationKeyError:
            return _key_error_response()
    elif isinstance(body.get("pseudonym_id"), str) and _PSEUDONYM_ID_RE.match(body["pseudonym_id"]):
        pseudonyms = [(body["pseudonym_id"], "unknown")]
    else:
        return _json_error(
            422,
            "INVALID_SUBJECT",
            "Envie {\"cpf\": \"...\"} ou {\"pseudonym_id\": \"<64 hex>\"}.",
        )

    try:
        export = await run_in_threadpool(export_subject, pseudonyms, store=store, audit=audit)
    except AuditWriteError:
        return _json_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AUDIT_UNAVAILABLE",
            "Trilha de auditoria indisponível; exportação não realizada.",
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content=export)


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
