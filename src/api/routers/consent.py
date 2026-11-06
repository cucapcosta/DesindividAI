"""
Endpoints de consentimento Open Finance.
Responsável: M1

Rotas finas — regras de negócio em src/services/consent_service.py.
ServiceError levanta para o handler global em main.py.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.api.models import ConsentRequest, ConsentResponse, ConsentStatusResponse
from src.services import consent_service
from src.storage.store import DocumentStore, get_store

router = APIRouter(prefix="/consent", tags=["Consent"])


@router.post(
    "",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar consentimento Open Finance",
    responses={
        400: {"description": "INVALID_REQUEST | INVALID_CPF | INVALID_SCOPE"},
        401: {"description": "Token ausente ou inválido"},
        429: {"description": "Rate limit excedido"},
        503: {"description": "SERVICE_UNAVAILABLE | NOT_CONFIGURED"},
    },
)
async def create_consent(
    body: ConsentRequest,
    store: DocumentStore = Depends(get_store),
) -> ConsentResponse:
    """
    Inicia o fluxo de consentimento OAuth2/Open Finance (FAPI 1.0 — modo sandbox).
    Retorna redirect_url para o cliente autorizar no portal da instituição.
    """
    return await consent_service.create_consent(store, body)


# /callback deve ser declarado antes de /{consent_id} para o FastAPI não casar "callback" como UUID.
@router.get(
    "/callback",
    response_model=ConsentStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Callback OAuth: transiciona AWAITING_AUTHORISATION → AUTHORISED ou REJECTED",
    responses={
        400: {"description": "INVALID_REQUEST"},
        403: {"description": "CONSENT_AUTHORISATION_EXPIRED | CONSENT_REJECTED"},
        404: {"description": "CONSENT_NOT_FOUND"},
        409: {"description": "CONSENT_INVALID_STATE"},
        503: {"description": "SERVICE_UNAVAILABLE"},
    },
)
async def consent_callback(
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    store: DocumentStore = Depends(get_store),
) -> ConsentStatusResponse:
    """
    Recebe o retorno do Authorization Server com code (aprovado) ou error (negado).
    No modo sandbox (OPEN_FINANCE_AUTH_SANDBOX=true) a transição ocorre automaticamente.

    Limitação: a troca code→token (PKCE) e a introspecção são responsabilidade do IBM API
    Connect. O backend valida apenas o state e persiste o novo status.
    O state é de uso único: após autorização bem-sucedida, uma segunda chamada com o
    mesmo state retorna 404 CONSENT_NOT_FOUND.
    """
    return await consent_service.authorise_callback(store, state, code, error)


@router.get(
    "/{consent_id}",
    response_model=ConsentStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Status do consentimento",
    responses={
        400: {"description": "INVALID_REQUEST"},
        404: {"description": "CONSENT_NOT_FOUND"},
        503: {"description": "SERVICE_UNAVAILABLE"},
    },
)
async def get_consent_status(
    consent_id: UUID,
    store: DocumentStore = Depends(get_store),
) -> ConsentStatusResponse:
    """Retorna o status atual do consentimento para o canal acompanhar."""
    return await consent_service.get_consent_status(store, str(consent_id))


@router.delete(
    "/{consent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revogar consentimento",
    responses={
        400: {"description": "INVALID_REQUEST"},
        404: {"description": "CONSENT_NOT_FOUND"},
        401: {"description": "Token ausente ou inválido"},
        503: {"description": "SERVICE_UNAVAILABLE"},
    },
)
async def revoke_consent(
    consent_id: UUID,
    store: DocumentStore = Depends(get_store),
) -> None:
    """
    Revoga o consentimento conforme LGPD Art. 8º §5 — sem ônus ao titular.
    Não aciona /internal/erase: os dados são retidos por 90 dias (política M4).
    """
    await consent_service.revoke_consent(store, str(consent_id))
