"""
Endpoint de dívidas — coleta e priorização por CET.
Responsável: M2

Implementar:
    - Agente 1 (LangChain + Granite via watsonx.ai)
    - Tools: get_open_finance_debts, get_serasa_data, calculate_cet, persist_snapshot
    - Pseudonimização antes de persistir no Cloudant
    - Ver Sub-Tarefa 2 no PLANO.md
"""

from uuid import UUID

from fastapi import APIRouter, status

from api.models import DebtListResponse, ErrorResponse

router = APIRouter(prefix="/debts", tags=["Debts"])


@router.get(
    "",
    response_model=DebtListResponse,
    status_code=status.HTTP_200_OK,
    summary="Listar dívidas consolidadas e priorizadas por CET",
    responses={
        202: {"model": ErrorResponse, "description": "Processamento em andamento"},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_debts(consent_id: UUID) -> DebtListResponse:
    """
    Aciona o Agente 1 que coleta dívidas via Open Finance + Serasa,
    calcula o CET de cada uma e retorna o retrato financeiro priorizado.

    TODO (M2):
        1. Validar que consent_id existe e está AUTHORISED no Cloudant
        2. Invocar Agente 1 (LangChain AgentExecutor):
               a. tool get_open_finance_debts(consent_id)
               b. tool get_serasa_data(pseudonym_id)
               c. tool calculate_cet(debts) → ordena por CET desc
               d. tool persist_snapshot(debts) → salva em Cloudant financial_snapshots
        3. Retornar DebtListResponse com snapshot_id e lista priorizada
    """
    raise NotImplementedError
