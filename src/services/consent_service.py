"""
Regras de negócio para consentimento Open Finance (M1).

Isola pseudonimização e auditoria num único ponto de troca entre o Caminho B
(PR #3 ainda não mergeado) e o Caminho A (PR #3 em develop).

Caminho B (atual): pseudonymize_cpf de src/privacy/pseudonymization.py + logger.info.
Caminho A: await asyncio.to_thread(get_identity_vault().store, cpf) + await emit(…)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx

from src.api.models import ConsentResponse, ConsentStatus, ConsentStatusResponse
from src.core.config import ConfigurationError, consents_db, env, env_bool, require_env
from src.core.dates import parse_utc
from src.core.errors import ServiceError
from src.privacy.pseudonymization import PseudonymizationKeyError, pseudonymize_cpf
from src.storage.store import DocumentStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

VALID_SCOPES = {"debts:read", "simulation:read", "recommendation:read"}
AWAITING_EXPIRES_MINUTES = 10

# Padrão de nome de instituição: começa com letra, pode ter dígitos/hífen/underscore — máx 32 chars
_INSTITUTION_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


# ---------------------------------------------------------------------------
# Helper: data
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers: LGPD — pseudonimização e auditoria (pontos de troca A/B)
# ---------------------------------------------------------------------------

def _pseudonymize(cpf: str) -> str:
    """
    Caminho B: HMAC-SHA256 determinístico via src/privacy/pseudonymization.
    Caminho A: trocar por await asyncio.to_thread(get_identity_vault().store, cpf).
    """
    try:
        return pseudonymize_cpf(cpf)
    except PseudonymizationKeyError as exc:
        raise ServiceError(503, "NOT_CONFIGURED", "Chave de pseudonimização não configurada.") from exc


def _audit(event: str, **fields) -> None:
    """
    Caminho B: log estruturado sem CPF. Campos serializados como JSON para evitar
    injeção de linhas de log via valores fornecidos pelo usuário.
    Caminho A: trocar por await emit(event, **fields) de src/privacy/audit_bridge.
    Tratada como melhor-esforço: nunca propaga exceção nem devolve trace_id ao cliente.
    """
    logger.info("audit event=%s %s", event, json.dumps(fields, default=str))


# ---------------------------------------------------------------------------
# Validação de CPF
# ---------------------------------------------------------------------------

def _is_valid_cpf(cpf: str) -> bool:
    """
    Valida dígitos verificadores. Rejeita:
    - Valores não-ASCII (dígitos unicode de outras escritas passariam em isdigit())
    - Sequências repetidas (ex.: 11111111111)
    """
    if not cpf.isascii() or len(cpf) != 11 or not cpf.isdigit():
        return False
    if len(set(cpf)) == 1:
        return False
    # Primeiro dígito verificador
    total = sum(int(cpf[i]) * (10 - i) for i in range(9))
    d1 = 0 if (total * 10 % 11) >= 10 else (total * 10 % 11)
    # Segundo dígito verificador
    total2 = sum(int(cpf[i]) * (11 - i) for i in range(10))
    d2 = 0 if (total2 * 10 % 11) >= 10 else (total2 * 10 % 11)
    return int(cpf[9]) == d1 and int(cpf[10]) == d2


# ---------------------------------------------------------------------------
# Helpers: state OAuth
# ---------------------------------------------------------------------------

def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Helpers: redirect URL
# ---------------------------------------------------------------------------

def _build_redirect_url(consent_id: str, state: str) -> str:
    """
    Modo sandbox: aponta para o próprio callback (code=sandbox).
    Modo real: monta URL do Authorization Server com client_id e redirect_uri.
    Sem sandbox e sem OPEN_FINANCE_AUTHORIZE_URL, CLIENT_ID ou REDIRECT_URI → ServiceError 503.

    Nota: a troca code→token (PKCE) e a introspecção são responsabilidade do IBM API Connect,
    não do backend.
    """
    if env_bool("OPEN_FINANCE_AUTH_SANDBOX"):
        base = env("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        params = urllib.parse.urlencode({"state": state, "code": "sandbox"})
        return f"{base}/consent/callback?{params}"

    # Modo real — exige as três variáveis
    try:
        require_env(
            "OPEN_FINANCE_AUTHORIZE_URL",
            "OPEN_FINANCE_CLIENT_ID",
            "OPEN_FINANCE_REDIRECT_URI",
        )
    except ConfigurationError as exc:
        raise ServiceError(503, "NOT_CONFIGURED", str(exc)) from exc

    authorize_url = env("OPEN_FINANCE_AUTHORIZE_URL")
    client_id = env("OPEN_FINANCE_CLIENT_ID")
    redirect_uri = env("OPEN_FINANCE_REDIRECT_URI")
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": f"openid consent:{consent_id}",
        "state": state,
    })
    return f"{authorize_url}?{params}"


# ---------------------------------------------------------------------------
# POST /consent
# ---------------------------------------------------------------------------

async def create_consent(store: DocumentStore, body) -> ConsentResponse:
    """Valida, persiste e devolve ConsentResponse (201)."""
    # --- validação de CPF ---
    if not _is_valid_cpf(body.cpf):
        raise ServiceError(400, "INVALID_CPF", "CPF inválido.")

    # --- normalizar e validar institutions ---
    seen: set[str] = set()
    institutions: list[str] = []
    for inst in body.institutions:
        norm = inst.strip().lower()
        if not norm:
            raise ServiceError(400, "INVALID_REQUEST", "Nome de instituição não pode ser vazio.")
        if not _INSTITUTION_PATTERN.match(norm):
            raise ServiceError(400, "INVALID_REQUEST",
                               "Nome de instituição inválido: use apenas letras, dígitos, hífen e underscore (máx 32).")
        if norm not in seen:
            seen.add(norm)
            institutions.append(norm)

    # --- validar e deduplicar scopes ---
    seen_scopes: set[str] = set()
    scopes: list[str] = []
    for scope in body.scopes:
        if scope not in VALID_SCOPES:
            raise ServiceError(
                400, "INVALID_SCOPE",
                f"Escopo não reconhecido. Escopos aceitos: {', '.join(sorted(VALID_SCOPES))}.",
            )
        if scope not in seen_scopes:
            seen_scopes.add(scope)
            scopes.append(scope)

    # --- pseudonimização (CPF não sai daqui) ---
    pseudonym_id = _pseudonymize(body.cpf)

    # --- gerar IDs e datas ---
    consent_id = str(uuid4())
    state = secrets.token_urlsafe(32)
    now = _utcnow()
    expires_at = (now + timedelta(days=body.expiration_days)).isoformat()
    awaiting_expires_at = (now + timedelta(minutes=AWAITING_EXPIRES_MINUTES)).isoformat()
    created_at = now.isoformat()

    # --- montar redirect_url ---
    redirect_url = _build_redirect_url(consent_id, state)

    # --- persistir (CPF nunca entra no documento) ---
    doc = {
        "type": "consent",
        "consent_id": consent_id,
        "pseudonym_id": pseudonym_id,
        "institutions": institutions,
        "scopes": scopes,
        "status": ConsentStatus.AWAITING_AUTHORISATION.value,
        "created_at": created_at,
        "expires_at": expires_at,
        "awaiting_expires_at": awaiting_expires_at,
        "state_hash": _state_hash(state),
        "authorised_at": None,
        "rejected_at": None,
        "rejection_reason": None,
        "revoked_at": None,
        "revocation_reason": None,
        "revoked_by": None,
        "token_revocation": None,
    }
    await store.put(consents_db(), consent_id, doc)

    # --- auditoria (melhor-esforço, sem CPF) ---
    _audit(
        "consent.created",
        consent_id=consent_id,
        institutions=institutions,
        scopes=scopes,
        expires_at=expires_at,
    )

    return ConsentResponse(
        consent_id=UUID(consent_id),
        status=ConsentStatus.AWAITING_AUTHORISATION,
        redirect_url=redirect_url,
        expires_at=expires_at,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# GET /consent/callback
# ---------------------------------------------------------------------------

async def authorise_callback(
    store: DocumentStore,
    state: str | None,
    code: str | None,
    error: str | None,
) -> ConsentStatusResponse:
    """Transiciona AWAITING_AUTHORISATION → AUTHORISED ou REJECTED."""
    if not state:
        raise ServiceError(400, "INVALID_REQUEST", "Parâmetro 'state' obrigatório.")
    if not code and not error:
        raise ServiceError(400, "INVALID_REQUEST", "Parâmetro 'code' ou 'error' obrigatório.")

    # Validação de code por modo (B2)
    is_sandbox = env_bool("OPEN_FINANCE_AUTH_SANDBOX")
    if code:
        if is_sandbox and code != "sandbox":
            raise ServiceError(400, "INVALID_REQUEST",
                               "Modo sandbox: o único code aceito é 'sandbox'.")
        if not is_sandbox and code == "sandbox":
            raise ServiceError(400, "INVALID_REQUEST",
                               "code 'sandbox' não é válido em modo real.")

    expected_hash = _state_hash(state)
    docs = await store.find(consents_db(), {"state_hash": expected_hash}, limit=1)
    if not docs:
        raise ServiceError(404, "CONSENT_NOT_FOUND",
                           "Consentimento não encontrado para o state informado.")

    doc = docs[0]
    consent_id = doc["consent_id"]
    status = doc.get("status")

    if status != ConsentStatus.AWAITING_AUTHORISATION.value:
        raise ServiceError(409, "CONSENT_INVALID_STATE",
                           f"Consentimento com status {status} não pode ser autorizado.")

    now = _utcnow()
    # Verificar expiração do prazo de autorização.
    # Campo ausente ou inválido = tratar como vencido (B5 — falha fechada).
    try:
        awaiting_exp = parse_utc(doc["awaiting_expires_at"])
    except (KeyError, ValueError, TypeError):
        awaiting_exp = now - timedelta(seconds=1)  # ausente/inválido → tratar como vencido

    if now >= awaiting_exp:
        doc["status"] = ConsentStatus.REJECTED.value
        doc["rejected_at"] = now.isoformat()
        doc["rejection_reason"] = "AUTHORISATION_TIMEOUT"
        await store.put(consents_db(), consent_id, doc)
        _audit("consent.rejected", consent_id=consent_id, reason="AUTHORISATION_TIMEOUT")
        raise ServiceError(403, "CONSENT_AUTHORISATION_EXPIRED",
                           "Prazo de autorização expirado.")

    if error:
        doc["status"] = ConsentStatus.REJECTED.value
        doc["rejected_at"] = now.isoformat()
        doc["rejection_reason"] = "ACCESS_DENIED"
        await store.put(consents_db(), consent_id, doc)
        _audit("consent.rejected", consent_id=consent_id, reason="ACCESS_DENIED")
        raise ServiceError(403, "CONSENT_REJECTED", "Autorização negada pelo titular.")

    # sucesso: AUTHORISED
    doc["status"] = ConsentStatus.AUTHORISED.value
    doc["authorised_at"] = now.isoformat()
    doc.pop("state_hash", None)       # state de uso único — removido após consumo
    await store.put(consents_db(), consent_id, doc)
    _audit("consent.authorised", consent_id=consent_id)

    return _to_status_response(doc)


# ---------------------------------------------------------------------------
# GET /consent/{consent_id}
# ---------------------------------------------------------------------------

async def get_consent_status(store: DocumentStore, consent_id: str) -> ConsentStatusResponse:
    doc = await _load_consent(store, consent_id)
    return _to_status_response(doc)


# ---------------------------------------------------------------------------
# DELETE /consent/{consent_id}
# ---------------------------------------------------------------------------

async def revoke_consent(store: DocumentStore, consent_id: str) -> None:
    doc = await _load_consent(store, consent_id)
    status = doc.get("status")

    # idempotente: já revogado ou rejeitado
    if status in (ConsentStatus.REVOKED.value, ConsentStatus.REJECTED.value):
        return

    now = _utcnow()
    doc["status"] = ConsentStatus.REVOKED.value
    doc["revoked_at"] = now.isoformat()
    doc["revocation_reason"] = "CUSTOMER_REQUEST"
    doc["revoked_by"] = "customer"
    doc["token_revocation"] = None  # valor provisório; atualizado abaixo

    # Escrita 1: gravar REVOKED antes de qualquer I/O externo.
    # Isso garante que, mesmo se _revoke_tokens demorar ou falhar, o consentimento
    # já está revogado no banco — a janela de corrida com o callback é eliminada.
    await store.put(consents_db(), consent_id, doc)

    # Escrita 2: chamar a revogação de tokens, depois reler e gravar token_revocation.
    token_revocation = await _revoke_tokens(consent_id)
    doc_fresh = await store.get(consents_db(), consent_id)
    if doc_fresh is not None:
        doc_fresh["token_revocation"] = token_revocation
        await store.put(consents_db(), consent_id, doc_fresh)

    _audit(
        "consent.revoked",
        consent_id=consent_id,
        revocation_reason="CUSTOMER_REQUEST",
        revoked_by="customer",
        token_revocation=token_revocation,
    )


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

async def _load_consent(store: DocumentStore, consent_id: str) -> dict:
    """Carrega consentimento pelo _id (= consent_id por contrato)."""
    doc = await store.get(consents_db(), consent_id)
    if doc is None:
        raise ServiceError(404, "CONSENT_NOT_FOUND", "Consentimento não encontrado.")
    return doc


def _to_status_response(doc: dict) -> ConsentStatusResponse:
    """Devolve ConsentStatusResponse público (sem pseudonym_id, state_hash)."""
    return ConsentStatusResponse(
        consent_id=doc["consent_id"],
        status=ConsentStatus(doc["status"]),
        institutions=doc.get("institutions", []),
        scopes=doc.get("scopes", []),
        created_at=doc.get("created_at"),
        expires_at=doc.get("expires_at"),
        authorised_at=doc.get("authorised_at"),
        revoked_at=doc.get("revoked_at"),
    )


async def _revoke_tokens(consent_id: str) -> str:
    """
    Chama POST OAUTH_REVOKE_URL para invalidar tokens associados ao consentimento.
    Falha não impede a revogação (LGPD Art. 8º §5).
    Retorna: "ok" | "failed" | "skipped"

    Pendência: o contrato exato do endpoint de revogação do IBM API Connect
    (parâmetros, autenticação) precisa ser definido pela equipe de API. O backend
    não detém os tokens — eles ficam no API Connect. Ver docs/m1-consent-flow.md.
    """
    revoke_url = env("OAUTH_REVOKE_URL")
    if not revoke_url:
        return "skipped"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(revoke_url, json={"consent_id": consent_id})
            resp.raise_for_status()
        return "ok"
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("Revogação de token falhou para consent_id=%s: %s", consent_id, type(exc).__name__)
        return "failed"
