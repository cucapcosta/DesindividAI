"""Testes unitários para src/agents/tools.py."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

import src.agents.tools as tools_module
from src.agents.tools import AGENT2_TOOLS, run_simulation
from tests.conftest import SNAPSHOT_ID


# ---------------------------------------------------------------------------
# Fake SimulationResponse
# ---------------------------------------------------------------------------

class _FakeSimulationResponse:
    """Imita SimulationResponse com model_dump(mode='json')."""

    def __init__(self):
        self.last_mode: str | None = None

    def model_dump(self, *, mode: str = "python") -> dict:
        self.last_mode = mode
        return {
            "simulation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "snapshot_id": SNAPSHOT_ID,
            "scenarios": [],
            "generated_at": "2026-01-01T00:00:00+00:00",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_create_simulation(captured: list):
    """Devolve um fake de create_simulation que grava os args em captured."""
    async def fake_create_simulation(store, snapshot_id, monthly_budget):
        resp = _FakeSimulationResponse()
        captured.append({"store": store, "snapshot_id": snapshot_id, "monthly_budget": monthly_budget, "response": resp})
        return resp
    return fake_create_simulation


class _FakeStore:
    pass


# ---------------------------------------------------------------------------
# Testes de estrutura
# ---------------------------------------------------------------------------

def test_agent2_tools_contains_run_simulation():
    assert run_simulation in AGENT2_TOOLS


def test_run_simulation_is_langchain_tool():
    """run_simulation deve ter o protocolo LangChain tool (ainvoke)."""
    assert hasattr(run_simulation, "ainvoke")


# ---------------------------------------------------------------------------
# Testes de comportamento via ainvoke
# ---------------------------------------------------------------------------

def test_run_simulation_returns_model_dump(monkeypatch):
    """ainvoke deve retornar o result de model_dump(mode='json')."""
    captured: list = []
    fake_store = _FakeStore()

    monkeypatch.setattr(tools_module, "create_simulation", _make_fake_create_simulation(captured))
    monkeypatch.setattr(tools_module, "get_store", lambda: fake_store)

    result = asyncio.run(
        run_simulation.ainvoke({"snapshot_id": SNAPSHOT_ID, "monthly_budget": 1500.0})
    )

    assert isinstance(result, dict)
    assert result["snapshot_id"] == SNAPSHOT_ID
    # model_dump deve ter sido chamado com mode="json"
    assert captured[0]["response"].last_mode == "json"


def test_run_simulation_passes_monthly_budget(monkeypatch):
    """O monthly_budget recebido deve ser repassado ao create_simulation."""
    captured: list = []
    monkeypatch.setattr(tools_module, "create_simulation", _make_fake_create_simulation(captured))
    monkeypatch.setattr(tools_module, "get_store", lambda: _FakeStore())

    asyncio.run(
        run_simulation.ainvoke({"snapshot_id": SNAPSHOT_ID, "monthly_budget": 2000.0})
    )

    assert len(captured) == 1
    assert captured[0]["monthly_budget"] == 2000.0


def test_run_simulation_snapshot_id_is_uuid(monkeypatch):
    """O snapshot_id deve ser convertido para UUID antes de passar ao create_simulation."""
    captured: list = []
    monkeypatch.setattr(tools_module, "create_simulation", _make_fake_create_simulation(captured))
    monkeypatch.setattr(tools_module, "get_store", lambda: _FakeStore())

    asyncio.run(
        run_simulation.ainvoke({"snapshot_id": SNAPSHOT_ID, "monthly_budget": 1500.0})
    )

    assert len(captured) == 1
    assert isinstance(captured[0]["snapshot_id"], UUID)
    assert captured[0]["snapshot_id"] == UUID(SNAPSHOT_ID)


def test_run_simulation_passes_store(monkeypatch):
    """O store retornado por get_store deve ser passado ao create_simulation."""
    captured: list = []
    fake_store = _FakeStore()
    monkeypatch.setattr(tools_module, "create_simulation", _make_fake_create_simulation(captured))
    monkeypatch.setattr(tools_module, "get_store", lambda: fake_store)

    asyncio.run(
        run_simulation.ainvoke({"snapshot_id": SNAPSHOT_ID, "monthly_budget": 1500.0})
    )

    assert captured[0]["store"] is fake_store


# ---------------------------------------------------------------------------
# snapshot_id inválido deve levantar erro
# ---------------------------------------------------------------------------

def test_run_simulation_invalid_snapshot_id_raises(monkeypatch):
    """snapshot_id que não é UUID válido deve levantar ValueError."""
    captured: list = []
    monkeypatch.setattr(tools_module, "create_simulation", _make_fake_create_simulation(captured))
    monkeypatch.setattr(tools_module, "get_store", lambda: _FakeStore())

    with pytest.raises(ValueError):
        asyncio.run(
            run_simulation.ainvoke({"snapshot_id": "not-a-uuid", "monthly_budget": 1500.0})
        )

    # create_simulation nunca deve ter sido chamado com um UUID inválido
    assert len(captured) == 0
