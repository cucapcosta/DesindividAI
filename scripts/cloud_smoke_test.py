"""
Smoke test do M3 contra a cloud REAL (IAM, Cloudant, watsonx.ai/Granite).

    python -m scripts.cloud_smoke_test

Roda o cloud_setup (idempotente) e depois exercita o fluxo completo. Imprime [OK]/[FALHA] por etapa,
nunca imprime segredos, e sai com código 1 se algo falhar. Escreve documentos de teste no Cloudant
(simulações, traces e o snapshot de exemplo).
"""

from __future__ import annotations

import asyncio
import sys

from fastapi.testclient import TestClient

from scripts.cloud_setup import SAMPLE_SNAPSHOT_ID, cloudant_store, setup
from src.agents.recommendation_agent import build_llm
from src.core.config import env
from src.core.errors import ServiceError
from src.core.ibm_auth import get_iam_token
from src.main import app
from src.services.simulation_service import decisions_db
from src.storage.store import get_store

failures = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global failures
    print(f"[{'OK' if ok else 'FALHA'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures += 1
    return ok


async def cloud_checks() -> None:
    missing = [v for v in ("IBM_CLOUD_API_KEY", "WATSONX_URL", "CLOUDANT_URL", "CLOUDANT_APIKEY") if not env(v)]
    if not check("variáveis obrigatórias no .env", not missing, "faltando: " + ", ".join(missing)):
        return
    check("projeto ou espaço definido", bool(env("WATSONX_PROJECT_ID") or env("WATSONX_SPACE_ID")),
          "defina WATSONX_PROJECT_ID ou WATSONX_SPACE_ID")
    try:
        await get_iam_token(env("IBM_CLOUD_API_KEY"))
        check("token IAM (IBM_CLOUD_API_KEY)", True)
    except ServiceError as exc:
        check("token IAM (IBM_CLOUD_API_KEY)", False, exc.message)
        return
    try:
        await setup(cloudant_store())
        check("Cloudant: databases e snapshot de exemplo", True)
    except Exception as exc:
        check("Cloudant: databases e snapshot de exemplo", False, f"{type(exc).__name__}: {exc}")


def granite_check() -> None:
    # síncrono de propósito: o cliente async do langchain-ibm não deve nascer num loop descartável
    try:
        reply = build_llm().invoke("Responda apenas com a palavra: ok")
        check("Granite responde", bool(str(reply.content).strip()))
    except Exception as exc:
        check("Granite responde", False, f"{type(exc).__name__}: {str(exc)[:300]}")


def api_flow() -> None:
    client = TestClient(app)  # dependências reais: Cloudant + Granite
    sim = client.post("/simulation", json={"snapshot_id": SAMPLE_SNAPSHOT_ID, "monthly_budget": 1500})
    if not check("POST /simulation", sim.status_code == 200, f"{sim.status_code} {sim.text[:200]}"):
        return
    body = sim.json()
    check("3 cenários devolvidos", len(body["scenarios"]) == 3)
    for level in ("simple", "technical"):
        rec = client.post("/recommendation", json={"simulation_id": body["simulation_id"], "language_level": level})
        if not check(f"POST /recommendation ({level})", rec.status_code == 200, f"{rec.status_code} {rec.text[:200]}"):
            continue
        data = rec.json()
        check(f"explicação não vazia ({level})", len(data["explanation"].strip()) > 40)
        trace = asyncio.run(get_store().get(decisions_db(), data["trace_id"]))
        check(f"trace persistido ({level})", bool(trace and trace.get("output")))
        if level == "simple":
            print("\n--- explicação (simple) ---\n" + data["explanation"] + "\n---------------------------\n")


if __name__ == "__main__":
    asyncio.run(cloud_checks())
    if failures == 0:
        granite_check()
    if failures == 0:
        api_flow()
    print(f"\n{'Tudo certo.' if failures == 0 else f'{failures} etapa(s) falharam.'}")
    sys.exit(1 if failures else 0)
