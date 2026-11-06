"""
Regras de negócio para consentimento Open Finance (M1).

O CPF entra só no POST /consent e vai direto para o cofre de identidade do M4
(cifrado, devolve o pseudonym_id). Eventos vão para a trilha de auditoria do M4.
Transições de estado usam escrita condicional por _rev: callback e DELETE
concorrentes não sobrescrevem um ao outro.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Callable
from uuid import UUID, uuid4

import httpx
import requests
from ibm_cloud_sdk_core import ApiException

from src.api.models import ConsentResponse, ConsentStatus, ConsentStatusResponse
from src.core.config import ConfigurationError, consents_db, env, env_bool, require_env
from src.core.dates import parse_utc
from src.core.errors import ServiceError
from src.privacy.audit_bridge import emit
from src.privacy.identity_vault import IdentityVault, InMemoryVaultRepository, get_identity_vault
from src.privacy.key_provider import KeyProviderError
from src.storage.store import DocumentStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

VALID_SCOPES = {"debts:read", "simulation:read", "recommendation:read"}
AWAITING_EXPIRES_MINUTES = 10
_MAX_WRITE_ATTEMPTS = 3

# Código de instituição: minúsculas, dígitos (aceita código COMPE como "001"), hífen e underscore.
_INSTITUTION_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")
# 11 dígitos (ignorando - e _) podem ser um CPF digitado no campo errado — nunca persistir.
_CPF_LIKE = re.compile(r"\d{11}")


# ---------------------------------------------------------------------------
# Helper: data
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers: LGPD — cofre de identidade e auditoria (M4)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _memory_vault() -> IdentityVault:
    return IdentityVault(repository=InMemoryVaultRepository())


def _identity_vault() -> IdentityVault:
    """Cofre do M4. Com STORE_BACKEND=memory (só desenvolvimento local), cofre em memória."""
    if env("STORE_BACKEND").lower() == "memory":
        return _memory_vault()
    return get_identity_vault()


async def _store_identity(cpf: str) -> str:
    """Cifra o CPF no cofre e devolve o pseudonym_id. O CPF não sai daqui."""
    try:
        vault = _identity_vault()
        return await asyncio.to_thread(vault.store, cpf)
    except (ConfigurationError, KeyProviderError) as exc:
        raise ServiceError(503, "NOT_CONFIGURED", "Cofre de identidade não configurado.") from exc
    except (ApiException, requests.RequestException) as exc:
        raise ServiceError(503, "SERVICE_UNAVAILABLE", "Cofre de identidade indisponível.") from exc


async def _audit(event: str, *, pseudonym_id: str, **fields) -> None:
    """Trilha de auditoria do M4. Melhor-esforço: emit nunca propaga erro. Sem CPF."""
    await emit(event, pseudonym_id=pseudonym_id, actor="consent_api", **fields)


async def _update(store: DocumentStore, consent_id: str, mutate: Callable[[dict], bool]) -> dict | None:
    """
    Relê o consentimento, aplica ``mutate`` e grava condicionado ao _rev lido; em conflito,
    relê e tenta de novo. ``mutate`` devolve False para não gravar (ou levanta ServiceError).
    Retorna o documento como foi gravado, ou None se ``mutate`` recusou. 404 se não existir.
    """
    for _ in range(_MAX_WRITE_ATTEMPTS):
        doc = await store.get(consents_db(), consent_id)
        if doc is None:
            raise ServiceError(404, "CONSENT_NOT_FOUND", "Consentimento não encontrado.")
        if not mutate(doc):
            return None
        try:
            await store.put(consents_db(), consent_id, doc, rev=doc["_rev"])
            return doc
        except ServiceError as exc:
            if exc.code != "CONFLICT":
                raise
    raise ServiceError(409, "CONSENT_INVALID_STATE",
                       "Consentimento alterado por outra operação; tente novamente.")


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
    institutions = list(dict.fromkeys(inst.strip().lower() for inst in body.institutions))
    if any(not _INSTITUTION_PATTERN.match(i) or _CPF_LIKE.search(re.sub(r"[-_]", "", i)) for i in institutions):
        raise ServiceError(400, "INVALID_REQUEST",
                           "Instituição inválida: use letras minúsculas, dígitos, hífen e underscore (máx 32).")

    # --- validar e deduplicar scopes ---
    if any(scope not in VALID_SCOPES for scope in body.scopes):
        raise ServiceError(
            400, "INVALID_SCOPE",
            f"Escopo não reconhecido. Escopos aceitos: {', '.join(sorted(VALID_SCOPES))}.",
        )
    scopes = list(dict.fromkeys(body.scopes))

    # --- IDs e redirect_url antes do cofre: erro de configuração não deixa CPF órfão ---
    consent_id = str(uuid4())
    state = secrets.token_urlsafe(32)
    redirect_url = _build_redirect_url(consent_id, state)

    # --- CPF cifrado no cofre do M4 (não sai daqui) ---
    pseudonym_id = await _store_identity(body.cpf)

    now = _utcnow()
    expires_at = (now + timedelta(days=body.expiration_days)).isoformat()
    awaiting_expires_at = (now + timedelta(minutes=AWAITING_EXPIRES_MINUTES)).isoformat()
    created_at = now.isoformat()

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
    await _audit(
        "consent.created",
        pseudonym_id=pseudonym_id,
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
    state: str,
    code: str | None,
    error: str | None,
) -> ConsentStatusResponse:
    """Transiciona AWAITING_AUTHORISATION → AUTHORISED ou REJECTED."""
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

    docs = await store.find(consents_db(), {"state_hash": _state_hash(state)}, limit=1)
    if not docs:
        raise ServiceError(404, "CONSENT_NOT_FOUND",
                           "Consentimento não encontrado para o state informado.")
    consent_id = docs[0]["consent_id"]
    now = _utcnow()
    outcome: dict = {}

    def decide(doc: dict) -> bool:
        if doc.get("status") != ConsentStatus.AWAITING_AUTHORISATION.value:
            raise ServiceError(409, "CONSENT_INVALID_STATE",
                               f"Consentimento com status {doc.get('status')} não pode ser autorizado.")
        # prazo ausente ou inválido = vencido (falha fechada)
        try:
            expired = now >= parse_utc(doc["awaiting_expires_at"])
        except (KeyError, ValueError, TypeError):
            expired = True
        if expired or error:
            outcome["reason"] = "AUTHORISATION_TIMEOUT" if expired else "ACCESS_DENIED"
            doc.update(status=ConsentStatus.REJECTED.value, rejected_at=now.isoformat(),
                       rejection_reason=outcome["reason"])
        else:
            doc.pop("state_hash", None)  # state de uso único
            doc.update(status=ConsentStatus.AUTHORISED.value, authorised_at=now.isoformat())
        return True

    doc = await _update(store, consent_id, decide)
    if outcome:
        await _audit("consent.rejected", pseudonym_id=doc["pseudonym_id"],
                     consent_id=consent_id, reason=outcome["reason"])
        if outcome["reason"] == "AUTHORISATION_TIMEOUT":
            raise ServiceError(403, "CONSENT_AUTHORISATION_EXPIRED", "Prazo de autorização expirado.")
        raise ServiceError(403, "CONSENT_REJECTED", "Autorização negada pelo titular.")

    await _audit("consent.authorised", pseudonym_id=doc["pseudonym_id"], consent_id=consent_id)
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
    """
    AUTHORISED → REVOKED. AWAITING_AUTHORISATION (ainda sem tokens) → REJECTED com
    CUSTOMER_CANCELLED, para a retenção do M4 limpar em minutos e não em 90 dias.
    Já REVOKED ou REJECTED: idempotente (204 sem nova escrita).
    """
    now = _utcnow().isoformat()

    def revoke(doc: dict) -> bool:
        status = doc.get("status")
        if status == ConsentStatus.AWAITING_AUTHORISATION.value:
            doc.update(status=ConsentStatus.REJECTED.value, rejected_at=now,
                       rejection_reason="CUSTOMER_CANCELLED")
            return True
        if status == ConsentStatus.AUTHORISED.value:
            doc.update(status=ConsentStatus.REVOKED.value, revoked_at=now,
                       revocation_reason="CUSTOMER_REQUEST", revoked_by="customer", token_revocation=None)
            return True
        return False

    # Escrita condicional ao _rev: um callback concorrente não sobrescreve a revogação.
    doc = await _update(store, consent_id, revoke)
    if doc is None:
        return
    if doc["status"] == ConsentStatus.REJECTED.value:
        await _audit("consent.rejected", pseudonym_id=doc["pseudonym_id"],
                     consent_id=consent_id, reason="CUSTOMER_CANCELLED")
        return

    # A revogação já está gravada e é auditada antes de qualquer I/O externo.
    await _audit("consent.revoked", pseudonym_id=doc["pseudonym_id"], consent_id=consent_id,
                 revocation_reason="CUSTOMER_REQUEST", revoked_by="customer")

    token_revocation = await _revoke_tokens(consent_id)

    def record_token_revocation(current: dict) -> bool:
        current["token_revocation"] = token_revocation
        return True

    # Melhor-esforço: o consentimento já está REVOKED; falhar aqui não pode virar erro
    # para o titular nem impedir o registro.
    try:
        await _update(store, consent_id, record_token_revocation)
    except ServiceError as exc:
        logger.warning("token_revocation=%s não gravado para %s: %s", token_revocation, consent_id, exc.code)


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
