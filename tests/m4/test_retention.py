"""
Testes do job de retenção — M4, Fase 6.

Cobertura:
  - Regra a: snapshot expirado → deletado
  - Regra a: snapshot com consentimento revogado cedo → min(expires_at, revoked_at+delta)
  - Regra a: snapshot ligado a consentimento REVOKED que também será apagado na mesma execução
  - Regra b: AWAITING_AUTHORISATION expirado → deletado
  - Regra c: AUTHORISED expirado → REVOKED + consent.expired
  - Regra d: REVOKED há mais de 90 dias → deletado
  - Regra e: cofre sem consentimento nem snapshot → shred
  - Carência de 24h: vault criado há menos de 24h → não shreddar
  - Cofre mantido se ainda houver snapshot ativo
  - dry_run: não altera nada, emite retention.run com dry_run=true
  - Idempotência: segunda execução → tudo zero
  - Data inválida → skipped_invalid
  - Nenhum evento contém CPF
  - CloudantRetentionStore com client fake: paginação por bookmark e delete com _rev
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional
from unittest.mock import MagicMock, call

import pytest

from src.privacy.retention import RetentionReport, run_retention
from src.privacy.retention_store import (
    CloudantRetentionStore,
    InMemoryRetentionStore,
    _matches,
)

# ---------------------------------------------------------------------------
# Constantes de tempo
# ---------------------------------------------------------------------------

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
PAST = NOW - timedelta(days=200)  # bem no passado
RECENT_PAST = NOW - timedelta(hours=1)  # 1h atrás (dentro do prazo)
FAR_FUTURE = NOW + timedelta(days=365)
RETENTION_DAYS = 90
RETENTION_DELTA = timedelta(days=RETENTION_DAYS)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Fake AuditLogger
# ---------------------------------------------------------------------------

class FakeAudit:
    """Coleta eventos sem gravar em nenhum destino real."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def record_event(self, event_type: str, *, pseudonym_id, actor, **fields) -> str:
        trace_id = str(uuid.uuid4())
        self.events.append({
            "event_type": event_type,
            "pseudonym_id": pseudonym_id,
            "actor": actor,
            **fields,
        })
        return trace_id

    def events_of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["event_type"] == event_type]


# ---------------------------------------------------------------------------
# Fake IdentityVault (apenas o método shred)
# ---------------------------------------------------------------------------

class FakeVault:
    def __init__(self, pseudonym_ids: list[str] | None = None) -> None:
        self.shredded: set[str] = set()
        self._alive: set[str] = set(pseudonym_ids or [])

    def shred(self, pseudonym_id: str) -> bool:
        if pseudonym_id in self._alive:
            self._alive.discard(pseudonym_id)
            self.shredded.add(pseudonym_id)
            return True
        return False


# ---------------------------------------------------------------------------
# Helpers para construir documentos
# ---------------------------------------------------------------------------

def make_consent(
    *,
    status: str,
    pseudonym_id: str = "pid-1",
    consent_id: str | None = None,
    expires_at: datetime | None = None,
    awaiting_expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    revocation_reason: str | None = None,
    doc_id: str | None = None,
) -> dict:
    cid = consent_id or str(uuid.uuid4())
    did = doc_id or cid
    doc: dict = {
        "_id": did,
        "consent_id": cid,
        "pseudonym_id": pseudonym_id,
        "status": status,
        "created_at": iso(PAST),
    }
    if expires_at is not None:
        doc["expires_at"] = iso(expires_at)
    if awaiting_expires_at is not None:
        doc["awaiting_expires_at"] = iso(awaiting_expires_at)
    if revoked_at is not None:
        doc["revoked_at"] = iso(revoked_at)
    if revocation_reason is not None:
        doc["revocation_reason"] = revocation_reason
    return doc


def make_snapshot(
    *,
    pseudonym_id: str = "pid-1",
    consent_id: str = "cid-1",
    expires_at: datetime,
    snapshot_id: str | None = None,
) -> dict:
    sid = snapshot_id or str(uuid.uuid4())
    return {
        "_id": sid,
        "snapshot_id": sid,
        "pseudonym_id": pseudonym_id,
        "consent_id": consent_id,
        "expires_at": iso(expires_at),
        "type": "financial_snapshot",
    }


def make_vault_doc(
    *,
    pseudonym_id: str,
    created_at: datetime,
    shredded_at: str | None = None,
) -> dict:
    return {
        "_id": pseudonym_id,
        "type": "identity",
        "created_at": iso(created_at),
        "shredded_at": shredded_at,
    }


# ---------------------------------------------------------------------------
# Regra a — snapshot expirado simples
# ---------------------------------------------------------------------------

def test_rule_a_snapshot_expired():
    """Snapshot com expires_at no passado deve ser deletado."""
    snap = make_snapshot(expires_at=PAST)
    store = InMemoryRetentionStore(snapshots=[snap])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.snapshots_deleted == 1
    # Snapshot deve ter sido removido
    assert list(store.iter_snapshots({})) == []
    # Deve ter emitido retention.run
    assert len(audit.events_of_type("retention.run")) == 1


def test_rule_a_snapshot_not_expired():
    """Snapshot com expires_at no futuro NÃO deve ser deletado."""
    snap = make_snapshot(expires_at=FAR_FUTURE)
    store = InMemoryRetentionStore(snapshots=[snap])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.snapshots_deleted == 0
    assert len(list(store.iter_snapshots({}))) == 1


