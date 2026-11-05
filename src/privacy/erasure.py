"""
Apagamento de titular — M4, Fase 7 (LGPD Art. 18).

erase_subject() implementa o passo 3c do M1 (docs/m1-consent-lifecycle.md):
  a. Evento consent.erasure_requested
  b. Revogar todos os consentimentos não-REVOKED do titular
  c. Apagar todos os snapshots do titular
  d. Crypto-shredding do cofre de identidade
  e. Evento erasure.completed

Idempotente: segunda chamada retorna contagens zero.
Nunca loga CPF.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class ErasureReport:
    consents_revoked: int
    snapshots_deleted: int
    vault_shredded: bool
    trace_id: str


def _utcnow_iso(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def erase_subject(
    pseudonym_id: str,
    *,
    store,
    vault,
    audit,
    now: Optional[datetime] = None,
) -> ErasureReport:
    """
    Apaga todos os dados do titular identificado por pseudonym_id.

    Parâmetros:
        pseudonym_id — ID pseudonimizado do titular (HMAC-SHA256).
        store        — RetentionStore: iter_consents, update_consent, iter_snapshots,
                       delete_snapshot.
        vault        — IdentityVault: shred(pseudonym_id) → bool.
        audit        — AuditLogger: record_event(...) → trace_id.
        now          — instante de referência; padrão: utcnow().

    Retorna ErasureReport com as contagens e o trace_id do evento inicial.
    """
    now_iso = _utcnow_iso(now)

    # (a) Evento de início
    trace_id: str = audit.record_event(
        "consent.erasure_requested",
        pseudonym_id=pseudonym_id,
        actor="internal_api",
    )

    # (b) Revogar consentimentos não-REVOKED
    consents_revoked = 0
    for doc in store.iter_consents({"pseudonym_id": pseudonym_id}):
        if doc.get("status") == "REVOKED":
            continue
        doc["status"] = "REVOKED"
        doc["revocation_reason"] = "LGPD_ERASURE"
        doc["revoked_at"] = now_iso
        store.update_consent(doc)
        consents_revoked += 1

    # (c) Apagar snapshots
    snapshots_deleted = 0
    for doc in store.iter_snapshots({"pseudonym_id": pseudonym_id}):
        store.delete_snapshot(doc)
        snapshots_deleted += 1

    # (d) Crypto-shredding do cofre
    vault_shredded: bool = vault.shred(pseudonym_id)

    # (e) Evento de conclusão
    audit.record_event(
        "erasure.completed",
        pseudonym_id=pseudonym_id,
        actor="internal_api",
        consents_revoked=consents_revoked,
        snapshots_deleted=snapshots_deleted,
        vault_shredded=vault_shredded,
        request_trace_id=trace_id,
    )

    return ErasureReport(
        consents_revoked=consents_revoked,
        snapshots_deleted=snapshots_deleted,
        vault_shredded=vault_shredded,
        trace_id=trace_id,
    )
