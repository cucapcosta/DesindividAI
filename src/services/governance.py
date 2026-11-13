"""
Trace de auditoria de cada recomendação (LGPD / rastreabilidade de decisões de IA).

Persistimos o trace completo (prompt, saída, modelo, regra de seleção) e devolvemos o trace_id.
Integração com watsonx.governance (AI Factsheets) é um próximo passo: o documento aqui é o
payload que seria enviado, e o trace_id é a chave de correlação.
"""

from __future__ import annotations

from uuid import uuid4

from src.core.config import env
from src.privacy.audit_bridge import emit, guard_or_raise, sha256_text
from src.services.simulation_service import decisions_db, now_iso
from src.storage.store import DocumentStore


async def record_trace(store: DocumentStore, **payload) -> str:
    trace_id = f"trace-{uuid4()}"

    # Calcular hashes do prompt e output (se presentes) antes de montar o doc
    prompt_text: str = payload.get("prompt", "") or ""
    output_text: str = payload.get("output", "") or ""
    prompt_sha256 = sha256_text(prompt_text) if prompt_text else None
    output_sha256 = sha256_text(output_text) if output_text else None

    doc = {
        "type": "trace",
        "trace_id": trace_id,
        "model_id": env("WATSONX_MODEL_ID", "ibm/granite-4-h-small"),
        "selection_rule": "min(total_interest_paid), desempate por total_months, entre cenários que quitam",
        "recorded_at": now_iso(),
        **payload,
    }
    if prompt_sha256 is not None:
        doc["prompt_sha256"] = prompt_sha256
    if output_sha256 is not None:
        doc["output_sha256"] = output_sha256

    await guard_or_raise(doc, context="agent2.recommendation_generated")
    await store.put(decisions_db(), trace_id, doc)
    await emit(
        "agent2.recommendation_generated",
        pseudonym_id=payload.get("pseudonym_id"),
        actor="agent2",
        decision_trace_id=trace_id,
        simulation_id=payload.get("simulation_id"),
        recommended_scenario=payload.get("recommended_scenario"),
        model_id=env("WATSONX_MODEL_ID", "ibm/granite-4-h-small"),
        prompt_sha256=prompt_sha256,
        output_sha256=output_sha256,
    )
    return trace_id
