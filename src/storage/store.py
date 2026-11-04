"""
Armazenamento de documentos. Cloudant em produção/hackathon; memória quando
CLOUDANT_URL não está configurada (desenvolvimento e testes).

Todos os documentos aqui são pseudonimizados (sem CPF) — ver PLANO.md, Sub-Tarefa 4.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

import httpx

from src.core.config import env
from src.core.errors import ServiceError
from src.core.ibm_auth import get_iam_token


class DocumentStore(Protocol):
    async def get(self, db: str, doc_id: str) -> Optional[dict[str, Any]]: ...
    async def put(self, db: str, doc_id: str, doc: dict[str, Any]) -> None: ...
    async def find(self, db: str, selector: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]: ...


class MemoryStore:
    def __init__(self) -> None:
        self._dbs: dict[str, dict[str, dict[str, Any]]] = {}

    async def get(self, db, doc_id):
        doc = self._dbs.get(db, {}).get(doc_id)
        return dict(doc) if doc else None

    async def put(self, db, doc_id, doc):
        self._dbs.setdefault(db, {})[doc_id] = {**doc, "_id": doc_id}

    async def find(self, db, selector, limit=10):
        docs = [d for d in self._dbs.get(db, {}).values()
                if all(d.get(k) == v for k, v in selector.items())]
        return [dict(d) for d in docs[:limit]]


class CloudantStore:
    def __init__(self, url: str, apikey: str) -> None:
        self._url = url.rstrip("/")
        self._apikey = apikey

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = await get_iam_token(self._apikey)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                return await client.request(
                    method, f"{self._url}/{path}",
                    headers={"Authorization": f"Bearer {token}"}, **kwargs,
                )
        except httpx.HTTPError as exc:
            raise ServiceError(503, "SERVICE_UNAVAILABLE", "Cloudant indisponível.") from exc

    async def get(self, db, doc_id):
        resp = await self._request("GET", f"{db}/{doc_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def put(self, db, doc_id, doc):
        existing = await self.get(db, doc_id)
        body = {**doc, "_id": doc_id}
        if existing:
            body["_rev"] = existing["_rev"]
        resp = await self._request("PUT", f"{db}/{doc_id}", json=body)
        resp.raise_for_status()

    async def find(self, db, selector, limit=10):
        resp = await self._request("POST", f"{db}/_find", json={"selector": selector, "limit": limit})
        resp.raise_for_status()
        return resp.json().get("docs", [])


_store: Optional[DocumentStore] = None


def get_store() -> DocumentStore:
    """Dependência FastAPI — singleton por processo."""
    global _store
    if _store is None:
        url, key = env("CLOUDANT_URL"), env("CLOUDANT_APIKEY")
        _store = CloudantStore(url, key) if url and key else MemoryStore()
    return _store