# ---------------------------------------------------------------------------
# Regra a — snapshot com consentimento revogado antes de expires_at
# ---------------------------------------------------------------------------

def test_rule_a_snapshot_consent_revoked_early():
    """
    Consentimento REVOKED com revoked_at cedo:
    limite = min(expires_at, revoked_at + RETENTION_DELTA)
    Se revoked_at + 90d < now → snapshot apagado mesmo com expires_at no futuro.
    """
    consent_id = "cid-rev-early"
    # Consentimento revogado 200 dias atrás, expires_at no futuro
    revoked_at = NOW - timedelta(days=200)
    consent = make_consent(
        status="REVOKED",
        consent_id=consent_id,
        pseudonym_id="pid-rev",
        expires_at=FAR_FUTURE,
        revoked_at=revoked_at,
        revocation_reason="CUSTOMER_REQUEST",
    )
    # Snapshot com expires_at no futuro, mas ligado ao consentimento revogado
    snap = make_snapshot(
        pseudonym_id="pid-rev",
        consent_id=consent_id,
        expires_at=FAR_FUTURE,
    )
    store = InMemoryRetentionStore(consents=[consent], snapshots=[snap])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    # revoked_at + 90d = 200-90=110 dias atrás < now → deletar
    assert report.snapshots_deleted == 1
    assert list(store.iter_snapshots({})) == []


def test_rule_a_snapshot_consent_revoked_recent():
    """
    Consentimento REVOKED com revoked_at recente:
    revoked_at + 90d no futuro → NÃO apagar snapshot ainda.
    """
    consent_id = "cid-rev-recent"
    revoked_at = NOW - timedelta(days=10)  # revogado 10 dias atrás
    consent = make_consent(
        status="REVOKED",
        consent_id=consent_id,
        pseudonym_id="pid-rev2",
        expires_at=FAR_FUTURE,
        revoked_at=revoked_at,
        revocation_reason="CUSTOMER_REQUEST",
    )
    snap = make_snapshot(
        pseudonym_id="pid-rev2",
        consent_id=consent_id,
        expires_at=FAR_FUTURE,
    )
    store = InMemoryRetentionStore(consents=[consent], snapshots=[snap])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.snapshots_deleted == 0
    assert len(list(store.iter_snapshots({}))) == 1


# ---------------------------------------------------------------------------
# Regra a — snapshot ligado a consentimento que será apagado na mesma execução
# ---------------------------------------------------------------------------

def test_rule_a_snapshot_and_revoked_consent_same_run():
    """
    Snapshot ligado a consentimento REVOKED que também será apagado (regra d)
    na mesma execução. O snapshot deve ser apagado pela regra a antes.
    """
    consent_id = "cid-joint"
    revoked_at = NOW - timedelta(days=200)  # > 90 dias
    consent = make_consent(
        status="REVOKED",
        consent_id=consent_id,
        pseudonym_id="pid-joint",
        expires_at=PAST,
        revoked_at=revoked_at,
        revocation_reason="CUSTOMER_REQUEST",
    )
    snap = make_snapshot(
        pseudonym_id="pid-joint",
        consent_id=consent_id,
        expires_at=FAR_FUTURE,  # expires_at no futuro, mas min(fut, revoked+90d) < now
    )
    store = InMemoryRetentionStore(consents=[consent], snapshots=[snap])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.snapshots_deleted == 1
    assert report.consents_revoked_deleted == 1
    assert list(store.iter_snapshots({})) == []
    assert list(store.iter_consents({})) == []


# ---------------------------------------------------------------------------
# Regra b — AWAITING_AUTHORISATION expirado
# ---------------------------------------------------------------------------

def test_rule_b_awaiting_expired():
    """Consentimento AWAITING_AUTHORISATION com awaiting_expires_at < now → deletar."""
    consent = make_consent(
        status="AWAITING_AUTHORISATION",
        awaiting_expires_at=PAST,
    )
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.consents_awaiting_deleted == 1
    assert list(store.iter_consents({})) == []


def test_rule_b_awaiting_not_expired():
    """Consentimento AWAITING_AUTHORISATION com awaiting_expires_at no futuro → manter."""
    consent = make_consent(
        status="AWAITING_AUTHORISATION",
        awaiting_expires_at=FAR_FUTURE,
    )
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.consents_awaiting_deleted == 0
    assert len(list(store.iter_consents({}))) == 1


# ---------------------------------------------------------------------------
# Regra c — AUTHORISED expirado → REVOKED + consent.expired
# ---------------------------------------------------------------------------

def test_rule_c_authorised_expired():
    """Consentimento AUTHORISED com expires_at < now → REVOKED, audit consent.expired."""
    cid = "cid-auth-exp"
    consent = make_consent(
        status="AUTHORISED",
        consent_id=cid,
        pseudonym_id="pid-auth",
        expires_at=PAST,
    )
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.consents_expired == 1

    # Consentimento deve ter sido atualizado para REVOKED
    docs = list(store.iter_consents({}))
    assert len(docs) == 1
    updated = docs[0]
    assert updated["status"] == "REVOKED"
    assert updated["revocation_reason"] == "EXPIRED"
    assert updated["revoked_at"] is not None

    # Evento consent.expired deve ter sido emitido com consent_id e pseudonym_id
    expired_events = audit.events_of_type("consent.expired")
    assert len(expired_events) == 1
    evt = expired_events[0]
    assert evt["pseudonym_id"] == "pid-auth"
    assert evt["actor"] == "retention"
    assert evt["consent_id"] == cid


