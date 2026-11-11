"""Testes unitários de src/storage/store.py — CloudantStore e get_store.

Sem rede: get_iam_token é monkeypatchado para devolver um token fixo, e o
cliente httpx.AsyncClient é substituído por um fake baseado em MockTransport.
"""
import asyncio
import json
import os

import httpx
import pytest

from src.core.errors import ServiceError
from src.storage.store import CloudantStore, build_store, get_store


# ---------------------------------------------------------------------------
# Helpers de transporte falso
# ---------------------------------------------------------------------------

FAKE_TOKEN = "tok-teste"
CLOUDANT_BASE = "https://cloudant.example.test"


def _fake_token(monkeypatch):
    """Substitui get_iam_token para devolver FAKE_TOKEN sem ir à rede."""
    import src.storage.store as _store_mod
    monkeypatch.setattr(_store_mod, "get_iam_token", lambda _key: _coroutine(FAKE_TOKEN))


def _coroutine(value):
    """Cria uma coroutine que resolve imediatamente com *value*."""
    async def _inner():
        return value
    return _inner()


def _make_transport(responses: list[httpx.Response]):
    """AsyncMockTransport que devolve as respostas em sequência (FIFO)."""
    queue = list(responses)

    class _T(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            resp = queue.pop(0)
            resp.request = request
            return resp

    return _T()


def _json_resp(status: int, body: dict) -> httpx.Response:
    content = json.dumps(body).encode()
    return httpx.Response(
        status,
        content=content,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", CLOUDANT_BASE),
    )


def _patch_client(monkeypatch, transport: httpx.MockTransport):
    """Substitui httpx.AsyncClient pelo cliente que usa o transporte falso."""
    import src.storage.store as _store_mod

    original_init = httpx.AsyncClient.__init__

    class _FakeClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(_store_mod.httpx, "AsyncClient", _FakeClient)


def _setup(monkeypatch, responses: list[httpx.Response]):
    """Configura token fake + transporte falso e devolve um CloudantStore pronto."""
    _fake_token(monkeypatch)
    _patch_client(monkeypatch, _make_transport(responses))
    return CloudantStore(CLOUDANT_BASE, "apikey-fake")


# ---------------------------------------------------------------------------
# CloudantStore.get
# ---------------------------------------------------------------------------


def test_cloudant_get_200_devolve_doc(monkeypatch):
    doc = {"_id": "doc1", "_rev": "1-abc", "dado": "valor"}
    store = _setup(monkeypatch, [_json_resp(200, doc)])

    resultado = asyncio.run(store.get("meu-db", "doc1"))

    assert resultado == doc


def test_cloudant_get_404_devolve_none(monkeypatch):
    store = _setup(monkeypatch, [_json_resp(404, {"error": "not_found"})])

    resultado = asyncio.run(store.get("meu-db", "inexistente"))

    assert resultado is None


# ---------------------------------------------------------------------------
# CloudantStore.put
# ---------------------------------------------------------------------------


def test_cloudant_put_com_rev_passa_rev_no_corpo(monkeypatch):
    """put com rev= explícito não faz GET prévio; envia PUT e espera 201."""
    store = _setup(monkeypatch, [_json_resp(201, {"ok": True, "rev": "2-xyz"})])

    # Não deve levantar exceção
    asyncio.run(store.put("meu-db", "doc1", {"dado": "novo"}, rev="1-abc"))


def test_cloudant_put_sem_rev_busca_rev_existente(monkeypatch):
    """put sem rev= faz GET para obter _rev do doc existente antes de gravar."""
    doc_existente = {"_id": "doc1", "_rev": "3-old", "dado": "antigo"}
    # Primeira resposta: GET para obter rev; segunda: PUT de escrita
    store = _setup(monkeypatch, [
        _json_resp(200, doc_existente),
        _json_resp(201, {"ok": True, "rev": "4-new"}),
    ])

    asyncio.run(store.put("meu-db", "doc1", {"dado": "novo"}))


def test_cloudant_put_sem_rev_doc_novo(monkeypatch):
    """put sem rev= em documento inexistente: GET devolve 404, PUT sem _rev."""
    store = _setup(monkeypatch, [
        _json_resp(404, {"error": "not_found"}),     # GET
        _json_resp(201, {"ok": True, "rev": "1-new"}),  # PUT
    ])

    asyncio.run(store.put("meu-db", "novo-doc", {"dado": "x"}))


def test_cloudant_put_conflito_409_com_rev_levanta_service_error(monkeypatch):
    """Quando PUT com rev= retorna 409, CloudantStore levanta ServiceError CONFLICT."""
    store = _setup(monkeypatch, [_json_resp(409, {"error": "conflict"})])

    with pytest.raises(ServiceError) as exc_info:
        asyncio.run(store.put("meu-db", "doc1", {"dado": "x"}, rev="1-abc"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CONFLICT"


# ---------------------------------------------------------------------------
# CloudantStore.find
# ---------------------------------------------------------------------------


def test_cloudant_find_devolve_lista_de_docs(monkeypatch):
    docs = [{"_id": "d1", "tipo": "x"}, {"_id": "d2", "tipo": "x"}]
    store = _setup(monkeypatch, [_json_resp(200, {"docs": docs})])

    resultado = asyncio.run(store.find("meu-db", {"tipo": "x"}, limit=5))

    assert resultado == docs


def test_cloudant_find_sem_docs_devolve_lista_vazia(monkeypatch):
    store = _setup(monkeypatch, [_json_resp(200, {"docs": []})])

    resultado = asyncio.run(store.find("meu-db", {"tipo": "inexistente"}))

    assert resultado == []


# ---------------------------------------------------------------------------
# CloudantStore.delete
# ---------------------------------------------------------------------------


def test_cloudant_delete_doc_existente_retorna_true(monkeypatch):
    doc = {"_id": "doc1", "_rev": "1-abc"}
    # GET para obter rev + DELETE
    store = _setup(monkeypatch, [
        _json_resp(200, doc),
        _json_resp(200, {"ok": True}),
    ])

    resultado = asyncio.run(store.delete("meu-db", "doc1"))

    assert resultado is True


def test_cloudant_delete_doc_inexistente_retorna_false(monkeypatch):
    store = _setup(monkeypatch, [_json_resp(404, {"error": "not_found"})])

    resultado = asyncio.run(store.delete("meu-db", "nao-existe"))

    assert resultado is False


# ---------------------------------------------------------------------------
# Erro de credencial ausente — _request levanta ServiceError via get_iam_token
# ---------------------------------------------------------------------------


def test_cloudant_sem_apikey_levanta_503(monkeypatch):
    """CloudantStore construído com apikey vazia: get_iam_token levanta 503."""
    # Deixa get_iam_token funcionar de verdade (não monkeypatcha) —
    # ela mesma levanta ServiceError quando apikey é vazia.
    store = CloudantStore(CLOUDANT_BASE, "")

    with pytest.raises(ServiceError) as exc_info:
        asyncio.run(store.get("meu-db", "doc1"))

    assert exc_info.value.status_code == 503


def test_cloudant_http_error_levanta_503(monkeypatch):
    """httpx.HTTPError dentro de _request deve ser mapeado para ServiceError 503."""
    _fake_token(monkeypatch)

    import src.storage.store as _store_mod

    class _ErrorTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("timeout", request=request)

    class _FakeErrorClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=_ErrorTransport(), **kwargs)

    monkeypatch.setattr(_store_mod.httpx, "AsyncClient", _FakeErrorClient)
    store = CloudantStore(CLOUDANT_BASE, "apikey-fake")

    with pytest.raises(ServiceError) as exc_info:
        asyncio.run(store.get("meu-db", "doc1"))

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# get_store — linhas 147-149 (singleton lazy)
# ---------------------------------------------------------------------------


def test_get_store_singleton_lazy(monkeypatch):
    """get_store() constrói o store na primeira chamada e devolve o mesmo objeto."""
    import src.storage.store as _store_mod

    # Limpa o singleton para forçar a criação
    monkeypatch.setattr(_store_mod, "_store", None)
    # Garante que build_store encontra STORE_BACKEND=memory
    monkeypatch.setenv("STORE_BACKEND", "memory")
    monkeypatch.delenv("CLOUDANT_URL", raising=False)
    monkeypatch.delenv("CLOUDANT_APIKEY", raising=False)

    primeiro = get_store()
    segundo = get_store()

    assert primeiro is segundo


def test_get_store_reutiliza_instancia_existente(monkeypatch):
    """get_store() não substitui um _store já definido."""
    import src.storage.store as _store_mod
    from src.storage.store import MemoryStore

    sentinel = MemoryStore()
    monkeypatch.setattr(_store_mod, "_store", sentinel)

    resultado = get_store()

    assert resultado is sentinel
