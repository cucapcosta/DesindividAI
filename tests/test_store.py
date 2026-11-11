"""Testes unitários de src/storage/store.py — CloudantStore e get_store.

Sem rede: get_iam_token é monkeypatchado para devolver um token fixo, e o
cliente httpx.AsyncClient é substituído por um fake baseado em MockTransport.
"""
import asyncio
import json
from urllib.parse import quote

import httpx
import pytest

from src.core.errors import ServiceError
from src.storage.store import CloudantStore, get_store


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


class _RecordingTransport(httpx.AsyncBaseTransport):
    """AsyncTransport que grava as requisições e devolve as respostas em sequência (FIFO)."""

    def __init__(self, responses: list[httpx.Response]):
        self._queue = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        resp = self._queue.pop(0)
        resp.request = request
        return resp


def _json_resp(status: int, body: dict) -> httpx.Response:
    content = json.dumps(body).encode()
    return httpx.Response(
        status,
        content=content,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", CLOUDANT_BASE),
    )


def _setup(monkeypatch, responses: list[httpx.Response]):
    """Configura token fake + transporte gravador e devolve (store, transport)."""
    _fake_token(monkeypatch)
    transport = _RecordingTransport(responses)
    import src.storage.store as _store_mod

    class _FakeClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(_store_mod.httpx, "AsyncClient", _FakeClient)
    return CloudantStore(CLOUDANT_BASE, "apikey-fake"), transport


# ---------------------------------------------------------------------------
# CloudantStore.get
# ---------------------------------------------------------------------------


def test_cloudant_get_200_devolve_doc(monkeypatch):
    doc = {"_id": "doc1", "_rev": "1-abc", "dado": "valor"}
    store, transport = _setup(monkeypatch, [_json_resp(200, doc)])

    resultado = asyncio.run(store.get("meu-db", "doc1"))

    assert resultado == doc
    req = transport.requests[0]
    assert req.method == "GET"
    assert req.url == f"{CLOUDANT_BASE}/{quote('meu-db', safe='')}/{quote('doc1', safe='')}"
    assert req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"


def test_cloudant_get_404_devolve_none(monkeypatch):
    store, transport = _setup(monkeypatch, [_json_resp(404, {"error": "not_found"})])

    resultado = asyncio.run(store.get("meu-db", "inexistente"))

    assert resultado is None
    req = transport.requests[0]
    assert req.method == "GET"
    assert str(req.url).endswith(f"/{quote('meu-db', safe='')}/{quote('inexistente', safe='')}")


def test_cloudant_get_doc_id_com_escape(monkeypatch):
    """doc_id com '/' e ' ' deve aparecer percent-encoded na URL."""
    doc_id = "a/b c"
    doc = {"_id": doc_id, "_rev": "1-abc"}
    store, transport = _setup(monkeypatch, [_json_resp(200, doc)])

    resultado = asyncio.run(store.get("meu-db", doc_id))

    assert resultado == doc
    req = transport.requests[0]
    expected_url = f"{CLOUDANT_BASE}/{quote('meu-db', safe='')}/{quote(doc_id, safe='')}"
    assert req.url == expected_url


# ---------------------------------------------------------------------------
# CloudantStore.put
# ---------------------------------------------------------------------------


def test_cloudant_put_com_rev_passa_rev_no_corpo(monkeypatch):
    """put com rev= explícito não faz GET prévio; envia PUT com _rev no corpo."""
    store, transport = _setup(monkeypatch, [_json_resp(201, {"ok": True, "rev": "2-xyz"})])

    asyncio.run(store.put("meu-db", "doc1", {"dado": "novo"}, rev="1-abc"))

    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req.method == "PUT"
    assert req.url == f"{CLOUDANT_BASE}/{quote('meu-db', safe='')}/{quote('doc1', safe='')}"
    assert req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
    body = json.loads(req.content)
    assert body["_rev"] == "1-abc"


def test_cloudant_put_sem_rev_busca_rev_existente(monkeypatch):
    """put sem rev= faz GET para obter _rev do doc existente antes de gravar."""
    doc_existente = {"_id": "doc1", "_rev": "3-old", "dado": "antigo"}
    store, transport = _setup(monkeypatch, [
        _json_resp(200, doc_existente),
        _json_resp(201, {"ok": True, "rev": "4-new"}),
    ])

    asyncio.run(store.put("meu-db", "doc1", {"dado": "novo"}))

    assert len(transport.requests) == 2
    get_req = transport.requests[0]
    assert get_req.method == "GET"
    put_req = transport.requests[1]
    assert put_req.method == "PUT"
    body = json.loads(put_req.content)
    assert body["_rev"] == "3-old"


