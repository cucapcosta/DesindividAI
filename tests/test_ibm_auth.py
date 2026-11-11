"""Testes unitários para src/core/ibm_auth.py — sem chamada real ao IAM."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qsl

import httpx
import pytest

from src.core.errors import ServiceError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _RecordingTransport(httpx.AsyncBaseTransport):
    """AsyncTransport que grava as requisições recebidas."""

    def __init__(self, status: int, body: dict):
        self.requests: list[httpx.Request] = []
        self._status = status
        self._body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json=self._body)


def _error_transport(exc: Exception) -> httpx.MockTransport:
    """MockTransport que lança uma exceção HTTP."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


def _patch_client(monkeypatch, transport) -> None:
    """Substitui httpx.AsyncClient dentro de ibm_auth pelo client com transport falso."""
    import src.core.ibm_auth as module

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_iam_cache():
    """Garante que o cache em memória está limpo antes de cada teste."""
    import src.core.ibm_auth as module
    module._cache.clear()
    yield
    module._cache.clear()


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

class TestGetIamToken:
    def test_empty_api_key_raises_service_error(self):
        """Sem API key, get_iam_token deve lançar ServiceError 503 imediatamente."""
        import src.core.ibm_auth as module

        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(module.get_iam_token(""))

        err = exc_info.value
        assert err.status_code == 503
        assert err.code == "SERVICE_UNAVAILABLE"

    def test_successful_call_returns_access_token(self, monkeypatch):
        """Resposta 200 do IAM deve retornar o access_token."""
        import src.core.ibm_auth as module

        future_expiry = int(time.time()) + 3600
        transport = _RecordingTransport(
            200, {"access_token": "tok-abc", "expiration": future_expiry}
        )
        _patch_client(monkeypatch, transport)

        token = asyncio.run(module.get_iam_token("my-api-key"))
        assert token == "tok-abc"

    def test_iam_request_carries_apikey_and_grant_type(self, monkeypatch):
        """A requisição ao IAM leva a api key e o grant_type corretos no corpo."""
        import src.core.ibm_auth as module

        future_expiry = int(time.time()) + 3600
        transport = _RecordingTransport(
            200, {"access_token": "tok-check", "expiration": future_expiry}
        )
        _patch_client(monkeypatch, transport)

        asyncio.run(module.get_iam_token("test-api-key-123"))

        assert len(transport.requests) == 1
        req = transport.requests[0]
        assert req.method == "POST"
        assert str(req.url) == module.IAM_URL
        # O corpo é application/x-www-form-urlencoded
        body = dict(parse_qsl(req.content.decode()))
        assert body["apikey"] == "test-api-key-123"
        assert body["grant_type"] == "urn:ibm:params:oauth:grant-type:apikey"

    def test_token_is_cached_after_first_call(self, monkeypatch):
        """Segunda chamada com a mesma key NÃO deve bater no IAM novamente."""
        import src.core.ibm_auth as module

        future_expiry = int(time.time()) + 3600
        transport = _RecordingTransport(
            200, {"access_token": "tok-cached", "expiration": future_expiry}
        )
        _patch_client(monkeypatch, transport)

        t1 = asyncio.run(module.get_iam_token("cached-key"))
        t2 = asyncio.run(module.get_iam_token("cached-key"))

        assert t1 == t2 == "tok-cached"
        assert len(transport.requests) == 1, "IAM foi chamado mais de uma vez apesar do cache"

    def test_expired_cache_refreshes_token(self, monkeypatch):
        """Se a entrada no cache está prestes a expirar (< 60s), deve renovar."""
        import src.core.ibm_auth as module

        near_expiry = time.time() + 30
        module._cache["stale-key"] = ("old-token", near_expiry)

        future_expiry = int(time.time()) + 3600
        transport = _RecordingTransport(
            200, {"access_token": "new-token", "expiration": future_expiry}
        )
        _patch_client(monkeypatch, transport)

        token = asyncio.run(module.get_iam_token("stale-key"))
        assert token == "new-token"

    def test_http_error_raises_service_error_503(self, monkeypatch):
        """Falha de rede ao chamar o IAM deve virar ServiceError 503."""
        import src.core.ibm_auth as module

        _patch_client(
            monkeypatch,
            _error_transport(httpx.ConnectError("connection refused")),
        )

        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(module.get_iam_token("key-with-network-error"))

        err = exc_info.value
        assert err.status_code == 503
        assert err.code == "SERVICE_UNAVAILABLE"

    def test_http_status_error_raises_service_error_503(self, monkeypatch):
        """Resposta 4xx/5xx do IAM deve levantar ServiceError 503."""
        import src.core.ibm_auth as module

        _patch_client(
            monkeypatch,
            _RecordingTransport(401, {"errorCode": "BXNIM0415E", "errorMessage": "bad key"}),
        )

        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(module.get_iam_token("bad-key"))

        err = exc_info.value
        assert err.status_code == 503
        assert err.code == "SERVICE_UNAVAILABLE"
