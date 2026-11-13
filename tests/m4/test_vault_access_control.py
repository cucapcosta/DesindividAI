"""
Controles de acesso do reveal(): finalidade amarrada ao actor e consentimento
ativo para SERASA_QUERY (defesa em profundidade).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.privacy.identity_vault import (
    _DEFAULT_PURPOSE_ACTORS,
    IdentityVault,
    InMemoryVaultRepository,
    VaultAccessDenied,
    VaultError,
    has_active_consent,
)

_CPF = "529.982.247-25"
_CPF_DIGITS = "52998224725"
_NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


class _Events:
    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event_type, **fields):
        self.events.append({"event_type": event_type, **fields})


def _vault(*, purpose_actors=_DEFAULT_PURPOSE_ACTORS, consent_checker=None):
    events = _Events()
    vault = IdentityVault(
        repository=InMemoryVaultRepository(),
        audit=events,
        purpose_actors=purpose_actors,
        consent_checker=consent_checker,
    )
    pid = vault.store(_CPF)
    return vault, pid, events


# ---------------------------------------------------------------------------
# purpose → actor
# ---------------------------------------------------------------------------

def test_agent1_can_reveal_for_serasa():
    vault, pid, events = _vault(consent_checker=lambda p: True)
    assert vault.reveal(pid, purpose="SERASA_QUERY", actor="agent1") == _CPF_DIGITS
    assert events.events[-1]["ok"] is True


@pytest.mark.parametrize("purpose,actor", [
    ("SERASA_QUERY", "internal_api"),
    ("SERASA_QUERY", "agent2"),
    ("LEGAL_REQUEST", "agent1"),
    ("LEGAL_REQUEST", "retention"),
])
def test_actor_not_allowed_for_purpose_is_denied_and_audited(purpose, actor):
    vault, pid, events = _vault(consent_checker=lambda p: True)
    with pytest.raises(VaultAccessDenied):
        vault.reveal(pid, purpose=purpose, actor=actor)
    ev = events.events[-1]
    assert ev["ok"] is False and ev["reason"] == "actor_not_allowed"
    assert ev["actor"] == actor and ev["purpose"] == purpose
    assert _CPF_DIGITS not in repr(events.events)


def test_internal_api_can_reveal_for_legal_request_without_consent():
    """LEGAL_REQUEST não depende de consentimento (obrigação legal)."""
    vault, pid, _ = _vault(consent_checker=lambda p: False)
    assert vault.reveal(pid, purpose="LEGAL_REQUEST", actor="internal_api") == _CPF_DIGITS


def test_access_denied_is_a_vault_error():
    """O cliente Serasa já trata VaultError/Exception como fonte indisponível."""
    assert issubclass(VaultAccessDenied, VaultError)


def test_without_purpose_actors_any_actor_passes():
    """Construtor sem purpose_actors mantém o comportamento antigo (testes, ferramentas)."""
    vault, pid, _ = _vault(purpose_actors=None)
    assert vault.reveal(pid, purpose="SERASA_QUERY", actor="qualquer") == _CPF_DIGITS


# ---------------------------------------------------------------------------
# Consentimento ativo
# ---------------------------------------------------------------------------

def test_serasa_without_active_consent_is_denied_and_audited():
    seen = []
    vault, pid, events = _vault(consent_checker=lambda p: seen.append(p) or False)
    with pytest.raises(VaultAccessDenied):
        vault.reveal(pid, purpose="SERASA_QUERY", actor="agent1")
    assert seen == [pid]
    ev = events.events[-1]
    assert ev["ok"] is False and ev["reason"] == "no_active_consent"


def test_consent_checker_failure_denies_access():
    def _boom(_):
        raise RuntimeError("Cloudant offline")

    vault, pid, events = _vault(consent_checker=_boom)
    with pytest.raises(VaultAccessDenied):
        vault.reveal(pid, purpose="SERASA_QUERY", actor="agent1")
    assert events.events[-1]["reason"] == "no_active_consent"


def test_actor_check_runs_before_consent_check():
    calls = []
    vault, pid, events = _vault(consent_checker=lambda p: calls.append(p) or True)
    with pytest.raises(VaultAccessDenied):
        vault.reveal(pid, purpose="SERASA_QUERY", actor="intruso")
    assert calls == []
    assert events.events[-1]["reason"] == "actor_not_allowed"


def _consent(pid, status="AUTHORISED", expires=_NOW + timedelta(days=30)):
    return {
        "pseudonym_id": pid,
        "status": status,
        "expires_at": expires.isoformat() if isinstance(expires, datetime) else expires,
    }


@pytest.mark.parametrize("docs,expected", [
    ([_consent("p")], True),
    ([_consent("p", expires=_NOW - timedelta(seconds=1))], False),
    ([_consent("p", status="REVOKED")], False),
    ([_consent("p", status="AWAITING_AUTHORISATION")], False),
    ([_consent("outro")], False),
    ([_consent("p", expires="não é data")], False),
    ([_consent("p", expires=None)], False),
    ([], False),
    ([_consent("p", status="REVOKED"), _consent("p")], True),
])
def test_has_active_consent(docs, expected):
    assert has_active_consent(docs, "p", now=_NOW) is expected


def test_cloudant_checker_queries_authorised_consents(monkeypatch):
    from src.privacy import identity_vault as iv
    from src.privacy.retention_store import InMemoryRetentionStore

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    store = InMemoryRetentionStore(consents=[
        {"_id": "c1", "pseudonym_id": "p1", "status": "AUTHORISED", "expires_at": future},
        {"_id": "c2", "pseudonym_id": "p2", "status": "REVOKED", "expires_at": future},
    ])
    monkeypatch.setattr("src.privacy.retention_store.get_retention_store", lambda: store)
    assert iv._cloudant_consent_checker("p1") is True
    assert iv._cloudant_consent_checker("p2") is False
    assert iv._cloudant_consent_checker("p3") is False


def test_factory_wires_production_controls(monkeypatch):
    from src.privacy import identity_vault as iv

    monkeypatch.setattr(iv, "get_vault_repository", lambda: InMemoryVaultRepository())
    vault = iv.get_identity_vault()
    assert vault._purpose_actors is iv._DEFAULT_PURPOSE_ACTORS
    assert vault._consent_checker is iv._cloudant_consent_checker


def test_factory_vault_denies_serasa_when_consent_store_unavailable(monkeypatch):
    """Sem Cloudant configurado o checker falha e o reveal é negado (fail closed)."""
    from src.privacy import identity_vault as iv

    monkeypatch.setattr(iv, "get_vault_repository", lambda: InMemoryVaultRepository())
    vault = iv.get_identity_vault()
    pid = vault.store(_CPF)
    with pytest.raises(VaultAccessDenied):
        vault.resolve_for_serasa(pid)


def test_serasa_client_maps_denied_reveal_to_source_unavailable():
    from src.agents.agent1.sources import HttpSerasaClient
    from src.agents.agent1.sources import SourceUnavailableError

    def _denied(_pid):
        raise VaultAccessDenied("sem consentimento")

    client = HttpSerasaClient(base_url="https://serasa.invalid", api_key="x", resolver=_denied)
    with pytest.raises(SourceUnavailableError):
        client.fetch_report("a" * 64)
