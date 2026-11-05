"""
Testes de privacidade para persist_snapshot (InMemory, sem rede).

Cenários:
  1. Snapshot persistido contém key_version == current_key_version().
  2. CPF válido no snapshot → PIIViolation; repositório NÃO tem snapshot.
  3. Audit fake → evento agent1.snapshot_persisted com snapshot_id/consent_id e sem CPF.
  4. Audit cujo record_event lança RuntimeError → tool retorna OK e snapshot salvo.
  5. ctx.audit None → funciona como antes.
"""

from __future__ import annotations

import pytest

from src.agents.agent1 import tools
from src.agents.agent1.repository import InMemoryRepository
from src.agents.agent1.sandbox_data import SANDBOX_CPF, sandbox_consents
from src.agents.agent1.sources import SandboxOpenFinanceClient, SandboxSerasaClient
from src.privacy.pii_guard import PIIViolation
from src.privacy.pseudonymization import pseudonymize_cpf, current_key_version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(consent, repo, audit=None):
    ctx = tools.RunContext(
        consent=consent,
        repository=repo,
        open_finance=SandboxOpenFinanceClient(),
        serasa=SandboxSerasaClient(),
        audit=audit,
    )
    # Executar as três primeiras tools para chegar em persist_snapshot
    tools.get_open_finance_debts(ctx, consent["consent_id"])
    tools.get_serasa_data(ctx, consent["pseudonym_id"])
    tools.calculate_cet(ctx)
    return ctx


@pytest.fixture
def consent():
    pseudonym_id = pseudonymize_cpf(SANDBOX_CPF)
    return sandbox_consents(pseudonym_id)[0]


@pytest.fixture
def repo(consent):
    return InMemoryRepository([consent])


# ---------------------------------------------------------------------------
# Teste 1: key_version presente no snapshot
# ---------------------------------------------------------------------------

def test_snapshot_contains_current_key_version(consent, repo):
    ctx = _make_ctx(consent, repo)
    result = tools.persist_snapshot(ctx)

    snapshot = repo.get_snapshot(result["snapshot_id"])
    assert snapshot is not None
    assert snapshot["key_version"] == current_key_version()


# ---------------------------------------------------------------------------
# Teste 2: CPF válido injetado → PIIViolation; repositório sem snapshot
# ---------------------------------------------------------------------------

def test_pii_in_snapshot_raises_violation_and_nothing_persisted(consent, repo, monkeypatch):
    original_build = tools.build_snapshot

    def build_with_cpf(ctx):
        snap = original_build(ctx)
        # Injetar CPF válido num campo de texto
        snap["notes"] = f"cliente {SANDBOX_CPF} aprovado"
        return snap

    monkeypatch.setattr(tools, "build_snapshot", build_with_cpf)

    ctx = _make_ctx(consent, repo)

    with pytest.raises(PIIViolation):
        tools.persist_snapshot(ctx)

    # Repositório não deve ter nenhum snapshot
    assert repo._snapshots == {}


# ---------------------------------------------------------------------------
# Teste 3: Audit fake → evento correto e sem CPF nos campos
# ---------------------------------------------------------------------------

class _FakeAudit:
    def __init__(self):
        self.events: list[dict] = []

    def record_event(self, event_type: str, **fields) -> str:
        self.events.append({"event_type": event_type, **fields})
        return "fake-trace-id"


def test_audit_event_snapshot_persisted(consent, repo):
    audit = _FakeAudit()
    ctx = _make_ctx(consent, repo, audit=audit)
    result = tools.persist_snapshot(ctx)

    # Deve haver exatamente um evento
    assert len(audit.events) == 1
    evt = audit.events[0]
    assert evt["event_type"] == "agent1.snapshot_persisted"
    assert evt["snapshot_id"] == result["snapshot_id"]
    assert evt["consent_id"] == consent["consent_id"]
    # Sem CPF nos campos
    assert SANDBOX_CPF not in str(evt)


# ---------------------------------------------------------------------------
# Teste 4: record_event lança RuntimeError → tool retorna OK e snapshot salvo
# ---------------------------------------------------------------------------

class _BrokenAudit:
    def record_event(self, event_type: str, **fields) -> str:
        raise RuntimeError("falha simulada na auditoria")


def test_audit_failure_does_not_fail_tool(consent, repo):
    audit = _BrokenAudit()
    ctx = _make_ctx(consent, repo, audit=audit)
    result = tools.persist_snapshot(ctx)

    # Tool deve ter retornado normalmente
    assert "snapshot_id" in result
    # Snapshot deve estar salvo no repositório
    assert repo.get_snapshot(result["snapshot_id"]) is not None


# ---------------------------------------------------------------------------
# Teste 5: ctx.audit None → funciona como antes
# ---------------------------------------------------------------------------

def test_persist_snapshot_without_audit(consent, repo):
    ctx = _make_ctx(consent, repo, audit=None)
    result = tools.persist_snapshot(ctx)

    assert "snapshot_id" in result
    assert repo.get_snapshot(result["snapshot_id"]) is not None
