"""
Armazenamento de documentos. Cloudant em produção/hackathon. Sem credenciais, falha
com 503 (como o Agente 1): a memória só é usada com STORE_BACKEND=memory explícito
(desenvolvimento local); nada é perdido em silêncio por erro de configuração.

Todos os documentos aqui são pseudonimizados (sem CPF) — ver PLANO.md, Sub-Tarefa 4.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol
from urllib.parse import quote

import httpx

from src.core.config import env
from src.core.errors import ServiceError
from src.core.ibm_auth import get_iam_token


class DocumentStore(Protocol):
    async def get(self, db: str, doc_id: str) -> Optional[dict[str, Any]]: ...
    async def put(self, db: str, doc_id: str, doc: dict[str, Any], *, rev: Optional[str] = None) -> None:
        """Grava o documento. Com ``rev``, a escrita só acontece se o documento ainda estiver
        nessa revisão; se outra escrita chegou antes, levanta ServiceError(409, "CONFLICT")."""
    async def find(self, db: str, selector: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]: ...
    async def delete(self, db: str, doc_id: str) -> bool: ...


class MemoryStore:
    def __init__(self) -> None:
        self._dbs: dict[str, dict[str, dict[str, Any]]] = {}
        self._seq = 0

    async def get(self, db, doc_id):
        doc = self._dbs.get(db, {}).get(doc_id)
        return dict(doc) if doc else None

    async def put(self, db, doc_id, doc, *, rev=None):
        current = self._dbs.get(db, {}).get(doc_id)
        if rev is not None and (current or {}).get("_rev") != rev:
            raise ServiceError(409, "CONFLICT", "Documento alterado por outra escrita.")
        self._seq += 1
        self._dbs.setdefault(db, {})[doc_id] = {**doc, "_id": doc_id, "_rev": f"{self._seq}-mem"}

    async def find(self, db, selector, limit=10):
        docs = [d for d in self._dbs.get(db, {}).values()
                if all(d.get(k) == v for k, v in selector.items())]
        return [dict(d) for d in docs[:limit]]

    async def delete(self, db, doc_id):
        db_store = self._dbs.get(db, {})
        if doc_id not in db_store:
            return False
        del db_store[doc_id]
        return True


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

    def _check(self, resp: httpx.Response) -> None:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ServiceError(503, "SERVICE_UNAVAILABLE", "Cloudant indisponível.") from exc

    async def get(self, db, doc_id):
        resp = await self._request("GET", f"{quote(db, safe='')}/{quote(doc_id, safe='')}")
        if resp.status_code == 404:
            return None
        self._check(resp)
        return resp.json()

    async def put(self, db, doc_id, doc, *, rev=None):
        body = {**doc, "_id": doc_id}
        if rev is not None:
            body["_rev"] = rev
        else:
            existing = await self.get(db, doc_id)
            if existing:
                body["_rev"] = existing["_rev"]
        resp = await self._request("PUT", f"{quote(db, safe='')}/{quote(doc_id, safe='')}", json=body)
        if rev is not None and resp.status_code == 409:
            raise ServiceError(409, "CONFLICT", "Documento alterado por outra escrita.")
        self._check(resp)

    async def ensure_db(self, db: str) -> bool:
        """Cria o database se não existir (idempotente). Retorna True se criou."""
        resp = await self._request("PUT", quote(db, safe=""))
        if resp.status_code == 412:  # já existe
            return False
        self._check(resp)
        return True

    async def find(self, db, selector, limit=10):
        resp = await self._request("POST", f"{quote(db, safe='')}/_find", json={"selector": selector, "limit": limit})
        self._check(resp)
        return resp.json().get("docs", [])

    async def delete(self, db, doc_id):
        resp = await self._request("GET", f"{quote(db, safe='')}/{quote(doc_id, safe='')}")
        if resp.status_code == 404:
            return False
        self._check(resp)
        rev = resp.json()["_rev"]
        resp = await self._request(
            "DELETE", f"{quote(db, safe='')}/{quote(doc_id, safe='')}",
            params={"rev": rev},
        )
        self._check(resp)
        return True


_store: Optional[DocumentStore] = None


def build_store() -> DocumentStore:
    if env("STORE_BACKEND").lower() == "memory":
        return MemoryStore()
    url, key = env("CLOUDANT_URL"), env("CLOUDANT_APIKEY")
    if not (url and key):
        raise ServiceError(
            503, "SERVICE_UNAVAILABLE",
            "Cloudant não configurado (CLOUDANT_URL e CLOUDANT_APIKEY). "
            "Para desenvolvimento local sem Cloudant, defina STORE_BACKEND=memory.",
        )
    return CloudantStore(url, key)


def get_store() -> DocumentStore:
    """Dependência FastAPI — singleton por processo."""
    global _store
    if _store is None:
        _store = build_store()
    return _store
