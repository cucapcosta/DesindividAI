"""
Endpoint de simulação de cenários de quitação (Sub-Tarefa 3 — M3).

Fluxo: snapshot (Agente 1) -> Motor de Simulação (AI Service ou local) -> persistência.
Ver docs/m3-simulation-engine.md.
"""

from fastapi import APIRouter, Depends, status

from src.api.models import SimulationRequest, SimulationResponse, ErrorResponse
from src.services.simulation_service import create_simulation
from src.storage.store import DocumentStore, get_store

router = APIRouter(prefix="/simulation", tags=["Simulation"])


@router.post(
    "",
    response_model=SimulationResponse,
    status_code=status.HTTP_200_OK,
    summary="Simular cenários de quitação",
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def run_simulation(
    body: SimulationRequest, store: DocumentStore = Depends(get_store)
) -> SimulationResponse:
    """Retorna 3 cenários: agressivo, consolidação e mínimo viável."""
    return await create_simulation(store, body.snapshot_id, body.monthly_budget)
