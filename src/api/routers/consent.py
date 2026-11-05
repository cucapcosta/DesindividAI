"""
Endpoints de consentimento Open Finance.
Responsável: M1

Implementar:
    - Persistência em Cloudant (consent_registry)
    - Integração com IBM API Connect Native OAuth Provider
    - Geração do pseudonym_id (HMAC-SHA256) — ver docs/m1-consent-lifecycle.md
"""

from uuid import UUID

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from src.api.models import ConsentRequest, ConsentResponse, ErrorResponse

router = APIRouter(prefix="/consent", tags=["Consent"])


@router.post(
    "",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar consentimento Open Finance",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def create_consent(body: ConsentRequest) -> ConsentResponse:
    """
    Inicia o fluxo de consentimento OAuth2/Open Finance (FAPI 1.0).
    Retorna redirect_url para o cliente autorizar no portal da instituição.

    TODO (M1):
        1. pseudonym_id = get_identity_vault().store(body.cpf)
           (get_identity_vault de src.privacy.identity_vault; cifra o CPF com AES-256-GCM
           e retorna o HMAC-SHA256 já existente ou recém-gerado)
        2. Persistir documento em Cloudant → consent_registry
           com status AWAITING_AUTHORISATION
        3. get_audit_logger().record_event(
               "consent.created",            # NOTE: adicionar "consent.created" a
               pseudonym_id=pseudonym_id,    # _ALLOWED_EVENT_TYPES em src/privacy/audit.py
               actor="consent_api",          # quando o M1 implementar este endpoint
               consent_id=consent_id,
           )
        4. Construir redirect_url apontando para o Authorization Server
        5. Retornar ConsentResponse com consent_id e redirect_url
    """
    raise NotImplementedError


@router.delete(
    "/{consent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revogar consentimento",
    responses={
        404: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
)
async def revoke_consent(consent_id: UUID) -> None:
    """
    Revoga o consentimento e invalida os tokens OAuth associados.
    Conforme LGPD Art. 8º §5 — revogação sem ônus ao titular.

    TODO (M1):
        1. Buscar consent_id no Cloudant → consent_registry
        2. Atualizar status → REVOKED, revoked_at = now (ISO UTC),
           revocation_reason = "USER_REQUEST"
        3. Chamar IBM API Connect OAuth Provider: POST /oauth/revoke
        4. get_audit_logger().record_event(
               "consent.revoked",            # NOTE: adicionar "consent.revoked" a
               pseudonym_id=consent["pseudonym_id"],  # _ALLOWED_EVENT_TYPES em audit.py
               actor="consent_api",
               consent_id=str(consent_id),
               revocation_reason="USER_REQUEST",
           )
        5. Apagamento total do titular (LGPD Art. 18): POST /internal/erase/{pseudonym_id}
           (implementado no M4 — ver src/api/routers/internal.py)
        6. Retornar 204 No Content
    """
    raise NotImplementedError
