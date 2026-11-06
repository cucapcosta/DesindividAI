"""
RetentionStore — M4, Fase 6 / Fase B1.

Abstração de acesso aos bancos envolvidos no job de retenção:
  - consent_registry    (consentimentos)
  - financial_snapshots (snapshots do Agente 1)
  - identity_vault      (cofre de identidade)
  - decision_traces     (simulações e traces do Agente 2)

Padrão: Protocol + InMemory (testes) + Cloudant (produção), igual ao M2.

Cloudant:
  - post_find com paginação por bookmark (limit=200).
  - delete com _rev (get → delete).
  - 412 em ensure_databases/ensure_indexes = já existe → ignorar.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Iterator, Optional, Protocol

from src.core.config import decisions_db as _decisions_db

logger = logging.getLogger(__name__)

_PAGE_SIZE = 200


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class RetentionStore(Protocol):
    def iter_consents(self, selector: dict) -> Iterator[dict]: ...
    def update_consent(self, doc: dict) -> None: ...
    def delete_consent(self, doc: dict) -> None: ...
    def iter_snapshots(self, selector: dict) -> Iterator[dict]: ...
    def delete_snapshot(self, doc: dict) -> None: ...
    def iter_vault(self, selector: dict) -> Iterator[dict]: ...
    def iter_decisions(self, selector: dict) -> Iterator[dict]: ...
    def delete_decision(self, doc: dict) -> None: ...


# ---------------------------------------------------------------------------
# InMemory
# ---------------------------------------------------------------------------

class InMemoryRetentionStore:
    """
    Repositório em memória — apenas para testes (sem rede).

    consents:  lista de dicts com pelo menos consent_id e _id.
    snapshots: lista de dicts com pelo menos snapshot_id e _id.
    vault:     lista de dicts com _id = pseudonym_id.
    decisions: lista de dicts com _id, type, pseudonym_id, expires_at.
    """

    def __init__(
        self,
        consents: list[dict] | None = None,
        snapshots: list[dict] | None = None,
        vault: list[dict] | None = None,
        decisions: list[dict] | None = None,
    ) -> None:
        # keyed by _id
        self._consents: dict[str, dict] = {}
        self._snapshots: dict[str, dict] = {}
        self._vault: dict[str, dict] = {}
        self._decisions: dict[str, dict] = {}

        for c in consents or []:
            key = c.get("_id") or c.get("consent_id", "")
            self._consents[key] = dict(c)

        for s in snapshots or []:
            key = s.get("_id") or s.get("snapshot_id", "")
            self._snapshots[key] = dict(s)

        for v in vault or []:
            key = v["_id"]
            self._vault[key] = dict(v)

        for d in decisions or []:
            key = d["_id"]
            self._decisions[key] = dict(d)

    # -- consents --

    def iter_consents(self, selector: dict) -> Iterator[dict]:
        for doc in list(self._consents.values()):
            if _matches(doc, selector):
                yield dict(doc)

    def update_consent(self, doc: dict) -> None:
        key = doc.get("_id") or doc.get("consent_id", "")
        if key in self._consents:
            self._consents[key] = dict(doc)
        else:
            self._consents[key] = dict(doc)

    def delete_consent(self, doc: dict) -> None:
        key = doc.get("_id") or doc.get("consent_id", "")
        self._consents.pop(key, None)

    # -- snapshots --

    def iter_snapshots(self, selector: dict) -> Iterator[dict]:
        for doc in list(self._snapshots.values()):
            if _matches(doc, selector):
                yield dict(doc)

    def delete_snapshot(self, doc: dict) -> None:
        key = doc.get("_id") or doc.get("snapshot_id", "")
        self._snapshots.pop(key, None)

    # -- vault --

    def iter_vault(self, selector: dict) -> Iterator[dict]:
        for doc in list(self._vault.values()):
            if _matches(doc, selector):
                yield dict(doc)

    # -- decisions --

    def iter_decisions(self, selector: dict) -> Iterator[dict]:
        for doc in list(self._decisions.values()):
            if _matches(doc, selector):
                yield dict(doc)

    def delete_decision(self, doc: dict) -> None:
        key = doc.get("_id", "")
        self._decisions.pop(key, None)


def _matches(doc: dict, selector: dict) -> bool:
    """Filtragem simples equivalente ao Mango $eq e $in."""
    for key, value in selector.items():
        if isinstance(value, dict):
            # Operadores Mango simples
            if "$in" in value:
                if doc.get(key) not in value["$in"]:
                    return False
            # Outros operadores podem ser adicionados aqui
        elif value is None:
            if doc.get(key) is not None:
                return False
        else:
            if doc.get(key) != value:
                return False
    return True


# ---------------------------------------------------------------------------
# Cloudant
# ---------------------------------------------------------------------------

class CloudantRetentionStore:
    """
    Repositório Cloudant para o job de retenção.

    Usa o SDK ibmcloudant síncrono + IAMAuthenticator.
    post_find com paginação por bookmark (limit=_PAGE_SIZE).
    delete com _rev (get_document → delete_document).

    Bancos:
      CLOUDANT_DB_CONSENTS   (padrão "consent_registry")
      CLOUDANT_DB_SNAPSHOTS  (padrão "financial_snapshots")
      CLOUDANT_DB_IDENTITY_VAULT (padrão "identity_vault")
      CLOUDANT_DB_DECISIONS  (padrão "decision_traces")
    """

    def __init__(
        self,
        url: str,
        apikey: str,
        consents_db: str = "consent_registry",
        snapshots_db: str = "financial_snapshots",
        vault_db: str = "identity_vault",
        decisions_db: str = "decision_traces",
    ) -> None:
        from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
        from ibmcloudant.cloudant_v1 import CloudantV1

        self._client = CloudantV1(authenticator=IAMAuthenticator(apikey))
        self._client.set_service_url(url)
        self._consents_db = consents_db
        self._snapshots_db = snapshots_db
        self._vault_db = vault_db
        self._decisions_db = decisions_db

    # -- helpers --

    def _iter_db(self, db: str, selector: dict) -> Iterator[dict]:
        bookmark: Optional[str] = None
        while True:
            kwargs: dict = {"db": db, "selector": selector, "limit": _PAGE_SIZE}
            if bookmark:
                kwargs["bookmark"] = bookmark
            result = self._client.post_find(**kwargs).get_result()
            docs = result.get("docs", [])
            for doc in docs:
                yield doc
            if len(docs) < _PAGE_SIZE:
                break
            next_bookmark = result.get("bookmark")
            if not next_bookmark or next_bookmark == bookmark:
                break
            bookmark = next_bookmark

    def _delete_doc(self, db: str, doc: dict) -> None:
        from ibm_cloud_sdk_core import ApiException

        doc_id = doc.get("_id", "")
        rev = doc.get("_rev", "")
        if not rev:
            # Fetch _rev if not present
            try:
                fetched = self._client.get_document(db=db, doc_id=doc_id).get_result()
                rev = fetched.get("_rev", "")
            except ApiException as exc:
                if exc.code == 404:
                    return
                raise
        try:
            self._client.delete_document(db=db, doc_id=doc_id, rev=rev)
        except ApiException as exc:
            if exc.code == 404:
                return
            raise

    # -- consents --

    def iter_consents(self, selector: dict) -> Iterator[dict]:
        return self._iter_db(self._consents_db, selector)

    def update_consent(self, doc: dict) -> None:
        from ibmcloudant.cloudant_v1 import Document

        self._client.put_document(
            db=self._consents_db,
            doc_id=doc["_id"],
            document=Document.from_dict(doc),
        )

    def delete_consent(self, doc: dict) -> None:
        self._delete_doc(self._consents_db, doc)

    # -- snapshots --

    def iter_snapshots(self, selector: dict) -> Iterator[dict]:
        return self._iter_db(self._snapshots_db, selector)

    def delete_snapshot(self, doc: dict) -> None:
        self._delete_doc(self._snapshots_db, doc)

    # -- vault --

    def iter_vault(self, selector: dict) -> Iterator[dict]:
        return self._iter_db(self._vault_db, selector)

    # -- decisions --

    def iter_decisions(self, selector: dict) -> Iterator[dict]:
        return self._iter_db(self._decisions_db, selector)

    def delete_decision(self, doc: dict) -> None:
        self._delete_doc(self._decisions_db, doc)


# ---------------------------------------------------------------------------
# ensure_databases / ensure_indexes
# ---------------------------------------------------------------------------

def ensure_databases(client) -> None:
    """
    Cria identity_vault, audit_trail e decision_traces se não existirem.
    412 = já existe → ignorar silenciosamente.
    """
    from ibm_cloud_sdk_core import ApiException

    vault_db = os.getenv("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault")
    audit_db = os.getenv("CLOUDANT_DB_AUDIT", "audit_trail")
    decisions_db = _decisions_db()

    for db in (vault_db, audit_db, decisions_db):
        try:
            client.put_database(db=db).get_result()
            logger.info("ensure_databases: banco criado: %s", db)
        except ApiException as exc:
            if exc.code == 412:
                logger.debug("ensure_databases: banco já existe: %s", db)
            else:
                raise


def ensure_indexes(client) -> None:
    """
    Cria índices Mango idempotentes nos bancos do M4.
    Índices do M2 (by-pseudonym, by-expiry) já existem; aqui só os do M4.
    409 = já existe → ignorar.
    """
    from ibm_cloud_sdk_core import ApiException

    consents_db = os.getenv("CLOUDANT_DB_CONSENTS", "consent_registry")
    audit_db = os.getenv("CLOUDANT_DB_AUDIT", "audit_trail")
    decisions_db = _decisions_db()

    indexes: list[tuple[str, list[str], str]] = [
        # (db, fields, name)
        (consents_db, ["status", "awaiting_expires_at"], "by-status-awaiting-expires"),
        (consents_db, ["expires_at"], "by-expires-at"),
        (consents_db, ["pseudonym_id", "status"], "by-pseudonym-status"),
        (audit_db, ["pseudonym_id", "recorded_at"], "by-pseudonym-recorded"),
        (audit_db, ["event_type", "recorded_at"], "by-event-recorded"),
        (decisions_db, ["pseudonym_id"], "by-pseudonym"),
        (decisions_db, ["type", "expires_at"], "by-type-expires"),
    ]

    for db, fields, name in indexes:
        try:
            client.post_index(
                db=db,
                index={"fields": fields},
                name=name,
                type="json",
            ).get_result()
            logger.debug("ensure_indexes: índice criado/verificado: %s/%s", db, name)
        except ApiException as exc:
            if exc.code == 409:
                logger.debug("ensure_indexes: índice já existe: %s/%s", db, name)
            else:
                raise


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_retention_store() -> RetentionStore:
    """
    Factory com @lru_cache — padrão do M2.
    Requer CLOUDANT_URL e CLOUDANT_APIKEY.
    """
    from src.agents.agent1.config import ConfigurationError

    url = os.getenv("CLOUDANT_URL", "")
    apikey = os.getenv("CLOUDANT_APIKEY", "")
    if not url or not apikey:
        raise ConfigurationError(
            "CLOUDANT_URL e CLOUDANT_APIKEY são obrigatórios para CloudantRetentionStore."
        )
    return CloudantRetentionStore(
        url=url,
        apikey=apikey,
        consents_db=os.getenv("CLOUDANT_DB_CONSENTS", "consent_registry"),
        snapshots_db=os.getenv("CLOUDANT_DB_SNAPSHOTS", "financial_snapshots"),
        vault_db=os.getenv("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault"),
        decisions_db=_decisions_db(),
    )