def test_rule_c_authorised_not_expired():
    """Consentimento AUTHORISED com expires_at no futuro → não tocar."""
    consent = make_consent(
        status="AUTHORISED",
        pseudonym_id="pid-auth2",
        expires_at=FAR_FUTURE,
    )
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.consents_expired == 0
    docs = list(store.iter_consents({}))
    assert docs[0]["status"] == "AUTHORISED"


# ---------------------------------------------------------------------------
# Regra d — REVOKED expirado → deletar
# ---------------------------------------------------------------------------

def test_rule_d_revoked_expired():
    """Consentimento REVOKED com revoked_at + 90d < now → deletar."""
    revoked_at = NOW - timedelta(days=100)  # 100 dias atrás > 90
    consent = make_consent(
        status="REVOKED",
        pseudonym_id="pid-rev3",
        revoked_at=revoked_at,
        revocation_reason="CUSTOMER_REQUEST",
        expires_at=PAST,
    )
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.consents_revoked_deleted == 1
    assert list(store.iter_consents({})) == []


def test_rule_d_revoked_lgpd_erasure():
    """Consentimento REVOKED com reason LGPD_ERASURE também é apagado após o prazo."""
    revoked_at = NOW - timedelta(days=100)
    consent = make_consent(
        status="REVOKED",
        pseudonym_id="pid-lgpd",
        revoked_at=revoked_at,
        revocation_reason="LGPD_ERASURE",
        expires_at=PAST,
    )
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.consents_revoked_deleted == 1
    assert list(store.iter_consents({})) == []


def test_rule_d_revoked_within_retention_period():
    """Consentimento REVOKED com revoked_at + 90d no futuro → manter."""
    revoked_at = NOW - timedelta(days=50)  # 50 dias atrás < 90
    consent = make_consent(
        status="REVOKED",
        pseudonym_id="pid-rev4",
        revoked_at=revoked_at,
        revocation_reason="CUSTOMER_REQUEST",
        expires_at=PAST,
    )
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.consents_revoked_deleted == 0
    assert len(list(store.iter_consents({}))) == 1


# ---------------------------------------------------------------------------
# Regra e — Cofre: sem consentimento nem snapshot → shred
# ---------------------------------------------------------------------------

def test_rule_e_vault_shred_no_consent_no_snapshot():
    """Vault ativo sem consentimento nem snapshot, criado há mais de 24h → shred."""
    pid = "pid-orphan"
    vault_doc = make_vault_doc(
        pseudonym_id=pid,
        created_at=NOW - timedelta(hours=48),  # 48h atrás
    )
    store = InMemoryRetentionStore(vault=[vault_doc])
    audit = FakeAudit()
    vault = FakeVault(pseudonym_ids=[pid])

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.vault_shredded == 1
    assert pid in vault.shredded


def test_rule_e_vault_kept_with_active_consent():
    """Vault ativo com consentimento ativo → NÃO shreddar."""
    pid = "pid-with-consent"
    vault_doc = make_vault_doc(
        pseudonym_id=pid,
        created_at=NOW - timedelta(hours=48),
    )
    consent = make_consent(
        status="AUTHORISED",
        pseudonym_id=pid,
        expires_at=FAR_FUTURE,
    )
    store = InMemoryRetentionStore(consents=[consent], vault=[vault_doc])
    audit = FakeAudit()
    vault = FakeVault(pseudonym_ids=[pid])

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.vault_shredded == 0
    assert pid not in vault.shredded


def test_rule_e_vault_kept_with_active_snapshot():
    """Vault ativo com snapshot ainda vigente → NÃO shreddar."""
    pid = "pid-with-snap"
    vault_doc = make_vault_doc(
        pseudonym_id=pid,
        created_at=NOW - timedelta(hours=48),
    )
    snap = make_snapshot(
        pseudonym_id=pid,
        consent_id="cid-x",
        expires_at=FAR_FUTURE,
    )
    store = InMemoryRetentionStore(snapshots=[snap], vault=[vault_doc])
    audit = FakeAudit()
    vault = FakeVault(pseudonym_ids=[pid])

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.vault_shredded == 0
    assert pid not in vault.shredded


def test_rule_e_vault_carencia_24h():
    """Vault ativo criado há menos de 24h → NÃO shreddar (carência)."""
    pid = "pid-new"
    vault_doc = make_vault_doc(
        pseudonym_id=pid,
        created_at=NOW - timedelta(hours=12),  # só 12h
    )
    store = InMemoryRetentionStore(vault=[vault_doc])
    audit = FakeAudit()
    vault = FakeVault(pseudonym_ids=[pid])

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.vault_shredded == 0
    assert pid not in vault.shredded


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------

