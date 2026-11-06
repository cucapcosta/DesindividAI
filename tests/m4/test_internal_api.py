"""
Testes de integração para /internal/* (TestClient, sem rede).

Usa app.dependency_overrides para injetar fakes de store/vault/audit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.api.routers.internal import get_audit, get_store, get_vault
from src.privacy.audit import AuditLogger, InMemoryAuditRepository
from src.privacy.identity_vault import IdentityVault, InMemoryVaultRepository
from src.privacy.pseudonymization import pseudonymize_cpf
from src.privacy.retention_store import InMemoryRetentionStore

# CPF válido que deve ser rejeitado como pseudonym_id
_VALID_CPF = "52998224725"

# pseudonym_id válido (64 hex chars)
_VALID_PSEUDONYM = "a" * 64

_TOKEN = "test-secret-token-for-internal-api"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_repo():
    return InMemoryAuditRepository()


@pytest.fixture
def fake_audit(audit_repo):
    return AuditLogger(repository=audit_repo)


@pytest.fixture
def fake_vault():
    return IdentityVault(repository=InMemoryVaultRepository())


@pytest.fixture
def fake_store():
    return InMemoryRetentionStore()


@pytest.fixture
def client(fake_store, fake_vault, fake_audit):
    """TestClient com dependency_overrides para store/vault/audit."""
    app.dependency_overrides[get_store] = lambda: fake_store
    app.dependency_overrides[get_vault] = lambda: fake_vault
    app.dependency_overrides[get_audit] = lambda: fake_audit
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers(token: str) -> dict:
    return {"X-Internal-Token": token}


# ---------------------------------------------------------------------------
# Testes de autenticação
# ---------------------------------------------------------------------------

def test_no_token_env_returns_503(client, monkeypatch):
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    resp = client.post(
        f"/internal/erase/{_VALID_PSEUDONYM}",
        headers=_headers(_TOKEN),
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "INTERNAL_API_DISABLED"


def test_missing_header_returns_401(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", _TOKEN)
    resp = client.post(f"/internal/erase/{_VALID_PSEUDONYM}")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "UNAUTHORIZED"


def test_wrong_header_returns_401(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", _TOKEN)
    resp = client.post(
        f"/internal/erase/{_VALID_PSEUDONYM}",
        headers=_headers("wrong-token"),
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# Teste de validação de pseudonym_id
# ---------------------------------------------------------------------------

def test_cpf_as_pseudonym_returns_422(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", _TOKEN)
    resp = client.post(
        f"/internal/erase/{_VALID_CPF}",
        headers=_headers(_TOKEN),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Teste de erase OK
# ---------------------------------------------------------------------------

def test_erase_ok_returns_report(client, monkeypatch, fake_store, fake_vault):
    monkeypatch.setenv("INTERNAL_API_TOKEN", _TOKEN)

    # Inserir dados para o titular
    pseudonym_a = pseudonymize_cpf(_VALID_CPF)
    fake_store._consents["c1"] = {
        "_id": "c1",
        "consent_id": "c1",
        "pseudonym_id": pseudonym_a,
        "status": "AUTHORISED",
    }
    fake_store._snapshots["s1"] = {
        "_id": "s1",
        "snapshot_id": "s1",
        "pseudonym_id": pseudonym_a,
    }
    fake_store._decisions["dec1"] = {
        "_id": "dec1",
        "type": "simulation",
        "pseudonym_id": pseudonym_a,
        "consent_id": "c1",
    }
    fake_vault.store(_VALID_CPF)

    resp = client.post(
        f"/internal/erase/{pseudonym_a}",
        headers=_headers(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "consents_revoked" in body
    assert "snapshots_deleted" in body
    assert "decisions_deleted" in body
    assert "vault_shredded" in body
    assert "trace_id" in body
    assert body["consents_revoked"] == 1
    assert body["snapshots_deleted"] == 1
    assert body["decisions_deleted"] == 1
    assert body["vault_shredded"] is True


# ---------------------------------------------------------------------------
# Testes de retention
# ---------------------------------------------------------------------------

def test_retention_dry_run_by_default(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", _TOKEN)
    resp = client.post(
        "/internal/retention/run",
        headers=_headers(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Sem query → dry_run=True por padrão
    assert body["dry_run"] is True
    # Nada apagado
    assert body["snapshots_deleted"] == 0
    assert body["decisions_deleted"] == 0


def test_retention_dry_run_false_deletes_expired_snapshot(client, monkeypatch, fake_store):
    monkeypatch.setenv("INTERNAL_API_TOKEN", _TOKEN)

    # Snapshot expirado
    past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pseudonym = "b" * 64
    fake_store._snapshots["snap-expired"] = {
        "_id": "snap-expired",
        "snapshot_id": "snap-expired",
        "pseudonym_id": pseudonym,
        "expires_at": past,
    }

    resp = client.post(
        "/internal/retention/run?dry_run=false",
        headers=_headers(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshots_deleted"] == 1
    # Snapshot deve ter sido removido do store
    assert "snap-expired" not in fake_store._snapshots
