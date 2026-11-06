"""Caso de uso de POST /simulation: snapshot (Agente 1) -> Motor de Simulação -> persistência."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.api.models import SimulationResponse
from src.core.config import consents_db, decisions_db, env_bool, snapshots_db  # noqa: F401  (reexportados)
from src.core.dates import parse_utc
from src.core.errors import ServiceError
from src.privacy.audit_bridge import emit, guard_or_raise
from src.simulation.client import run_engine
from src.storage.store import DocumentStore

# Retenção padrão para documentos de decisão sem expires_at explícito (LGPD Art. 15/16).
DECISION_RETENTION_DAYS = 90


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def load_snapshot(store: DocumentStore, snapshot_id: UUID) -> dict:
    snapshot = await store.get(snapshots_db(), str(snapshot_id))
    if not snapshot:
        raise ServiceError(404, "SNAPSHOT_NOT_FOUND", "Snapshot financeiro não encontrado.")
    # expires_at = fim do consentimento + retenção (Agente 1). A remoção física do documento pode
    # atrasar; enquanto isso o dado vencido não deve ser usado.
    if snapshot.get("expires_at"):
        try:
            expired = parse_utc(snapshot["expires_at"]) <= datetime.now(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise ServiceError(422, "SNAPSHOT_INVALID", "Snapshot com data de expiração inválida.") from exc
        if expired:
            raise ServiceError(404, "SNAPSHOT_NOT_FOUND", "Snapshot financeiro expirado.")
    return snapshot


async def ensure_consent(store: DocumentStore, *, consent_id: str | None, pseudonym_id: str, scope: str) -> None:
    """
    Exige consentimento vigente (AUTHORISED, com o escopo do endpoint e não expirado), mesma regra do
    GET /debts. Só é imposto com REQUIRE_CONSENT=true: o /consent já persiste consentimentos, mas
    ligar a exigência por padrão é decisão do time (snapshots antigos e ambiente de demonstração).

    O consentimento é o do próprio snapshot (consent_id): se o titular revogar depois da coleta, a
    simulação e a recomendação deixam de ser servidas. Snapshots sem consent_id (anteriores ao
    Agente 1) caem na busca por titular.
    """
    if not env_bool("REQUIRE_CONSENT"):
        return
    if consent_id:
        found = await store.find(consents_db(), {"consent_id": consent_id}, limit=1)
    else:
        found = await store.find(consents_db(), {"pseudonym_id": pseudonym_id, "status": "AUTHORISED"}, limit=1)
    if not found:
        raise ServiceError(403, "CONSENT_REQUIRED", "Não há consentimento ativo para este titular.")
    consent = found[0]
    if consent.get("pseudonym_id") and pseudonym_id and consent["pseudonym_id"] != pseudonym_id:
        raise ServiceError(403, "CONSENT_REQUIRED", "O consentimento não pertence ao titular do snapshot.")
    if consent.get("status") != "AUTHORISED":
        raise ServiceError(403, "CONSENT_REQUIRED", f"Consentimento com status {consent.get('status')}.")
    if scope not in (consent.get("scopes") or []):
        raise ServiceError(403, "INSUFFICIENT_SCOPE", f"Consentimento não inclui o escopo {scope}.")
    try:
        expired = parse_utc(consent["expires_at"]) <= datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError(403, "CONSENT_REQUIRED", "Consentimento sem data de expiração válida.") from exc
    if expired:
        raise ServiceError(403, "CONSENT_EXPIRED", "Consentimento expirado.")


async def create_simulation(store: DocumentStore, snapshot_id: UUID, monthly_budget: float) -> SimulationResponse:
    snapshot = await load_snapshot(store, snapshot_id)
    await ensure_consent(
        store, consent_id=snapshot.get("consent_id"),
        pseudonym_id=snapshot.get("pseudonym_id", ""), scope="simulation:read",
    )

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
    # expires_at: herdar do snapshot; se ausente (snapshot legado), agora + 90 dias
    snap_expires = snapshot.get("expires_at")
    if snap_expires:
        expires_at = snap_expires
    else:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=DECISION_RETENTION_DAYS)).isoformat()

    pseudonym_id = snapshot.get("pseudonym_id")
    consent_id = snapshot.get("consent_id")
    doc = {
        "type": "simulation",
        "pseudonym_id": pseudonym_id,
        "consent_id": consent_id,
        "monthly_budget": monthly_budget,
        "expires_at": expires_at,
        **response.model_dump(mode="json"),
    }
    await guard_or_raise(doc, context="agent2.simulation_created")
    await store.put(decisions_db(), f"simulation:{response.simulation_id}", doc)
    await emit(
        "agent2.simulation_created",
        pseudonym_id=pseudonym_id,
        actor="agent2",
        simulation_id=str(response.simulation_id),
        snapshot_id=str(snapshot_id),
        consent_id=consent_id,
    )
    return response