def test_dry_run_does_not_modify():
    """dry_run=True não altera nada nos dados."""
    snap = make_snapshot(expires_at=PAST)
    consent_await = make_consent(
        status="AWAITING_AUTHORISATION",
        awaiting_expires_at=PAST,
    )
    consent_auth = make_consent(
        status="AUTHORISED",
        pseudonym_id="pid-dry",
        expires_at=PAST,
    )
    revoked_at = NOW - timedelta(days=100)
    consent_rev = make_consent(
        status="REVOKED",
        pseudonym_id="pid-dry2",
        revoked_at=revoked_at,
        revocation_reason="CUSTOMER_REQUEST",
        expires_at=PAST,
    )
    pid = "pid-orphan-dry"
    vault_doc = make_vault_doc(pseudonym_id=pid, created_at=NOW - timedelta(hours=48))

    store = InMemoryRetentionStore(
        consents=[consent_await, consent_auth, consent_rev],
        snapshots=[snap],
        vault=[vault_doc],
    )
    audit = FakeAudit()
    vault = FakeVault(pseudonym_ids=[pid])

    report = run_retention(
        now=NOW, dry_run=True, store=store, vault=vault, audit=audit.record_event
    )

    assert report.dry_run is True
    # Tudo contado mas não modificado
    assert report.snapshots_deleted == 1
    assert report.consents_awaiting_deleted == 1
    assert report.consents_expired == 1
    assert report.consents_revoked_deleted == 1
    assert report.vault_shredded == 1

    # Nenhum dado alterado
    assert len(list(store.iter_snapshots({}))) == 1
    awaiting_docs = list(store.iter_consents({"status": "AWAITING_AUTHORISATION"}))
    assert len(awaiting_docs) == 1
    auth_docs = list(store.iter_consents({"status": "AUTHORISED"}))
    assert len(auth_docs) == 1
    revoked_docs = list(store.iter_consents({"status": "REVOKED"}))
    assert len(revoked_docs) == 1

    # consent.expired NÃO deve ser emitido em dry_run
    assert audit.events_of_type("consent.expired") == []

    # retention.run deve ser emitido com dry_run=True
    run_events = audit.events_of_type("retention.run")
    assert len(run_events) == 1
    assert run_events[0]["dry_run"] is True

    # Vault não shredado
    assert pid not in vault.shredded


# ---------------------------------------------------------------------------
# Idempotência
# ---------------------------------------------------------------------------

def test_idempotency():
    """Segunda execução com o mesmo now → tudo zero."""
    snap = make_snapshot(expires_at=PAST)
    consent_await = make_consent(
        status="AWAITING_AUTHORISATION",
        awaiting_expires_at=PAST,
    )
    consent_auth = make_consent(
        status="AUTHORISED",
        pseudonym_id="pid-idm",
        expires_at=PAST,
    )
    revoked_at = NOW - timedelta(days=100)
    consent_rev = make_consent(
        status="REVOKED",
        pseudonym_id="pid-idm2",
        revoked_at=revoked_at,
        revocation_reason="CUSTOMER_REQUEST",
        expires_at=PAST,
    )

    store = InMemoryRetentionStore(
        consents=[consent_await, consent_auth, consent_rev],
        snapshots=[snap],
    )
    audit = FakeAudit()
    vault = FakeVault()

    # Primeira execução
    report1 = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)
    assert report1.snapshots_deleted >= 1 or report1.consents_awaiting_deleted >= 1

    # Segunda execução com o mesmo now
    report2 = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report2.snapshots_deleted == 0
    assert report2.consents_awaiting_deleted == 0
    # consent_auth virou REVOKED mas revoked_at=NOW, então revoked_at+90d > NOW
    assert report2.consents_expired == 0
    # consent_rev foi deletado na primeira execução
    assert report2.consents_revoked_deleted == 0
    assert report2.vault_shredded == 0


# ---------------------------------------------------------------------------
# Data inválida → skipped_invalid
# ---------------------------------------------------------------------------

def test_invalid_date_skipped():
    """Documento com data inválida deve ser ignorado e contado em skipped_invalid."""
    # Snapshot com expires_at inválido
    snap = {
        "_id": "snap-bad-date",
        "snapshot_id": "snap-bad-date",
        "pseudonym_id": "pid-bad",
        "consent_id": "cid-bad",
        "expires_at": "nao-e-uma-data",
        "type": "financial_snapshot",
    }
    store = InMemoryRetentionStore(snapshots=[snap])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.skipped_invalid >= 1
    assert report.snapshots_deleted == 0


def test_missing_date_skipped():
    """Documento sem campo de data deve ser ignorado e contado em skipped_invalid."""
    consent = {
        "_id": "cid-no-expires",
        "consent_id": "cid-no-expires",
        "pseudonym_id": "pid-noexp",
        "status": "AUTHORISED",
        "created_at": iso(PAST),
        # sem expires_at
    }
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.skipped_invalid >= 1
    assert report.consents_expired == 0


# ---------------------------------------------------------------------------
# Nenhum evento contém CPF
# ---------------------------------------------------------------------------

CPF_PATTERN = re.compile(r"\b\d{3}[\.\-]?\d{3}[\.\-]?\d{3}[\.\-]?\d{2}\b")


def test_no_cpf_in_audit_events():
    """Nenhum evento de auditoria emitido pelo job contém CPF."""
    consent = make_consent(
        status="AUTHORISED",
        pseudonym_id="pid-nocpf",
        expires_at=PAST,
    )
    store = InMemoryRetentionStore(consents=[consent])
    audit = FakeAudit()
    vault = FakeVault()

    run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    for event in audit.events:
        event_str = str(event)
        assert not CPF_PATTERN.search(event_str), (
            f"CPF encontrado no evento: {event}"
        )


# ---------------------------------------------------------------------------
# retention.run sempre emitido (inclusive dry_run)
# ---------------------------------------------------------------------------

