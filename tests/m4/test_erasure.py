"""
Testes de erase_subject (InMemory, sem rede).

Cenários:
  1. Contagens corretas após erase.
  2. Eventos na ordem: consent.erasure_requested → erasure.completed.
  3. request_trace_id == trace_id do primeiro evento.
  4. Segunda chamada → 0, 0, False.
  5. Consentimento/snapshot de OUTRO titular intocados.
  6. Consentimento já REVOKED (motivo EXPIRED) não alterado.
  7. Após erase, vault.reveal falha (CPF irrecuperável).
"""

from __future__ import annotations

import pytest

from src.privacy.audit import AuditLogger, InMemoryAuditRepository
from src.privacy.erasure import erase_subject
from src.privacy.identity_vault import IdentityVault, InMemoryVaultRepository
from src.privacy.pseudonymization import pseudonymize_cpf
from src.privacy.retention_store import InMemoryRetentionStore
from src.privacy.identity_vault import VaultError

# CPFs válidos para teste
_CPF_A = "52998224725"   # titular alvo
_CPF_B = "11144477735"   # outro titular (CPF válido de teste)


def _pseudonym(cpf: str) -> str:
    return pseudonymize_cpf(cpf)


def _make_vault_with_cpf(cpf: str, vault_repo: InMemoryVaultRepository) -> IdentityVault:
    """Cria um IdentityVault e armazena o CPF."""
    vault = IdentityVault(repository=vault_repo)
    vault.store(cpf)
    return vault


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pseudonym_a():
    return _pseudonym(_CPF_A)


@pytest.fixture
def pseudonym_b():
    return _pseudonym(_CPF_B)


@pytest.fixture
def audit_repo():
    return InMemoryAuditRepository()


@pytest.fixture
def audit(audit_repo):
    return AuditLogger(repository=audit_repo)


@pytest.fixture
def vault_repo():
    return InMemoryVaultRepository()


@pytest.fixture
def vault(vault_repo):
    v = IdentityVault(repository=vault_repo)
    v.store(_CPF_A)
    return v


@pytest.fixture
def store(pseudonym_a, pseudonym_b):
    """Store com 2 consentimentos e 1 snapshot de A, 1 de B."""
    consents = [
        {
            "_id": "c-a-1",
            "consent_id": "c-a-1",
            "pseudonym_id": pseudonym_a,
            "status": "AUTHORISED",
        },
        {
            "_id": "c-a-2",
            "consent_id": "c-a-2",
            "pseudonym_id": pseudonym_a,
            "status": "AUTHORISED",
        },
        {
            "_id": "c-b-1",
            "consent_id": "c-b-1",
            "pseudonym_id": pseudonym_b,
            "status": "AUTHORISED",
        },
    ]
    snapshots = [
        {
            "_id": "s-a-1",
            "snapshot_id": "s-a-1",
            "pseudonym_id": pseudonym_a,
        },
        {
            "_id": "s-b-1",
            "snapshot_id": "s-b-1",
            "pseudonym_id": pseudonym_b,
        },
    ]
    return InMemoryRetentionStore(consents=consents, snapshots=snapshots)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

def test_erase_returns_correct_counts(store, vault, audit, pseudonym_a):
    report = erase_subject(pseudonym_a, store=store, vault=vault, audit=audit)

    assert report.consents_revoked == 2
    assert report.snapshots_deleted == 1
    assert report.vault_shredded is True


def test_erase_events_order_and_fields(audit_repo, store, vault, audit, pseudonym_a):
    report = erase_subject(pseudonym_a, store=store, vault=vault, audit=audit)

    events = audit_repo._store
    event_types = [e["event_type"] for e in events]

    assert event_types[0] == "consent.erasure_requested"
    assert event_types[1] == "erasure.completed"


def test_erase_request_trace_id_matches(audit_repo, store, vault, audit, pseudonym_a):
    report = erase_subject(pseudonym_a, store=store, vault=vault, audit=audit)

    events = audit_repo._store
    # O trace_id do relatório deve ser o trace_id do primeiro evento
    assert report.trace_id == events[0]["trace_id"]

    # O segundo evento deve ter request_trace_id apontando para o trace do primeiro
    completed_evt = events[1]
    assert completed_evt["request_trace_id"] == report.trace_id


def test_erase_idempotent_second_call_returns_zeros(store, vault, audit, pseudonym_a):
    erase_subject(pseudonym_a, store=store, vault=vault, audit=audit)
    report2 = erase_subject(pseudonym_a, store=store, vault=vault, audit=audit)

    assert report2.consents_revoked == 0
    assert report2.snapshots_deleted == 0
    assert report2.vault_shredded is False


def test_erase_does_not_touch_other_subject(store, vault_repo, audit, pseudonym_a, pseudonym_b):
    # Também armazenar B no vault
    v = IdentityVault(repository=vault_repo)
    v.store(_CPF_A)
    v.store(_CPF_B)

    erase_subject(pseudonym_a, store=store, vault=v, audit=audit)

    # Consentimento de B deve continuar AUTHORISED
    b_consents = list(store.iter_consents({"pseudonym_id": pseudonym_b}))
    assert len(b_consents) == 1
    assert b_consents[0]["status"] == "AUTHORISED"

    # Snapshot de B deve existir
    b_snapshots = list(store.iter_snapshots({"pseudonym_id": pseudonym_b}))
    assert len(b_snapshots) == 1

    # Vault de B deve ser recuperável
    cpf_b = v.reveal(pseudonym_b, purpose="LEGAL_REQUEST", actor="test")
    assert "11144477735" == cpf_b


def test_erase_does_not_change_already_revoked_expired(pseudonym_a, vault, audit):
    """Consentimento já REVOKED com motivo EXPIRED não deve ser alterado."""
    store = InMemoryRetentionStore(
        consents=[
            {
                "_id": "c-revoked",
                "consent_id": "c-revoked",
                "pseudonym_id": pseudonym_a,
                "status": "REVOKED",
                "revocation_reason": "EXPIRED",
                "revoked_at": "2024-01-01T00:00:00Z",
            }
        ],
        snapshots=[],
    )

    report = erase_subject(pseudonym_a, store=store, vault=vault, audit=audit)

    # Consentimento já REVOKED não é contado
    assert report.consents_revoked == 0

    # O consentimento deve manter revocation_reason EXPIRED (não ser sobrescrito)
    docs = list(store.iter_consents({"pseudonym_id": pseudonym_a}))
    assert docs[0]["revocation_reason"] == "EXPIRED"


def test_erase_vault_cpf_irrecoverable_after_shred(vault, store, audit, pseudonym_a):
    erase_subject(pseudonym_a, store=store, vault=vault, audit=audit)

    with pytest.raises(VaultError):
        vault.reveal(pseudonym_a, purpose="LEGAL_REQUEST", actor="test")
