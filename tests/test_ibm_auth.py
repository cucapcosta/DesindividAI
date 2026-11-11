"""Testes unitários para src/core/ibm_auth.py — sem chamada real ao IAM."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from src.core.errors import ServiceError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iam_transport(status: int, body: dict) -> httpx.MockTransport:
    """MockTransport que responde sempre com o JSON fornecido."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def _error_transport(exc: Exception) -> httpx.MockTransport:
    """MockTransport que lança uma exceção HTTP."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


def _patch_client(monkeypatch, transport: httpx.MockTransport) -> None:
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
        _patch_client(
            monkeypatch,
            _iam_transport(200, {"access_token": "tok-abc", "expiration": future_expiry}),
        )

        token = asyncio.run(module.get_iam_token("my-api-key"))
        assert token == "tok-abc"

    def test_token_is_cached_after_first_call(self, monkeypatch):
        """Segunda chamada com a mesma key NÃO deve bater no IAM novamente."""
        import src.core.ibm_auth as module

        call_count = 0
        future_expiry = int(time.time()) + 3600

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200, json={"access_token": "tok-cached", "expiration": future_expiry}
            )

        class FakeAsyncClient(httpx.AsyncClient):
            def __init__(self, **kwargs):
                kwargs.pop("transport", None)
                super().__init__(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)

        t1 = asyncio.run(module.get_iam_token("cached-key"))
        t2 = asyncio.run(module.get_iam_token("cached-key"))

        assert t1 == t2 == "tok-cached"
        assert call_count == 1, "IAM foi chamado mais de uma vez apesar do cache"

    def test_expired_cache_refreshes_token(self, monkeypatch):
        """Se a entrada no cache está prestes a expirar (< 60s), deve renovar."""
        import src.core.ibm_auth as module

        # Popula cache com token quase expirado (expira em 30s)
        near_expiry = time.time() + 30
        module._cache["stale-key"] = ("old-token", near_expiry)

        future_expiry = int(time.time()) + 3600
        _patch_client(
            monkeypatch,
            _iam_transport(200, {"access_token": "new-token", "expiration": future_expiry}),
        )

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
            _iam_transport(401, {"errorCode": "BXNIM0415E", "errorMessage": "bad key"}),
        )

        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(module.get_iam_token("bad-key"))

        err = exc_info.value
        assert err.status_code == 503
        assert err.code == "SERVICE_UNAVAILABLE"