def test_retention_run_event_always_emitted():
    """retention.run deve ser emitido ao final de toda execução, inclusive dry_run."""
    store = InMemoryRetentionStore()
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)
    run_events = audit.events_of_type("retention.run")
    assert len(run_events) == 1
    evt = run_events[0]
    assert evt["actor"] == "retention"
    assert evt["pseudonym_id"] is None
    assert "snapshots_deleted" in evt
    assert "dry_run" in evt
    assert evt["dry_run"] is False


# ---------------------------------------------------------------------------
# CloudantRetentionStore — paginação por bookmark e delete com _rev
# ---------------------------------------------------------------------------

class FakeCloudantClient:
    """
    Fake síncrono do SDK ibmcloudant para verificar paginação e delete.
    """

    def __init__(self, docs_by_db: dict[str, list[dict]]) -> None:
        """
        docs_by_db: {db_name: [doc_dict, ...]}
        Cada doc deve ter _id e _rev.
        """
        self._docs: dict[str, list[dict]] = {
            db: [dict(d) for d in docs]
            for db, docs in docs_by_db.items()
        }
        self.post_find_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    def set_service_url(self, url: str) -> None:
        pass

    def post_find(self, *, db: str, selector: dict, limit: int, bookmark: str | None = None, **kwargs) -> MagicMock:
        self.post_find_calls.append({"db": db, "selector": selector, "limit": limit, "bookmark": bookmark})
        all_docs = self._docs.get(db, [])

        # Filter by selector
        filtered = [d for d in all_docs if _matches(d, selector)]

        # Simulate pagination
        PAGE = limit
        if bookmark and bookmark.startswith("page:"):
            offset = int(bookmark.split(":")[1])
        else:
            offset = 0

        page = filtered[offset:offset + PAGE]
        next_offset = offset + PAGE
        has_more = next_offset < len(filtered)
        next_bm = f"page:{next_offset}" if has_more else bookmark  # same → stop

        result = MagicMock()
        result.get_result.return_value = {
            "docs": page,
            "bookmark": next_bm,
        }
        return result

    def get_document(self, *, db: str, doc_id: str, **kwargs) -> MagicMock:
        docs = self._docs.get(db, [])
        found = next((d for d in docs if d["_id"] == doc_id), None)
        if found is None:
            from ibm_cloud_sdk_core import ApiException
            exc = ApiException(code=404, message="not found")
            raise exc
        result = MagicMock()
        result.get_result.return_value = dict(found)
        return result

    def delete_document(self, *, db: str, doc_id: str, rev: str, **kwargs) -> MagicMock:
        self.delete_calls.append({"db": db, "doc_id": doc_id, "rev": rev})
        docs = self._docs.get(db, [])
        self._docs[db] = [d for d in docs if d["_id"] != doc_id]
        result = MagicMock()
        result.get_result.return_value = {"ok": True}
        return result

    def put_document(self, *, db: str, doc_id: str, document, **kwargs) -> MagicMock:
        result = MagicMock()
        result.get_result.return_value = {"ok": True}
        return result


def _make_cloudant_store(client: FakeCloudantClient) -> CloudantRetentionStore:
    """Constrói um CloudantRetentionStore injetando o client fake."""
    store = CloudantRetentionStore.__new__(CloudantRetentionStore)
    store._client = client
    store._consents_db = "consent_registry"
    store._snapshots_db = "financial_snapshots"
    store._vault_db = "identity_vault"
    store._decisions_db = "decision_traces"
    return store


def test_cloudant_store_pagination_bookmark():
    """
    CloudantRetentionStore deve paginar usando bookmark quando há mais docs que o limit.
    Verifica que post_find é chamado múltiplas vezes com bookmark crescente.
    """
    # Criar 3 docs — com PAGE_SIZE = 200, precisamos simular múltiplas páginas manualmente
    # Aqui testamos a lógica de bookmark: se a primeira página tem exatamente limit docs,
    # deve fazer uma segunda chamada com o bookmark retornado.

    # Vamos usar um PAGE_SIZE menor via monkey-patch
    import src.privacy.retention_store as rs_module
    original_page_size = rs_module._PAGE_SIZE
    rs_module._PAGE_SIZE = 2  # página de 2 para facilitar o teste

    try:
        docs = [
            {"_id": f"d{i}", "_rev": f"1-rev{i}", "type": "financial_snapshot",
             "snapshot_id": f"d{i}", "pseudonym_id": "pid-pg", "consent_id": "cid-pg",
             "expires_at": iso(FAR_FUTURE)}
            for i in range(5)
        ]
        client = FakeCloudantClient({"financial_snapshots": docs})
        store = _make_cloudant_store(client)

        result = list(store.iter_snapshots({}))
        assert len(result) == 5
        # Deve ter feito mais de uma chamada (paginação)
        snap_calls = [c for c in client.post_find_calls if c["db"] == "financial_snapshots"]
        assert len(snap_calls) > 1, "Esperava paginação com múltiplas chamadas post_find"

    finally:
        rs_module._PAGE_SIZE = original_page_size


def test_cloudant_store_delete_uses_rev():
    """delete_snapshot deve usar o _rev do documento ao chamar delete_document."""
    doc = {
        "_id": "snap-del",
        "_rev": "3-abc123",
        "type": "financial_snapshot",
        "snapshot_id": "snap-del",
        "pseudonym_id": "pid-del",
        "consent_id": "cid-del",
        "expires_at": iso(FAR_FUTURE),
    }
    client = FakeCloudantClient({"financial_snapshots": [doc]})
    store = _make_cloudant_store(client)

    store.delete_snapshot(doc)

    assert len(client.delete_calls) == 1
    dc = client.delete_calls[0]
    assert dc["db"] == "financial_snapshots"
    assert dc["doc_id"] == "snap-del"
    assert dc["rev"] == "3-abc123"


