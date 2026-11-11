"""
Cofre de identidade — M4, Fase 3.

Armazena o CPF cifrado por titular usando envelope encryption:
  - DEK (32 bytes, aleatória por titular) cifra o CPF com AES-256-GCM.
  - KEK (via KeyProvider, purpose="vault_kek") envolve a DEK.

Documento no banco identity_vault (_id = pseudonym_id):
{
  "_id":            "<pseudonym_id>",
  "type":           "identity",
  "key_version":    "v1",          # versão da chave de pseudonimização
  "kek_version":    "v1",          # versão da KEK usada para envolver a DEK
  "wrapped_dek":    "<b64>",       # DEK envolvida com AESGCM(KEK)
  "dek_nonce":      "<b64>",       # nonce do wrapping da DEK
  "cpf_ciphertext": "<b64>",       # CPF cifrado com AESGCM(DEK)
  "nonce":          "<b64>",       # nonce da cifragem do CPF
  "created_at":     "ISO-8601Z",
  "shredded_at":    null           # preenchido pelo shred()
}

Após shred(), wrapped_dek, dek_nonce, cpf_ciphertext e nonce são removidos
do documento corrente com um put (tombstone); shredded_at é preenchido.
O CPF torna-se irrecuperável pela aplicação (o documento corrente não tem mais
o material criptográfico). Cópias anteriores do documento (revisões até a
compactação do Cloudant, backups e réplicas) permanecem decifráveis enquanto
a KEK global existir. Para irrecuperabilidade inclusive em backups (produção):
DEK por titular em Key Protect/HSM, destruída no shred.

Controles de acesso no reveal() (defesa em profundidade, além das camadas de cima):
  - purpose precisa estar em _ALLOWED_PURPOSES.
  - purpose_actors (opcional): mapa purpose → actors autorizados. A factory de
    produção (get_identity_vault) usa _DEFAULT_PURPOSE_ACTORS. O actor ainda é
    declarado pelo chamador dentro do mesmo processo; isso impede uso trocado
    por engano, não um atacante com execução de código (para isso: IAM por
    serviço em produção).
  - consent_checker (opcional): callable(pseudonym_id) -> bool. Para
    SERASA_QUERY, o CPF só sai se houver consentimento AUTHORISED e não
    expirado. A factory de produção liga _cloudant_consent_checker. Falha ao
    consultar = acesso negado (fail closed).

Ligação com o Serasa:
  resolve_for_serasa() chama reveal(purpose="SERASA_QUERY", actor="agent1").
  O HttpSerasaClient em src/agents/agent1/sources.py chama este método (resolver
  injetável; padrão get_identity_vault().resolve_for_serasa); o CPF vai somente
  no corpo do POST /reports, nunca em URL ou log. Sandbox inalterado.
"""

from __future__ import annotations

import base64
import logging
import secrets
from functools import lru_cache
from typing import Callable, Optional, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .key_provider import KeyProvider, default_provider
from src.core.clock import utcnow_iso as _utcnow_iso

logger = logging.getLogger(__name__)

_ALLOWED_PURPOSES = frozenset({"SERASA_QUERY", "LEGAL_REQUEST"})

#: Quem pode pedir cada finalidade na factory de produção.
_DEFAULT_PURPOSE_ACTORS: dict[str, frozenset[str]] = {
    "SERASA_QUERY": frozenset({"agent1"}),
    "LEGAL_REQUEST": frozenset({"internal_api"}),
}

#: Finalidades que exigem consentimento ativo do titular.
_CONSENT_REQUIRED_PURPOSES = frozenset({"SERASA_QUERY"})


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _b64d(data: str) -> bytes:
    return base64.b64decode(data)


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------

class VaultError(RuntimeError):
    """CPF não pode ser recuperado (titular inexistente, shredded ou corrompido)."""


class VaultAccessDenied(VaultError):
    """reveal() negado: actor não autorizado ou titular sem consentimento ativo."""


# ---------------------------------------------------------------------------
# Repositório
# ---------------------------------------------------------------------------

class VaultRepository(Protocol):
    def get(self, pseudonym_id: str) -> Optional[dict]: ...
    def put(self, doc: dict) -> None: ...


