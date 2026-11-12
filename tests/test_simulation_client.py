"""Testes unitários para src/simulation/client.py — sem rede real."""

from __future__ import annotations

import asyncio
import json

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
        captured: list[tuple] = []

        def fake_simulate(debts, budget, cet):
            captured.append((debts, budget, cet))
            return fake_result

        monkeypatch.setattr(module, "simulate", fake_simulate)

        result = asyncio.run(module.run_engine([{"debt_id": "x"}], 1000.0))
        assert result == fake_result
        # Verifica que bb_cet (BB_CONSOLIDATION_CET_ANNUAL) foi repassado ao simulate
        assert len(captured) == 1
        _debts, _budget, cet_arg = captured[0]
        assert isinstance(cet_arg, float)

    def test_local_path_bb_cet_env_repassed(self, monkeypatch):
        """BB_CONSOLIDATION_CET_ANNUAL é lido do ambiente e repassado ao simulate."""
        import src.simulation.client as module

        monkeypatch.setenv("WATSONX_SIMULATION_URL", "")
        monkeypatch.setenv("BB_CONSOLIDATION_CET_ANNUAL", "45.5")
        captured_cet: list[float] = []

        def fake_simulate(debts, budget, cet):
            captured_cet.append(cet)
            return []

        monkeypatch.setattr(module, "simulate", fake_simulate)
        asyncio.run(module.run_engine([], 500.0))

        assert captured_cet == [45.5]

    def test_local_path_value_error_raises_service_error_400(self, monkeypatch):
        """ValueError do engine vira ServiceError 400."""
        import src.simulation.client as module

        monkeypatch.setenv("WATSONX_SIMULATION_URL", "")

        def boom(debts, budget, cet):
            raise ValueError("orçamento inválido")

        monkeypatch.setattr(module, "simulate", boom)

        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(module.run_engine([], 0.0))

        err = exc_info.value
        assert err.status_code == 400
        assert err.code == "INVALID_INPUT"
        assert "orçamento inválido" in err.message


# ---------------------------------------------------------------------------
# Caminho remoto (com WATSONX_SIMULATION_URL)
# ---------------------------------------------------------------------------

REMOTE_URL = "https://fake.watsonx.ai"


class TestRemotePath:
    """Testes do caminho remoto (HTTP para watsonx AI Service)."""

    def _patch_client(self, monkeypatch, transport) -> _RecordingTransport:
        """Patch httpx.AsyncClient para usar o transport fornecido."""
        import src.simulation.client as module

        class FakeAsyncClient(httpx.AsyncClient):
            def __init__(self, **kwargs):
                kwargs.pop("transport", None)
                super().__init__(transport=transport, **kwargs)

        monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
        return transport

    def _common_env(self, monkeypatch):
        monkeypatch.setenv("WATSONX_SIMULATION_URL", REMOTE_URL)
        monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
        import src.simulation.client as module
        async def fake_token(key): return "fake-bearer"
        monkeypatch.setattr(module, "get_iam_token", fake_token)

    def test_remote_200_request_shape(self, monkeypatch):
        """POST /predict recebe Authorization Bearer e corpo com debts, monthly_budget, bb_cet_annual."""
        self._common_env(monkeypatch)
        monkeypatch.setenv("BB_CONSOLIDATION_CET_ANNUAL", "28.0")
        scenarios = [{"scenario": "aggressive"}, {"scenario": "consolidation"}]
        transport = _RecordingTransport(200, {"scenarios": scenarios})
        self._patch_client(monkeypatch, transport)

        result = asyncio.run(
            __import__("src.simulation.client", fromlist=["run_engine"]).run_engine(
                [{"debt_id": "x"}], 1000.0
            )
        )
        assert result == scenarios
        assert len(transport.requests) == 1
        req = transport.requests[0]
        assert req.method == "POST"
        assert str(req.url) == f"{REMOTE_URL}/predict"
        assert req.headers["Authorization"] == "Bearer fake-bearer"
        body = json.loads(req.content)
        assert "debts" in body
        assert "monthly_budget" in body
        assert "bb_cet_annual" in body
        assert body["bb_cet_annual"] == 28.0

    def test_remote_200_with_scenarios(self, monkeypatch):
        """Resposta 200 com 'scenarios' retorna a lista."""
        self._common_env(monkeypatch)
        scenarios = [{"scenario": "aggressive"}, {"scenario": "consolidation"}]
        transport = _RecordingTransport(200, {"scenarios": scenarios})
        self._patch_client(monkeypatch, transport)

        result = asyncio.run(
            __import__("src.simulation.client", fromlist=["run_engine"]).run_engine(
                [{"debt_id": "x"}], 1000.0
            )
        )
        assert result == scenarios

    def test_remote_missing_scenarios_key_raises_400(self, monkeypatch):
        """Resposta 200 sem chave 'scenarios' -> ServiceError 400."""
        self._common_env(monkeypatch)
        transport = _RecordingTransport(200, {"message": "campo ausente"})
        self._patch_client(monkeypatch, transport)

        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(
                __import__("src.simulation.client", fromlist=["run_engine"]).run_engine(
                    [{"debt_id": "x"}], 1000.0
                )
            )

        err = exc_info.value
        assert err.status_code == 400
        assert err.code == "INVALID_INPUT"

    def test_remote_http_error_raises_503(self, monkeypatch):
        """Erro HTTP do servidor -> ServiceError 503."""
        self._common_env(monkeypatch)
        self._patch_client(
            monkeypatch,
            _error_transport(httpx.ConnectError("timeout")),
        )

        with pytest.raises(ServiceError) as exc_info:
            asyncio.run(
                __import__("src.simulation.client", fromlist=["run_engine"]).run_engine(
                    [{"debt_id": "x"}], 1000.0
                )
            )

        err = exc_info.value
        assert err.status_code == 503
        assert err.code == "SERVICE_UNAVAILABLE"