def test_cloudant_store_delete_fetches_rev_if_missing():
    """delete_snapshot sem _rev no doc deve buscar o _rev via get_document."""
    doc_in_db = {
        "_id": "snap-norev",
        "_rev": "5-xyz",
        "type": "financial_snapshot",
        "snapshot_id": "snap-norev",
        "pseudonym_id": "pid-norev",
        "consent_id": "cid-norev",
        "expires_at": iso(FAR_FUTURE),
    }
    client = FakeCloudantClient({"financial_snapshots": [doc_in_db]})
    store = _make_cloudant_store(client)

    # Passar doc sem _rev
    doc_without_rev = {k: v for k, v in doc_in_db.items() if k != "_rev"}
    store.delete_snapshot(doc_without_rev)

    assert len(client.delete_calls) == 1
    dc = client.delete_calls[0]
    assert dc["rev"] == "5-xyz"


# ---------------------------------------------------------------------------
# BUG 1 — fallback por pseudonym_id não deve afetar snapshot de consentimento AUTHORISED
# ---------------------------------------------------------------------------

def test_rule_a_authorised_snapshot_not_deleted_when_old_revoked_consent_exists():
    """
    Titular com dois consentimentos:
      - old_consent: REVOKED há 120 dias (revoked_at + 90d < now)
      - new_consent: AUTHORISED, expires_at no futuro

    Snapshot ligado a new_consent NÃO deve ser apagado.
    Antes do bug-fix o else fazia fallback por pseudonym_id e apagava indevidamente.
    """
    pid = "pid-two-consents"
    old_cid = "cid-old-revoked"
    new_cid = "cid-new-authorised"

    old_consent = make_consent(
        status="REVOKED",
        consent_id=old_cid,
        pseudonym_id=pid,
        expires_at=PAST,
        revoked_at=NOW - timedelta(days=120),
        revocation_reason="CUSTOMER_REQUEST",
    )
    new_consent = make_consent(
        status="AUTHORISED",
        consent_id=new_cid,
        pseudonym_id=pid,
        expires_at=FAR_FUTURE,
    )
    # Snapshot do consentimento novo (AUTHORISED, expires_at futuro)
    new_snap = make_snapshot(
        pseudonym_id=pid,
        consent_id=new_cid,
        expires_at=FAR_FUTURE,
    )

    store = InMemoryRetentionStore(
        consents=[old_consent, new_consent],
        snapshots=[new_snap],
    )
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    # Snapshot do novo consentimento NÃO deve ser apagado
    assert report.snapshots_deleted == 0, (
        "Snapshot de consentimento AUTHORISED foi apagado indevidamente "
        "(bug: fallback por pseudonym_id atingiu consentimento ativo)"
    )
    assert len(list(store.iter_snapshots({}))) == 1

    # Consentimento antigo REVOKED há 120 dias → deve ser apagado (regra d)
    assert report.consents_revoked_deleted == 1


def test_rule_a_snapshot_deleted_when_consent_doc_none_and_old_revoked_exists():
    """
    Snapshot sem consentimento associado (consent_doc is None) + revoked_consents
    por pseudonym_id com revoked_at + 90d < now → snapshot deve ser apagado.
    """
    pid = "pid-orphan-snap"
    # Consentimento REVOKED já foi apagado do store, mas o snapshot ainda existe.
    # Simulamos isso: não colocamos o consentimento no store.
    # O revoked_consents index não vai ter esse pid pois não há REVOKED no store.
    # Para o fallback funcionar, precisamos de um consentimento REVOKED no store
    # mas sem consent_id matchando o snapshot (simula consentimento diferente do mesmo pid).
    old_revoked = make_consent(
        status="REVOKED",
        consent_id="cid-orphan-old",
        pseudonym_id=pid,
        expires_at=PAST,
        revoked_at=NOW - timedelta(days=120),
        revocation_reason="CUSTOMER_REQUEST",
    )
    # Snapshot com consent_id que NÃO existe no store (consentimento já foi deletado)
    orphan_snap = make_snapshot(
        pseudonym_id=pid,
        consent_id="cid-already-deleted",  # não existe no consent_by_id
        expires_at=FAR_FUTURE,
    )

    store = InMemoryRetentionStore(
        consents=[old_revoked],
        snapshots=[orphan_snap],
    )
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    # consent_doc is None → fallback por pseudonym_id → revoked_at+90d < now → apagar
    assert report.snapshots_deleted == 1
    assert list(store.iter_snapshots({})) == []


# ---------------------------------------------------------------------------
# BUG 2 — dry_run e execução real produzem contagens idênticas
# ---------------------------------------------------------------------------

