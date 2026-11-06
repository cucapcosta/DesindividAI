"""
Endpoints de consentimento Open Finance.
Responsável: M1

Rotas finas — regras de negócio em src/services/consent_service.py.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from src.api.models import ConsentRequest, ConsentResponse, ConsentStatusResponse, ErrorResponse
from src.core.errors import ServiceError
from src.services import consent_service
from src.storage.store import DocumentStore, get_store

router = APIRouter(prefix="/consent", tags=["Consent"])


def _error(exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(code=exc.code, message=exc.message).model_dump(mode="json"),
    )


@router.post(
    "",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar consentimento Open Finance",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
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
    try:
        return await consent_service.create_consent(store, body)
    except ServiceError as exc:
        return _error(exc)


# /callback deve ser declarado antes de /{consent_id} para o FastAPI não casar "callback" como UUID.
@router.get(
    "/callback",
    response_model=ConsentStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Callback OAuth: transiciona AWAITING_AUTHORISATION → AUTHORISED ou REJECTED",
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
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
    No modo sandbox a transição ocorre automaticamente.

    Limitação: a troca code → token (PKCE) é responsabilidade do IBM API Connect;
    o backend valida apenas o state e persiste o novo status.
    """
    try:
        data = await consent_service.authorise_callback(store, state, code, error)
        return ConsentStatusResponse(**data)
    except ServiceError as exc:
        return _error(exc)


@router.get(
    "/{consent_id}",
    response_model=ConsentStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Status do consentimento",
    responses={
        404: {"model": ErrorResponse},
    },
)
async def get_consent_status(
    consent_id: UUID,
    store: DocumentStore = Depends(get_store),
) -> ConsentStatusResponse:
    """Retorna o status atual do consentimento para o canal acompanhar."""
    try:
        data = await consent_service.get_consent_status(store, str(consent_id))
        return ConsentStatusResponse(**data)
    except ServiceError as exc:
        return _error(exc)


@router.delete(
    "/{consent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revogar consentimento",
    responses={
        404: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
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
    try:
        await consent_service.revoke_consent(store, str(consent_id))
    except ServiceError as exc:
        return _error(exc)