class InMemoryVaultRepository:
    """Repositório em memória — apenas para testes (sem rede)."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    def get(self, pseudonym_id: str) -> Optional[dict]:
        doc = self._store.get(pseudonym_id)
        return dict(doc) if doc is not None else None

    def put(self, doc: dict) -> None:
        self._store[doc["_id"]] = dict(doc)

class CloudantVaultRepository:
    """
    Repositório Cloudant para o identity_vault.

    Usa o SDK ibmcloudant síncrono + IAMAuthenticator.
    Banco configurado em CLOUDANT_DB_IDENTITY_VAULT (padrão "identity_vault").
    get() retorna None em 404.
    put() faz upsert via put_document (_id = pseudonym_id).
    """

    def __init__(self, client, db: str = "identity_vault") -> None:
        self._client = client
        self._db = db

    def get(self, pseudonym_id: str) -> Optional[dict]:
        from ibm_cloud_sdk_core import ApiException

        try:
            return self._client.get_document(
                db=self._db, doc_id=pseudonym_id
            ).get_result()
        except ApiException as exc:
            if exc.status_code == 404:
                return None
            raise

    def put(self, doc: dict) -> None:
        from ibmcloudant.cloudant_v1 import Document

        self._client.put_document(
            db=self._db,
            doc_id=doc["_id"],
            document=Document.from_dict(doc),
        )

@lru_cache(maxsize=1)
def get_vault_repository() -> VaultRepository:
    """
    Factory com @lru_cache — padrão do M2 (get_repository()).
    Em produção, requer CLOUDANT_URL e CLOUDANT_APIKEY.
    """
    from src.core.cloudant import build_cloudant_client
    from src.core.config import ConfigurationError, vault_db

    try:
        client = build_cloudant_client()
    except ConfigurationError:
        raise ConfigurationError(
            "CLOUDANT_URL e CLOUDANT_APIKEY são obrigatórios para CloudantVaultRepository."
        )
    return CloudantVaultRepository(client=client, db=vault_db())


# ---------------------------------------------------------------------------
# Serviço principal
# ---------------------------------------------------------------------------

class IdentityVault:
    """
    Cofre de identidade: store / reveal / shred / resolve_for_serasa.

    Parâmetros:
        repository    — VaultRepository (obrigatório).
        key_provider  — KeyProvider (padrão: default_provider do módulo key_provider).
        audit         — callable(event_type: str, **fields); padrão: no-op.
                        Chamado em reveal() com ou sem sucesso. Nunca recebe o CPF.
        clock         — callable() -> str (ISO-8601Z); padrão: _utcnow_iso().
                        Injetável em testes para controlar o tempo.
        purpose_actors — mapa purpose → frozenset de actors autorizados; None
                        desativa a checagem (padrão nos testes unitários).
        consent_checker — callable(pseudonym_id) -> bool; None desativa a
                        checagem de consentimento ativo para SERASA_QUERY.
    """

    def __init__(
        self,
        repository: VaultRepository,
        key_provider: KeyProvider = default_provider,
        audit: Optional[Callable[..., None]] = None,
        clock: Optional[Callable[[], str]] = None,
        purpose_actors: Optional[dict[str, frozenset[str]]] = None,
        consent_checker: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._repo = repository
        self._purpose_actors = purpose_actors
        self._consent_checker = consent_checker
        self._kp = key_provider
        self._audit: Callable[..., None] = audit if audit is not None else (lambda event_type, **kw: None)
        self._clock: Callable[[], str] = clock if clock is not None else _utcnow_iso

    # ------------------------------------------------------------------
    # store
    # ------------------------------------------------------------------

    def store(self, cpf: str) -> str:
        """
        Persiste o CPF cifrado e retorna o pseudonym_id.

        Idempotência:
          - Titular ATIVO (shredded_at nulo) → retorna pseudonym_id sem regravar.
          - Tombstone (shredded_at preenchido) → recria com nova DEK; preserva
            _rev quando houver; registra previously_shredded_at.
          - Inexistente → cria normalmente.
        """
        from .pseudonymization import _active_version, _master_key_cached, _hmac_hex, normalize_cpf as _norm
        cpf_normalized = _norm(cpf)
        # Usar o KeyProvider do cofre para gerar o pseudonym_id, garantindo
        # consistência quando um provider fake/diferente é injetado.
        pseudo_version, pseudo_key = self._kp.get("pseudonym")
        pseudonym_id = _hmac_hex(pseudo_key, f"cpf:{cpf_normalized}")
        key_version = pseudo_version
        kek_version, kek = self._kp.get("vault_kek")

        existing = self._repo.get(pseudonym_id)

        # Titular ativo → idempotente
        if existing is not None and existing.get("shredded_at") is None:
            return pseudonym_id

        # Novo documento
        dek = secrets.token_bytes(32)
        aad = pseudonym_id.encode()

        # Cifrar CPF normalizado com DEK
        cpf_nonce = secrets.token_bytes(12)
        cpf_ciphertext = AESGCM(dek).encrypt(cpf_nonce, cpf_normalized.encode(), aad)

        # Envolver DEK com KEK
        dek_nonce = secrets.token_bytes(12)
        wrapped_dek = AESGCM(kek).encrypt(dek_nonce, dek, aad)

        doc: dict = {
            "_id": pseudonym_id,
            "type": "identity",
            "key_version": key_version,
            "kek_version": kek_version,
            "wrapped_dek": _b64e(wrapped_dek),
            "dek_nonce": _b64e(dek_nonce),
            "cpf_ciphertext": _b64e(cpf_ciphertext),
            "nonce": _b64e(cpf_nonce),
            "created_at": self._clock(),
            "shredded_at": None,
        }

        # Se existia tombstone, preservar _rev e registrar previously_shredded_at
        if existing is not None:
            if "_rev" in existing:
                doc["_rev"] = existing["_rev"]
            doc["previously_shredded_at"] = existing.get("shredded_at")

        try:
            self._repo.put(doc)
        except Exception as exc:
            # Tratar criação concorrente (409): se o documento agora está ativo,
            # retornar o pseudonym_id existente; caso contrário, relançar.
            code = getattr(exc, "status_code", None)
            if code == 409:
                fresh = self._repo.get(pseudonym_id)
                if fresh is not None and fresh.get("shredded_at") is None:
                    return pseudonym_id
            raise
        return pseudonym_id

    # ------------------------------------------------------------------
    # reveal
    # ------------------------------------------------------------------

    def _fire_audit(self, event_type: str, **kwargs) -> None:
        """Chama self._audit absorvendo qualquer exceção (audit nunca derruba o fluxo)."""
        try:
            self._audit(event_type, **kwargs)
        except Exception as exc:
            logger.warning(
                "audit callback levantou exceção; evento descartado: %s",
                type(exc).__name__,
            )

    def reveal(self, pseudonym_id: str, *, purpose: str, actor: str) -> str:
        """
        Decifra e retorna o CPF.

        purpose deve ser um dos valores em _ALLOWED_PURPOSES; caso contrário,
        ValueError é levantado ANTES de qualquer consulta ao repositório.

        Sempre registra o evento vault.reveal via self._audit (nunca com o CPF).
        """
        if purpose not in _ALLOWED_PURPOSES:
            self._fire_audit(
                "vault.reveal",
                pseudonym_id=pseudonym_id,
                actor=actor,
                purpose=purpose,
                ok=False,
                reason="invalid_purpose",
            )
            raise ValueError(
                f"purpose inválido: {purpose!r}. "
                f"Valores aceitos: {sorted(_ALLOWED_PURPOSES)}"
            )

        if self._purpose_actors is not None:
            allowed_actors = self._purpose_actors.get(purpose, frozenset())
            if actor not in allowed_actors:
                self._fire_audit(
                    "vault.reveal",
                    pseudonym_id=pseudonym_id,
                    actor=actor,
                    purpose=purpose,
                    ok=False,
                    reason="actor_not_allowed",
                )
                raise VaultAccessDenied(
                    f"actor {actor!r} não autorizado para purpose {purpose!r}."
                )

        if self._consent_checker is not None and purpose in _CONSENT_REQUIRED_PURPOSES:
            try:
                has_consent = bool(self._consent_checker(pseudonym_id))
            except Exception as exc:
                logger.error(
                    "vault: falha ao verificar consentimento (acesso negado): %s",
                    type(exc).__name__,
                )
                has_consent = False
            if not has_consent:
                self._fire_audit(
                    "vault.reveal",
                    pseudonym_id=pseudonym_id,
                    actor=actor,
                    purpose=purpose,
                    ok=False,
                    reason="no_active_consent",
                )
                raise VaultAccessDenied(
                    "Titular sem consentimento ativo para esta finalidade."
                )

        doc = self._repo.get(pseudonym_id)

        if doc is None or doc.get("shredded_at") is not None:
            self._fire_audit(
                "vault.reveal",
                pseudonym_id=pseudonym_id,
                actor=actor,
                purpose=purpose,
                ok=False,
            )
            raise VaultError(
                "Titular não encontrado ou já destruído (shredded)."
            )

        try:
            kek_version = doc["kek_version"]
            _, kek = self._kp.get("vault_kek", version=kek_version)

            aad = pseudonym_id.encode()

            # Desenvolver DEK
            dek = AESGCM(kek).decrypt(
                _b64d(doc["dek_nonce"]),
                _b64d(doc["wrapped_dek"]),
                aad,
            )

            # Decifrar CPF
            cpf_bytes = AESGCM(dek).decrypt(
                _b64d(doc["nonce"]),
                _b64d(doc["cpf_ciphertext"]),
                aad,
            )
        except Exception as exc:
            self._fire_audit(
                "vault.reveal",
                pseudonym_id=pseudonym_id,
                actor=actor,
                purpose=purpose,
                ok=False,
            )
            raise VaultError(
                "Falha ao decifrar CPF: chave ou ciphertext corrompido."
            ) from exc

        self._fire_audit(
            "vault.reveal",
            pseudonym_id=pseudonym_id,
            actor=actor,
            purpose=purpose,
            ok=True,
        )
        return cpf_bytes.decode()

    # ------------------------------------------------------------------
    # shred
    # ------------------------------------------------------------------

    def shred(self, pseudonym_id: str) -> bool:
        """
        Remove o material criptográfico do documento corrente (crypto-shredding).

        Remove wrapped_dek, dek_nonce, cpf_ciphertext e nonce com um put;
        preenche shredded_at (tombstone). O CPF torna-se irrecuperável pela
        aplicação. Cópias anteriores (revisões do Cloudant até a compactação,
        backups e réplicas) permanecem decifráveis enquanto a KEK global existir.
        Para irrecuperabilidade inclusive em backups (produção): DEK por titular
        em Key Protect/HSM, destruída no shred.

        Idempotente: retorna True se algo mudou, False se já estava destruído
        ou inexistente.
        """
        doc = self._repo.get(pseudonym_id)
        if doc is None:
            return False
        if doc.get("shredded_at") is not None:
            return False  # já destruído

        doc.pop("wrapped_dek", None)
        doc.pop("dek_nonce", None)
        doc.pop("cpf_ciphertext", None)
        doc.pop("nonce", None)
        doc["shredded_at"] = self._clock()

        self._repo.put(doc)
        return True

    # ------------------------------------------------------------------
    # resolve_for_serasa
    # ------------------------------------------------------------------

    def resolve_for_serasa(self, pseudonym_id: str) -> str:
        """
        Retorna o CPF para consulta ao Serasa (re-identificação).

        Delegação: reveal(purpose="SERASA_QUERY", actor="agent1").

        Ponto de ligação: HttpSerasaClient.fetch_report() em
        src/agents/agent1/sources.py deve chamar este método em vez de
        receber o CPF diretamente — ver docs/m4-chaves-e-iam.md.
        """
        return self.reveal(pseudonym_id, purpose="SERASA_QUERY", actor="agent1")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _default_audit() -> Callable[..., None]:
    """
    Tenta obter get_audit_logger().record_event para uso como callback de auditoria.

    Se get_audit_logger() levantar qualquer exceção (ex: ConfigurationError quando
    CLOUDANT_URL/APIKEY não estão configurados), retorna um no-op e registra um
    warning com o tipo da exceção (sem dados de configuração ou PII).

    Import local de src.privacy.audit para evitar import circular
    (audit → pii_guard; identity_vault → key_provider, pseudonymization).
    """
    from src.privacy.audit import get_audit_logger_or_none  # local — evita import circular

    audit_logger = get_audit_logger_or_none()
    if audit_logger is not None:
        return audit_logger.record_event
    return lambda event_type, **kw: None


def has_active_consent(consents, pseudonym_id: str, now=None) -> bool:
    """
    True se algum consentimento do titular está AUTHORISED e com expires_at no
    futuro. `consents` é um iterável de documentos do consent_registry.
    Documento com expires_at ausente ou inválido não conta.
    """
    from datetime import datetime, timezone

    from src.agents.agent1.dates import parse_utc

    now = now or datetime.now(timezone.utc)
    for doc in consents:
        if doc.get("pseudonym_id") != pseudonym_id or doc.get("status") != "AUTHORISED":
            continue
        try:
            expires_at = parse_utc(doc.get("expires_at"))
        except (TypeError, ValueError):
            continue
        if expires_at > now:
            return True
    return False


def _cloudant_consent_checker(pseudonym_id: str) -> bool:
    """Consulta o consent_registry real (via RetentionStore)."""
    from src.privacy.retention_store import get_retention_store  # local — evita ciclo

    store = get_retention_store()
    return has_active_consent(
        store.iter_consents({"pseudonym_id": pseudonym_id, "status": "AUTHORISED"}),
        pseudonym_id,
    )


def get_identity_vault() -> IdentityVault:
    """
    Retorna uma instância de IdentityVault com o repositório Cloudant real,
    o AuditLogger padrão (get_audit_logger().record_event) e os controles de
    acesso de produção: purpose amarrado ao actor e consentimento ativo
    exigido para SERASA_QUERY.

    Equivalente a get_repository() do M2, mas sem @lru_cache no nível da
    vault (o repositório já é cacheado por get_vault_repository()).
    """
    return IdentityVault(
        repository=get_vault_repository(),
        audit=_default_audit(),
        purpose_actors=_DEFAULT_PURPOSE_ACTORS,
        consent_checker=_cloudant_consent_checker,
    )