def _make_identical_store() -> tuple[InMemoryRetentionStore, FakeVault]:
    """Cria um store e vault com todos os tipos de documentos sujeitos a retenção."""
    pid_snap = "pid-snap-dry"
    snap = make_snapshot(
        pseudonym_id=pid_snap,
        consent_id="cid-snap-dry",
        expires_at=PAST,
    )

    pid_await = "pid-await-dry"
    consent_await = make_consent(
        status="AWAITING_AUTHORISATION",
        pseudonym_id=pid_await,
        awaiting_expires_at=PAST,
    )

    pid_auth = "pid-auth-dry"
    consent_auth = make_consent(
        status="AUTHORISED",
        pseudonym_id=pid_auth,
        expires_at=PAST,
    )

    pid_rev = "pid-rev-dry"
    consent_rev = make_consent(
        status="REVOKED",
        pseudonym_id=pid_rev,
        revoked_at=NOW - timedelta(days=100),
        revocation_reason="CUSTOMER_REQUEST",
        expires_at=PAST,
    )

    # Vault orphan: sem consentimento nem snapshot, criado há 48h
    pid_vault = "pid-vault-orphan-dry"
    vault_doc = make_vault_doc(
        pseudonym_id=pid_vault,
        created_at=NOW - timedelta(hours=48),
    )

    store = InMemoryRetentionStore(
        consents=[consent_await, consent_auth, consent_rev],
        snapshots=[snap],
        vault=[vault_doc],
    )
    vault = FakeVault(pseudonym_ids=[pid_vault])
    return store, vault


def _clone_store(src: InMemoryRetentionStore) -> InMemoryRetentionStore:
    """Clona um InMemoryRetentionStore para garantir estados independentes."""
    import copy
    return InMemoryRetentionStore(
        consents=[copy.deepcopy(d) for d in src.iter_consents({})],
        snapshots=[copy.deepcopy(d) for d in src.iter_snapshots({})],
        vault=[copy.deepcopy(d) for d in src.iter_vault({})],
    )


def test_dry_run_counts_match_real_run():
    """
    dry_run=True e dry_run=False em stores idênticos devem produzir
    contagens idênticas em todos os campos numéricos do RelatorioReport.
    (Apenas o campo dry_run difere.)
    """
    from dataclasses import asdict

    store_dry, vault_dry = _make_identical_store()
    store_real, vault_real = _make_identical_store()

    audit_dry = FakeAudit()
    audit_real = FakeAudit()

    report_dry = run_retention(
        now=NOW, dry_run=True,
        store=store_dry, vault=vault_dry, audit=audit_dry.record_event,
    )
    report_real = run_retention(
        now=NOW, dry_run=False,
        store=store_real, vault=vault_real, audit=audit_real.record_event,
    )

    dry_dict = asdict(report_dry)
    real_dict = asdict(report_real)

    # Remove o flag dry_run antes de comparar
    dry_dict.pop("dry_run")
    real_dict.pop("dry_run")

    assert dry_dict == real_dict, (
        f"dry_run e execução real divergem:\n  dry_run:  {dry_dict}\n  real_run: {real_dict}"
    )


# ---------------------------------------------------------------------------
# Helpers para documentos de decisão
# ---------------------------------------------------------------------------

def make_decision(
    *,
    doc_type: str = "simulation",
    pseudonym_id: str = "pid-dec-1",
    consent_id: str = "cid-dec-1",
    expires_at: datetime,
    doc_id: str | None = None,
) -> dict:
    did = doc_id or f"{doc_type}:{str(uuid.uuid4())}"
    return {
        "_id": did,
        "type": doc_type,
        "pseudonym_id": pseudonym_id,
        "consent_id": consent_id,
        "expires_at": iso(expires_at),
    }


# ---------------------------------------------------------------------------
# Regra a2 — Decisões (simulation / trace)
# ---------------------------------------------------------------------------

def test_rule_a2_decision_expired_deleted():
    """Decisão com expires_at no passado deve ser deletada."""
    dec = make_decision(doc_type="simulation", expires_at=PAST)
    store = InMemoryRetentionStore(decisions=[dec])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.decisions_deleted == 1
    assert list(store.iter_decisions({})) == []


def test_rule_a2_decision_not_expired_kept():
    """Decisão com expires_at no futuro NÃO deve ser deletada."""
    dec = make_decision(doc_type="trace", expires_at=FAR_FUTURE)
    store = InMemoryRetentionStore(decisions=[dec])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.decisions_deleted == 0
    assert len(list(store.iter_decisions({}))) == 1


def test_rule_a2_decision_revoked_consent_antecipates_limit():
    """
    Consentimento REVOKED há 95 dias antecipa o limite para
    min(expires_at_futuro, revoked_at + 90d). Como revoked_at + 90d < now → apagar.
    """
    consent_id = "cid-dec-rev"
    revoked_at = NOW - timedelta(days=95)  # 95 dias atrás → revoked_at+90d = 5 dias atrás
    consent = make_consent(
        status="REVOKED",
        consent_id=consent_id,
        pseudonym_id="pid-dec-rev",
        expires_at=FAR_FUTURE,
        revoked_at=revoked_at,
        revocation_reason="CUSTOMER_REQUEST",
    )
    dec = make_decision(
        doc_type="simulation",
        pseudonym_id="pid-dec-rev",
        consent_id=consent_id,
        expires_at=FAR_FUTURE,
    )
    store = InMemoryRetentionStore(consents=[consent], decisions=[dec])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    # revoked_at+90d = 95-90=5 dias atrás < now → apagar
    assert report.decisions_deleted == 1
    assert list(store.iter_decisions({})) == []


def test_rule_a2_decision_no_expires_at_skipped_invalid():
    """Decisão sem expires_at → skipped_invalid; logger.warning só com _id."""
    dec = {
        "_id": "sim:no-exp",
        "type": "simulation",
        "pseudonym_id": "pid-noexp",
        "consent_id": "cid-noexp",
        # sem expires_at
    }
    store = InMemoryRetentionStore(decisions=[dec])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.skipped_invalid >= 1
    assert report.decisions_deleted == 0
    # Documento mantido
    assert len(list(store.iter_decisions({}))) == 1


