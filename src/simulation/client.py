"""Cliente do Motor de Simulação: AI Service no watsonx.ai se configurado, senão execução local."""

from __future__ import annotations

import asyncio
import httpx

from src.core.config import env, env_float
from src.core.errors import ServiceError
from src.core.ibm_auth import get_iam_token
from src.simulation.engine import simulate


async def run_engine(debts: list[dict], monthly_budget: float) -> list[dict]:
    bb_cet = env_float("BB_CONSOLIDATION_CET_ANNUAL", 30.0)
    url = env("WATSONX_SIMULATION_URL")
    if not url:
        try:
            return await asyncio.to_thread(simulate, debts, monthly_budget, bb_cet)
        except ValueError as exc:
            raise ServiceError(400, "INVALID_INPUT", str(exc)) from exc

    token = await get_iam_token(env("IBM_CLOUD_API_KEY"))
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{url.rstrip('/')}/predict",
                json={"debts": debts, "monthly_budget": monthly_budget, "bb_cet_annual": bb_cet},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ServiceError(503, "SERVICE_UNAVAILABLE", "Motor de Simulação indisponível.") from exc
    body = resp.json()
    if "scenarios" not in body:
        raise ServiceError(400, "INVALID_INPUT", body.get("message", "Resposta inválida do Motor de Simulação."))
    return body["scenarios"]
