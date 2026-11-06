"""
Autenticação do canal nas rotas de consentimento (M1), enquanto o API Connect não existe.

O canal (app/portal BB) chama POST/GET/DELETE /consent com um token IAM do IBM Cloud,
obtido com a API key de um Service ID do canal. O backend confere a assinatura pelas chaves
públicas do IAM (JWKS), o emissor, a validade e se o `iam_id` está em CHANNEL_IAM_IDS.

Exigido por padrão com APP_ENV=production; fora dela, só com REQUIRE_CHANNEL_AUTH=true.
Quando o API Connect existir, ele valida na borda e esta checagem vira defesa em profundidade.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx
import jwt
from fastapi import Header

from src.core.config import env, env_bool
from src.core.errors import ServiceError

JWKS_URL = "https://iam.cloud.ibm.com/identity/keys"
ISSUER = "https://iam.cloud.ibm.com/identity"
_JWKS_TTL_S = 3600
_JWKS_MIN_REFRESH_S = 60

_jwks_cache: dict[str, Any] = {"keys": [], "fetched_at": 0.0}


def _required() -> bool:
    return env_bool("REQUIRE_CHANNEL_AUTH", default=env("APP_ENV").lower() == "production")


def _unauthorized(message: str) -> ServiceError:
    return ServiceError(401, "UNAUTHORIZED", message)


async def _fetch_jwks() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(JWKS_URL)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ServiceError(503, "SERVICE_UNAVAILABLE", "Chaves públicas do IAM indisponíveis.") from exc
    return resp.json()["keys"]


def _cached_jwk(kid: Optional[str]) -> Optional[dict]:
    return next((k for k in _jwks_cache["keys"] if k.get("kid") == kid), None)


async def _signing_key(kid: Optional[str]) -> Any:
    """
    Chave pública do IAM para o `kid`. O JWKS fica em cache por uma hora e é relido se o
    `kid` for desconhecido (rotação de chave), no máximo uma vez por minuto.
    """
    age = time.time() - _jwks_cache["fetched_at"]
    jwk = _cached_jwk(kid) if age <= _JWKS_TTL_S else None
    if jwk is None and age > _JWKS_MIN_REFRESH_S:
        _jwks_cache.update(keys=await _fetch_jwks(), fetched_at=time.time())
        jwk = _cached_jwk(kid)
    if jwk is None:
        raise _unauthorized("Token assinado por chave desconhecida.")
    return jwt.PyJWK(jwk).key


async def require_channel_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Dependência FastAPI das rotas de consentimento do canal."""
    if not _required():
        return
    allowed = {i.strip() for i in env("CHANNEL_IAM_IDS").split(",") if i.strip()}
    if not allowed:
        raise ServiceError(503, "NOT_CONFIGURED", "Canais autorizados (CHANNEL_IAM_IDS) não configurados.")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("Token Bearer ausente.")
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise _unauthorized("Token malformado.") from exc
    key = await _signing_key(kid)
    try:
        claims = jwt.decode(
            token, key, algorithms=["RS256"], issuer=ISSUER,
            options={"require": ["exp", "iat", "iss"], "verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized("Token inválido ou expirado.") from exc
    if claims.get("iam_id") not in allowed:
        raise _unauthorized("Token não pertence a um canal autorizado.")