def test_rule_a2_other_type_ignored():
    """Documento de outro type (não simulation nem trace) no banco de decisões é ignorado."""
    dec = {
        "_id": "other:123",
        "type": "other_type",
        "pseudonym_id": "pid-other",
        "consent_id": "cid-other",
        "expires_at": iso(PAST),  # vencido, mas type ignorado
    }
    store = InMemoryRetentionStore(decisions=[dec])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)

    assert report.decisions_deleted == 0
    assert len(list(store.iter_decisions({}))) == 1


def test_rule_a2_dry_run_counts_but_does_not_delete():
    """dry_run=True conta a decisão vencida mas NÃO a apaga."""
    dec = make_decision(doc_type="trace", expires_at=PAST)
    store = InMemoryRetentionStore(decisions=[dec])
    audit = FakeAudit()
    vault = FakeVault()

    report = run_retention(now=NOW, dry_run=True, store=store, vault=vault, audit=audit.record_event)

    assert report.decisions_deleted == 1
    # Documento ainda existe (dry_run)
    assert len(list(store.iter_decisions({}))) == 1


def test_rule_a2_idempotent_second_run_zero():
    """Segunda execução após a primeira → decisions_deleted = 0."""
    dec = make_decision(doc_type="simulation", expires_at=PAST)
    store = InMemoryRetentionStore(decisions=[dec])
    audit = FakeAudit()
    vault = FakeVault()

    report1 = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)
    assert report1.decisions_deleted == 1

    report2 = run_retention(now=NOW, store=store, vault=vault, audit=audit.record_event)
    assert report2.decisions_deleted == 0


# ---------------------------------------------------------------------------
# Cloudant iter_decisions / delete_decision
# ---------------------------------------------------------------------------

def test_cloudant_store_iter_decisions():
    """iter_decisions deve retornar documentos do banco de decisões."""
    docs = [
        {"_id": "sim:1", "_rev": "1-aaa", "type": "simulation", "pseudonym_id": "pid-d", "expires_at": iso(FAR_FUTURE)},
        {"_id": "trace:1", "_rev": "1-bbb", "type": "trace", "pseudonym_id": "pid-d", "expires_at": iso(FAR_FUTURE)},
    ]
    client = FakeCloudantClient({"decision_traces": docs})
    store = _make_cloudant_store(client)

    result = list(store.iter_decisions({}))
    assert len(result) == 2


def test_cloudant_store_delete_decision_uses_rev():
    """delete_decision deve usar o _rev ao chamar delete_document."""
    doc = {
        "_id": "sim:del",
        "_rev": "2-xyzabc",
        "type": "simulation",
        "pseudonym_id": "pid-del-dec",
        "expires_at": iso(PAST),
    }
    client = FakeCloudantClient({"decision_traces": [doc]})
    store = _make_cloudant_store(client)

    store.delete_decision(doc)

    assert len(client.delete_calls) == 1
    dc = client.delete_calls[0]
    assert dc["db"] == "decision_traces"
    assert dc["doc_id"] == "sim:del"
    assert dc["rev"] == "2-xyzabc"


# ---------------------------------------------------------------------------
# Bug: CLOUDANT_DB_DECISIONS="" deve cair no default "decision_traces"
# ---------------------------------------------------------------------------

def test_ensure_databases_empty_decisions_env_uses_default(monkeypatch):
    """
    Com CLOUDANT_DB_DECISIONS="" (como no .env.example), ensure_databases deve
    usar o nome padrão "decision_traces" em vez de "" (banco vazio).
    """
    from unittest.mock import MagicMock, call
    from src.privacy.retention_store import ensure_databases

    monkeypatch.setenv("CLOUDANT_DB_DECISIONS", "")

    client = MagicMock()
    db_result = MagicMock()
    db_result.get_result.return_value = {}
    client.put_database.return_value = db_result

    ensure_databases(client)

    called_dbs = [c.kwargs["db"] for c in client.put_database.call_args_list]
    assert "decision_traces" in called_dbs, (
        f"Esperava 'decision_traces' em {called_dbs}; "
        "CLOUDANT_DB_DECISIONS='' deve cair no default."
    )
    assert "" not in called_dbs, (
        f"Nome de banco vazio encontrado em {called_dbs}."
    )


def test_ensure_indexes_empty_decisions_env_uses_default(monkeypatch):
    """
    Com CLOUDANT_DB_DECISIONS="" (como no .env.example), ensure_indexes deve
    usar o nome padrão "decision_traces" em vez de "" (banco vazio).
    """
    from unittest.mock import MagicMock
    from src.privacy.retention_store import ensure_indexes

    monkeypatch.setenv("CLOUDANT_DB_DECISIONS", "")

    client = MagicMock()
    idx_result = MagicMock()
    idx_result.get_result.return_value = {}
    client.post_index.return_value = idx_result

    ensure_indexes(client)

    called_dbs = [c.kwargs["db"] for c in client.post_index.call_args_list]
    assert "decision_traces" in called_dbs, (
        f"Esperava 'decision_traces' em {called_dbs}; "
        "CLOUDANT_DB_DECISIONS='' deve cair no default."
    )
    assert "" not in called_dbs, (
        f"Nome de banco vazio encontrado em {called_dbs}."
    )
