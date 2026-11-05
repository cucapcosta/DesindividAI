"""
Persistência do Agente 1.

Produção: IBM Cloud Databases for MongoDB. Hackathon: Cloudant (ver
docs/infra-setup.md). O schema do documento é o mesmo nos dois — ver
docs/m2-agent1.md#schema-do-snapshot.

O repositório em memória existe só para testes (injetado explicitamente).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional, Protocol

from .config import CLOUDANT_VARS, require_env

class Repository(Protocol):
    def get_consent(self, consent_id: str) -> Optional[dict]: ...
    def save_snapshot(self, snapshot: dict) -> None: ...
    def get_snapshot(self, snapshot_id: str) -> Optional[dict]: ...


class InMemoryRepository:
    def __init__(self, consents: list[dict] | None = None):
        self._consents = {c["consent_id"]: c for c in consents or []}
        self._snapshots: dict[str, dict] = {}

    def get_consent(self, consent_id: str) -> Optional[dict]:
        return self._consents.get(consent_id)

    def save_snapshot(self, snapshot: dict) -> None:
        self._snapshots[snapshot["snapshot_id"]] = snapshot

    def get_snapshot(self, snapshot_id: str) -> Optional[dict]:
        return self._snapshots.get(snapshot_id)


class CloudantRepository:
    def __init__(self, url: str, apikey: str, consents_db: str, snapshots_db: str):
        from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
        from ibmcloudant.cloudant_v1 import CloudantV1

        self._client = CloudantV1(authenticator=IAMAuthenticator(apikey))
        self._client.set_service_url(url)
        self._consents_db = consents_db
        self._snapshots_db = snapshots_db

    def get_consent(self, consent_id: str) -> Optional[dict]:
        # consent_registry é escrito por M1; o _id não é garantidamente o
        # consent_id, então buscamos pelo campo (índice em consent_id).
        result = self._client.post_find(
            db=self._consents_db, selector={"consent_id": consent_id}, limit=1
        ).get_result()
        docs = result.get("docs") or []
        return docs[0] if docs else None

    def save_snapshot(self, snapshot: dict) -> None:
        from ibmcloudant.cloudant_v1 import Document

        # _id = snapshot_id: é por ele que o Agente 2 (M3) lê o snapshot.
        doc = {"_id": snapshot["snapshot_id"], **snapshot}
        self._client.put_document(
            db=self._snapshots_db, doc_id=doc["_id"], document=Document.from_dict(doc)
        )

    def get_snapshot(self, snapshot_id: str) -> Optional[dict]:
        from ibm_cloud_sdk_core import ApiException

        try:
            return self._client.get_document(
                db=self._snapshots_db, doc_id=snapshot_id
            ).get_result()
        except ApiException as exc:
            if exc.code == 404:
                return None
            raise


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    require_env(*CLOUDANT_VARS)
    return CloudantRepository(
        url=os.environ["CLOUDANT_URL"],
        apikey=os.environ["CLOUDANT_APIKEY"],
        consents_db=os.getenv("CLOUDANT_DB_CONSENTS", "consent_registry"),
        snapshots_db=os.getenv("CLOUDANT_DB_SNAPSHOTS", "financial_snapshots"),
    )
