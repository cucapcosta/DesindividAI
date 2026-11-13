"""
Endpoint de recomendação em linguagem simples (Sub-Tarefa 3 — M3).

Fluxo: simulação armazenada -> Agente 2 (LangChain + Granite) -> trace de governança.
Ver docs/m3-agent2-recommendation.md.
"""

from fastapi import APIRouter, Depends, status
from langchain_core.runnables import Runnable

from src.agents.recommendation_agent import get_llm
from src.api.models import RecommendationRequest, RecommendationResponse, ErrorResponse
from src.services.recommendation_service import create_recommendation
from src.storage.store import DocumentStore, get_store

router = APIRouter(prefix="/recommendation", tags=["Recommendation"])


@router.post(
    "",
    response_model=RecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="Obter recomendação de quitação em linguagem simples",
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse, "description": "LLM ou Motor de Simulação indisponível"},
    },
)
async def get_recommendation(
    body: RecommendationRequest,
    store: DocumentStore = Depends(get_store),
    llm: Runnable = Depends(get_llm),
) -> RecommendationResponse:
    """Escolhe o cenário (regra determinística) e o Granite explica; registra trace para auditoria LGPD."""
    return await create_recommendation(store, llm, body.simulation_id, body.language_level)
