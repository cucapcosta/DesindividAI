"""
Endpoint de recomendação em linguagem simples.
Responsável: M3

Implementar:
    - Agente 2 (LangChain + Granite via watsonx.ai)
    - Tool run_simulation → consome resultado do Motor de Simulação
    - Registro de trace no watsonx.governance para auditoria LGPD
    - Ver Sub-Tarefa 3 no PLANO.md
"""

from fastapi import APIRouter, status

from src.api.models import RecommendationRequest, RecommendationResponse, ErrorResponse

router = APIRouter(prefix="/recommendation", tags=["Recommendation"])


@router.post(
    "",
    response_model=RecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="Obter recomendação de quitação em linguagem simples",
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        503: {"model": ErrorResponse, "description": "LLM ou Motor de Simulação indisponível"},
    },
)
async def get_recommendation(body: RecommendationRequest) -> RecommendationResponse:
    """
    Aciona o Agente 2 que analisa os cenários e gera recomendação
    em linguagem simples usando Granite. Toda decisão é registrada
    no watsonx.governance para auditoria LGPD.

    TODO (M3):
        1. Buscar simulation_id no Cloudant → recuperar os 3 cenários
        2. Invocar Agente 2 (LangChain AgentExecutor):
               a. tool run_simulation(scenarios) → seleciona melhor cenário
               b. Granite gera explanation em linguagem simples/técnica
                  conforme body.language_level
        3. Registrar trace no watsonx.governance (AI Factsheet)
        4. Retornar RecommendationResponse com trace_id para auditoria
    """
    raise NotImplementedError
