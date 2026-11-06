"""
Trace de auditoria de cada recomendação (LGPD / rastreabilidade de decisões de IA).

Persistimos o trace completo (prompt, saída, modelo, regra de seleção) e devolvemos o trace_id.
Integração com watsonx.governance (AI Factsheets) é um próximo passo: o documento aqui é o
payload que seria enviado, e o trace_id é a chave de correlação.
"""

from __future__ import annotations

from uuid import uuid4

from src.core.config import env
from src.services.simulation_service import decisions_db, now_iso
from src.storage.store import DocumentStore


async def record_trace(store: DocumentStore, **payload) -> str:
    trace_id = f"trace-{uuid4()}"
    await store.put(
        decisions_db(),
        trace_id,
        {
            "type": "trace",
            "trace_id": trace_id,
            "model_id": env("WATSONX_MODEL_ID", "ibm/granite-4-h-small"),
            "selection_rule": "min(total_interest_paid), desempate por total_months, entre cenários que quitam",
            "recorded_at": now_iso(),
            **payload,
        },
    )
    return trace_id
