"""
Endpoint de simulação de cenários de quitação.
Responsável: M3

Implementar:
    - Motor de Simulação (AI Service Python no watsonx.ai)
    - 3 cenários: aggressive, consolidation, minimum_viable
    - Ver Sub-Tarefa 3 no PLANO.md
"""

from fastapi import APIRouter, status

from src.api.models import SimulationRequest, SimulationResponse, ErrorResponse

router = APIRouter(prefix="/simulation", tags=["Simulation"])


@router.post(
    "",
    response_model=SimulationResponse,
    status_code=status.HTTP_200_OK,
    summary="Simular cenários de quitação",
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def run_simulation(body: SimulationRequest) -> SimulationResponse:
    """
    Aciona o Motor de Simulação (AI Service Python no watsonx.ai) e retorna
    3 cenários: agressivo, consolidação e mínimo viável.

    TODO (M3):
        1. Buscar snapshot no Cloudant pelo body.snapshot_id
        2. Chamar Motor de Simulação (endpoint REST do AI Service):
               POST <WATSONX_SIMULATION_URL>/predict
               body: { debts: [...], monthly_budget: float }
        3. Mapear resposta do AI Service para SimulationResponse
        4. Persistir simulation_id no Cloudant para o Agente 2 consumir
    """
    raise NotImplementedError
