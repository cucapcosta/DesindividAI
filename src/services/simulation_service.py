"""Caso de uso de POST /simulation: snapshot (Agente 1) -> Motor de Simulação -> persistência."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.api.models import SimulationResponse
from src.core.config import consents_db, decisions_db, env_bool, snapshots_db  # noqa: F401  (reexportados)
from src.core.errors import ServiceError
from src.simulation.client import run_engine
from src.storage.store import DocumentStore


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def load_snapshot(store: DocumentStore, snapshot_id: UUID) -> dict:
    snapshot = await store.get(snapshots_db(), str(snapshot_id))
    if not snapshot:
        raise ServiceError(404, "SNAPSHOT_NOT_FOUND", "Snapshot financeiro não encontrado.")
    return snapshot


async def ensure_consent(store: DocumentStore, pseudonym_id: str) -> None:
    """
    Exige consentimento AUTHORISED para o titular. Só é imposto com REQUIRE_CONSENT=true,
    pois a persistência de consentimentos é da Sub-Tarefa 1 (endpoint /consent ainda stub).
    """
    if not env_bool("REQUIRE_CONSENT"):
        return
    found = await store.find(
        consents_db(),
        {"pseudonym_id": pseudonym_id, "status": "AUTHORISED"},
        limit=1,
    )
    if not found:
        raise ServiceError(403, "CONSENT_REQUIRED", "Não há consentimento ativo para este titular.")


async def create_simulation(store: DocumentStore, snapshot_id: UUID, monthly_budget: float) -> SimulationResponse:
    snapshot = await load_snapshot(store, snapshot_id)
    await ensure_consent(store, snapshot.get("pseudonym_id", ""))

    debts = snapshot.get("debts")
    if not isinstance(debts, list) or not debts:
        raise ServiceError(422, "SNAPSHOT_INVALID", "Snapshot financeiro sem dívidas válidas.")

    scenarios = await run_engine(debts, monthly_budget)
    response = SimulationResponse(
        simulation_id=uuid4(),
        snapshot_id=snapshot_id,
        scenarios=scenarios,
        generated_at=now_iso(),
    )
    await store.put(
        decisions_db(),
        f"simulation:{response.simulation_id}",
        {
            "type": "simulation",
            "pseudonym_id": snapshot.get("pseudonym_id"),
            "monthly_budget": monthly_budget,
            **response.model_dump(mode="json"),
        },
    )
    return response
