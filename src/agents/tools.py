"""Tools do Agente 2."""

from __future__ import annotations

from uuid import UUID

from langchain_core.tools import tool

from src.services.simulation_service import create_simulation
from src.storage.store import get_store


@tool
async def run_simulation(snapshot_id: str, monthly_budget: float) -> dict:
    """Executa o Motor de Simulação (AI Service) para um snapshot financeiro e um orçamento mensal.
    Retorna os 3 cenários (aggressive, consolidation, minimum_viable) e persiste a simulação."""
    result = await create_simulation(get_store(), UUID(snapshot_id), monthly_budget)
    return result.model_dump(mode="json")


AGENT2_TOOLS = [run_simulation]