def test_cloudant_put_sem_rev_doc_novo(monkeypatch):
    """put sem rev= em documento inexistente: GET devolve 404, PUT sem _rev."""
    store, transport = _setup(monkeypatch, [
        _json_resp(404, {"error": "not_found"}),     # GET
        _json_resp(201, {"ok": True, "rev": "1-new"}),  # PUT
    ])

    asyncio.run(store.put("meu-db", "novo-doc", {"dado": "x"}))

    assert len(transport.requests) == 2
    put_req = transport.requests[1]
    assert put_req.method == "PUT"
    body = json.loads(put_req.content)
    assert "_rev" not in body


def test_cloudant_put_conflito_409_com_rev_levanta_service_error(monkeypatch):
    """Quando PUT com rev= retorna 409, CloudantStore levanta ServiceError CONFLICT."""
    store, transport = _setup(monkeypatch, [_json_resp(409, {"error": "conflict"})])

    with pytest.raises(ServiceError) as exc_info:
        asyncio.run(store.put("meu-db", "doc1", {"dado": "x"}, rev="1-abc"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CONFLICT"


# ---------------------------------------------------------------------------
# CloudantStore.find
# ---------------------------------------------------------------------------


def test_cloudant_find_devolve_lista_de_docs(monkeypatch):
    docs = [{"_id": "d1", "tipo": "x"}, {"_id": "d2", "tipo": "x"}]
    store, transport = _setup(monkeypatch, [_json_resp(200, {"docs": docs})])

    resultado = asyncio.run(store.find("meu-db", {"tipo": "x"}, limit=5))

    assert resultado == docs
    req = transport.requests[0]
    assert req.method == "POST"
    assert str(req.url).endswith(f"/{quote('meu-db', safe='')}/_find")
    body = json.loads(req.content)
    assert body["selector"] == {"tipo": "x"}
    assert body["limit"] == 5


def test_cloudant_find_sem_docs_devolve_lista_vazia(monkeypatch):
    store, transport = _setup(monkeypatch, [_json_resp(200, {"docs": []})])

    resultado = asyncio.run(store.find("meu-db", {"tipo": "inexistente"}))

    assert resultado == []
    req = transport.requests[0]
    body = json.loads(req.content)
    assert body["selector"] == {"tipo": "inexistente"}


# ---------------------------------------------------------------------------
# CloudantStore.delete
# ---------------------------------------------------------------------------


def test_cloudant_delete_doc_existente_retorna_true(monkeypatch):
    doc = {"_id": "doc1", "_rev": "1-abc"}
    store, transport = _setup(monkeypatch, [
        _json_resp(200, doc),
        _json_resp(200, {"ok": True}),
    ])

    resultado = asyncio.run(store.delete("meu-db", "doc1"))

    assert resultado is True
    get_req = transport.requests[0]
    assert get_req.method == "GET"
    del_req = transport.requests[1]
    assert del_req.method == "DELETE"
    assert del_req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert del_req.url.params["rev"] == "1-abc"


def test_cloudant_delete_doc_inexistente_retorna_false(monkeypatch):
    store, transport = _setup(monkeypatch, [_json_resp(404, {"error": "not_found"})])

    resultado = asyncio.run(store.delete("meu-db", "nao-existe"))

    assert resultado is False


# ---------------------------------------------------------------------------
# Erro de credencial ausente — _request levanta ServiceError via get_iam_token
# ---------------------------------------------------------------------------


def test_cloudant_sem_apikey_levanta_503(monkeypatch):
    """CloudantStore construído com apikey vazia: get_iam_token levanta 503."""
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
# get_store — singleton lazy
# ---------------------------------------------------------------------------


def test_get_store_singleton_lazy(monkeypatch):
    """get_store() constrói o store na primeira chamada e devolve o mesmo objeto."""
    import src.storage.store as _store_mod

    monkeypatch.setattr(_store_mod, "_store", None)
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
