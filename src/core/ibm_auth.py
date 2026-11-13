"""Troca de API key por token IAM (cache em memória até perto da expiração)."""

import time

import httpx

from src.core.errors import ServiceError

IAM_URL = "https://iam.cloud.ibm.com/identity/token"
_cache: dict[str, tuple[str, float]] = {}


async def get_iam_token(apikey: str) -> str:
    if not apikey:
        raise ServiceError(503, "SERVICE_UNAVAILABLE", "Credencial IBM Cloud não configurada.")
    cached = _cache.get(apikey)
    if cached and cached[1] - 60 > time.time():
        return cached[0]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                IAM_URL,
                data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": apikey},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ServiceError(503, "SERVICE_UNAVAILABLE", "Falha ao autenticar no IBM Cloud IAM.") from exc
    data = resp.json()
    _cache[apikey] = (data["access_token"], float(data["expiration"]))
    return data["access_token"]
