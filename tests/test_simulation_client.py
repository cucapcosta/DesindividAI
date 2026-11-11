"""Testes unitários para src/simulation/client.py — sem rede real."""

from __future__ import annotations

import json
import pytest
import httpx

from src.core.errors import ServiceError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_transport(status: int, body: dict) -> httpx.MockTransport:
    """MockTransport que devolve sempre o mesmo JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def _error_transport(exc: Exception) -> httpx.MockTransport:
    """MockTransport que levanta uma exceção HTTP."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Caminho local (sem WATSONX_SIMULATION_URL)
# ---------------------------------------------------------------------------

class TestLocalPath:
    """Testes do caminho local (engine roda in-process)."""

    def test_local_path_returns_scenarios(self, monkeypatch):
        """Sem URL configurada, delega para engine.simulate e retorna cenários."""
        import src.simulation.client as module

        monkeypatch.setenv("WATSONX_SIMULATION_URL", "")
        fake_result = [{"scenario": "aggressive"}]
        monkeypatch.setattr(module, "simulate", lambda debts, budget, cet: fake_result)

        import asyncio
        result = asyncio.run(module.run_engine([{"debt_id": "x"}], 1000.0))
        assert result == fake_result

    def test_local_path_value_error_raises_service_error_400(self, monkeypatch):
        """ValueError do engine vira ServiceError 400."""
        import src.simulation.client as module

        monkeypatch.setenv("WATSONX_SIMULATION_URL", "")

        def boom(debts, budget, cet):
            raise ValueError("orçamento inválido")

        monkeypatch.setattr(module, "simulate", boom)

        import asyncio
        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(module.run_engine([], 0.0))

        err = exc_info.value
        assert err.status_code == 400
        assert err.code == "INVALID_INPUT"
        assert "orçamento inválido" in err.message


# ---------------------------------------------------------------------------
# Caminho remoto (com WATSONX_SIMULATION_URL)
# ---------------------------------------------------------------------------

class TestRemotePath:
    """Testes do caminho remoto (HTTP para watsonx AI Service)."""

    def _patch_client(self, monkeypatch, transport: httpx.MockTransport):
        """Patch httpx.AsyncClient para usar o transport falso."""
        import src.simulation.client as module

        original_init = httpx.AsyncClient.__init__

        class FakeAsyncClient(httpx.AsyncClient):
            def __init__(self, **kwargs):
                kwargs.pop("transport", None)
                super().__init__(transport=transport, **kwargs)

        monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)

    def test_remote_200_with_scenarios(self, monkeypatch):
        """Resposta 200 com 'scenarios' retorna a lista."""
        import src.simulation.client as module

        monkeypatch.setenv("WATSONX_SIMULATION_URL", "https://fake.watsonx.ai")
        monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
        # Stub get_iam_token para não chamar IAM real
        async def fake_token(key): return "fake-bearer"
        monkeypatch.setattr(module, "get_iam_token", fake_token)

        scenarios = [{"scenario": "aggressive"}, {"scenario": "consolidation"}]
        self._patch_client(monkeypatch, _json_transport(200, {"scenarios": scenarios}))

        import asyncio
        result = asyncio.run(module.run_engine([{"debt_id": "x"}], 1000.0))
        assert result == scenarios

    def test_remote_missing_scenarios_key_raises_400(self, monkeypatch):
        """Resposta 200 sem chave 'scenarios' -> ServiceError 400."""
        import src.simulation.client as module

        monkeypatch.setenv("WATSONX_SIMULATION_URL", "https://fake.watsonx.ai")
        monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
        async def fake_token(key): return "fake-bearer"
        monkeypatch.setattr(module, "get_iam_token", fake_token)

        self._patch_client(monkeypatch, _json_transport(200, {"message": "campo ausente"}))

        import asyncio
        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(module.run_engine([{"debt_id": "x"}], 1000.0))

        err = exc_info.value
        assert err.status_code == 400
        assert err.code == "INVALID_INPUT"

    def test_remote_http_error_raises_503(self, monkeypatch):
        """Erro HTTP do servidor -> ServiceError 503."""
        import src.simulation.client as module

        monkeypatch.setenv("WATSONX_SIMULATION_URL", "https://fake.watsonx.ai")
        monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
        async def fake_token(key): return "fake-bearer"
        monkeypatch.setattr(module, "get_iam_token", fake_token)

        self._patch_client(
            monkeypatch,
            _error_transport(httpx.ConnectError("timeout")),
        )

        import asyncio
        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(module.run_engine([{"debt_id": "x"}], 1000.0))

        err = exc_info.value
        assert err.status_code == 503
        assert err.code == "SERVICE_UNAVAILABLE"
