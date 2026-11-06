"""
Direitos do titular por CPF: apagamento em todas as versões da chave,
exportação (acesso/portabilidade) e log de falha de autenticação no /internal.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.api.routers.internal import get_audit, get_store, get_vault
from src.main import app
from src.privacy.audit import AuditLogger, AuditWriteError, InMemoryAuditRepository
from src.privacy.erasure import erase_subject_by_cpf
from src.privacy.identity_vault import IdentityVault
from src.privacy.retention_store import InMemoryRetentionStore
from src.privacy.subject_access import export_subject

_CPF = "529.982.247-25"
_CPF_DIGITS = "52998224725"
_TOKEN = "test-internal-token-do-not-use-in-production"
_FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


class _VaultRepoView:
    """InMemoryVaultRepository que o RetentionStore também enxerga."""

    def __init__(self, store: InMemoryRetentionStore):
        self._store = store

    def get(self, pseudonym_id):
        doc = self._store._vault.get(pseudonym_id)
        return dict(doc) if doc else None

    def put(self, doc):
        self._store._vault[doc["_id"]] = dict(doc)


def _seed_subject(store: InMemoryRetentionStore, pid: str, tag: str) -> None:
    store._consents[f"c-{tag}"] = {
        "_id": f"c-{tag}", "_rev": "1-x", "consent_id": f"c-{tag}", "pseudonym_id": pid,
        "status": "AUTHORISED", "expires_at": _FUTURE, "institutions": ["bb", "itau"],
    }
    store._snapshots[f"s-{tag}"] = {
        "_id": f"s-{tag}", "_rev": "1-y", "snapshot_id": f"s-{tag}", "pseudonym_id": pid,
        "consent_id": f"c-{tag}", "expires_at": _FUTURE, "debts": [{"cet_annual": 120.0}],
    }
    store._decisions[f"d-{tag}"] = {
        "_id": f"d-{tag}", "type": "trace", "pseudonym_id": pid,
        "consent_id": f"c-{tag}", "expires_at": _FUTURE, "explanation": "texto",
    }


@pytest.fixture
def env():
    store = InMemoryRetentionStore()
    audit_repo = InMemoryAuditRepository()
    audit = AuditLogger(repository=audit_repo)
    vault = IdentityVault(repository=_VaultRepoView(store), audit=audit.record_event)
    return store, vault, audit, audit_repo


def _no_cpf_anywhere(*objs) -> None:
    dumped = json.dumps(objs, default=str)
    assert _CPF_DIGITS not in dumped
    assert _CPF not in dumped


# ---------------------------------------------------------------------------
# Apagamento por CPF
# ---------------------------------------------------------------------------

def test_erase_by_cpf_covers_every_key_version(env, pseudonym_v2, monkeypatch):
    store, vault, audit, audit_repo = env
    pm = pseudonym_v2

    pid_v1 = vault.store(_CPF)
    _seed_subject(store, pid_v1, "v1")

    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v2")
    pm.reset_key_cache()
    pid_v2 = vault.store(_CPF)
    _seed_subject(store, pid_v2, "v2")
    assert pid_v1 != pid_v2

    report = erase_subject_by_cpf(_CPF, store=store, vault=vault, audit=audit)

    assert {(s.key_version, s.pseudonym_id) for s in report.subjects} == {("v1", pid_v1), ("v2", pid_v2)}
    assert report.totals == {
        "consents_revoked": 2, "snapshots_deleted": 2, "decisions_deleted": 2,
        "vault_shredded": 2, "completion_audited": True,
    }
    assert list(store.iter_snapshots({})) == []
    assert list(store.iter_decisions({})) == []
    assert all(c["status"] == "REVOKED" for c in store.iter_consents({}))
    assert all(v["shredded_at"] for v in store.iter_vault({}))
    erased = {e["pseudonym_id"] for e in audit_repo._store if e["event_type"] == "erasure.completed"}
    assert erased == {pid_v1, pid_v2}
    _no_cpf_anywhere(audit_repo._store, report.to_dict())


def test_erase_by_pseudonym_alone_would_leave_old_version(env, pseudonym_v2, monkeypatch):
    """Documenta o bug que o apagamento por CPF corrige."""
    from src.privacy.erasure import erase_subject

    store, vault, audit, _ = env
    pm = pseudonym_v2
    pid_v1 = vault.store(_CPF)
    _seed_subject(store, pid_v1, "v1")
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v2")
    pm.reset_key_cache()

    erase_subject(pm.pseudonymize_cpf(_CPF), store=store, vault=vault, audit=audit)
    assert len(list(store.iter_snapshots({"pseudonym_id": pid_v1}))) == 1


def test_erase_by_cpf_is_idempotent(env):
    store, vault, audit, _ = env
    pid = vault.store(_CPF)
    _seed_subject(store, pid, "a")
    erase_subject_by_cpf(_CPF, store=store, vault=vault, audit=audit)
    second = erase_subject_by_cpf(_CPF, store=store, vault=vault, audit=audit)
    assert second.totals == {
        "consents_revoked": 0, "snapshots_deleted": 0, "decisions_deleted": 0,
        "vault_shredded": 0, "completion_audited": True,
    }


def test_erase_by_cpf_missing_key_deletes_nothing(env, monkeypatch):
    from src.privacy import pseudonymization as pm

    store, vault, audit, audit_repo = env
    pid = vault.store(_CPF)
    _seed_subject(store, pid, "a")
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v9")
    pm.reset_key_cache()
    try:
        with pytest.raises(pm.PseudonymizationKeyError):
            erase_subject_by_cpf(_CPF, store=store, vault=vault, audit=audit)
    finally:
        monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
        pm.reset_key_cache()
    assert len(list(store.iter_snapshots({}))) == 1
    assert audit_repo._store == []


# ---------------------------------------------------------------------------
# Exportação
# ---------------------------------------------------------------------------

def test_export_returns_all_subject_data_without_secrets(env):
    store, vault, audit, audit_repo = env
    pid = vault.store(_CPF)
    _seed_subject(store, pid, "a")
    _seed_subject(store, "b" * 64, "outro")  # outro titular
    vault.reveal(pid, purpose="SERASA_QUERY", actor="agent1")

    export = export_subject([(pid, "v1")], store=store, audit=audit)

    (subject,) = export["subjects"]
    assert subject["pseudonym_id"] == pid
    assert [c["_id"] for c in subject["consents"]] == ["c-a"]
    assert [s["_id"] for s in subject["snapshots"]] == ["s-a"]
    assert [d["_id"] for d in subject["decisions"]] == ["d-a"]
    assert all("_rev" not in d for d in subject["consents"] + subject["snapshots"])
    assert subject["vault"]["exists"] is True and subject["vault"]["shredded_at"] is None
    assert set(subject["vault"]) <= {"exists", "created_at", "key_version", "shredded_at",
                                     "previously_shredded_at"}
    assert [e["event_type"] for e in subject["audit_events"]] == ["vault.reveal"]
    assert export["sharing"]["open_finance_institutions"] == ["bb", "itau"]
    assert export["sharing"]["serasa_queries"] == 1
    _no_cpf_anywhere(export)
    for secret in ("wrapped_dek", "cpf_ciphertext", "dek_nonce"):
        assert secret not in json.dumps(export)

    exported = [e for e in audit_repo._store if e["event_type"] == "subject.access_exported"]
    assert len(exported) == 1 and exported[0]["pseudonym_id"] == pid
    assert exported[0]["snapshots"] == 1 and exported[0]["trace_id"] in export["trace_ids"]


def test_export_unknown_subject_is_empty_but_audited(env):
    store, _, audit, audit_repo = env
    export = export_subject([("c" * 64, "v1")], store=store, audit=audit)
    subject = export["subjects"][0]
    assert subject["vault"] == {"exists": False}
    assert subject["consents"] == subject["snapshots"] == subject["decisions"] == []
    assert [e["event_type"] for e in audit_repo._store] == ["subject.access_exported"]


def test_export_without_audit_returns_nothing():
    class _FailingRepo:
        def put(self, doc):
            raise RuntimeError("offline")

        def find(self, selector, limit=25):
            return []

    store = InMemoryRetentionStore()
    _seed_subject(store, "a" * 64, "a")
    with pytest.raises(AuditWriteError):
        export_subject([("a" * 64, "v1")], store=store, audit=AuditLogger(repository=_FailingRepo()))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(env):
    store, vault, audit, _ = env
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_vault] = lambda: vault
    app.dependency_overrides[get_audit] = lambda: audit
    yield TestClient(app)
    app.dependency_overrides.clear()


_H = {"X-Internal-Token": _TOKEN}


@pytest.mark.parametrize("cpf", [_CPF, _CPF_DIGITS, "529 982 247 25"])
def test_api_erase_by_cpf(client, env, cpf):
    store, vault, _, _ = env
    pid = vault.store(_CPF)
    _seed_subject(store, pid, "a")
    resp = client.post("/internal/erase", json={"cpf": cpf}, headers=_H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["snapshots_deleted"] == 1
    assert body["subjects"][0]["pseudonym_id"] == pid
    assert _CPF_DIGITS not in resp.text


@pytest.mark.parametrize("payload", [
    {"cpf": "52998224726"},          # DV inválido
    {"cpf": "123"},
    {"cpf": 52998224725},            # número, não string
    {"documento": _CPF_DIGITS},
    ["52998224725"],
])
def test_api_erase_by_cpf_invalid_never_echoes(client, payload):
    resp = client.post("/internal/erase", json=payload, headers=_H)
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_CPF"
    assert "5299822472" not in resp.text


def test_api_erase_by_cpf_invalid_json(client):
    resp = client.post("/internal/erase", content=b"cpf=52998224725", headers=_H)
    assert resp.status_code == 422
    assert "52998224725" not in resp.text


def test_api_erase_by_cpf_requires_token(client):
    resp = client.post("/internal/erase", json={"cpf": _CPF_DIGITS})
    assert resp.status_code == 401


def test_api_erase_by_cpf_audit_unavailable(client, env):
    class _FailingRepo:
        def put(self, doc):
            raise RuntimeError("offline")

        def find(self, selector, limit=25):
            return []

    store, vault, _, _ = env
    pid = vault.store(_CPF)
    _seed_subject(store, pid, "a")
    app.dependency_overrides[get_audit] = lambda: AuditLogger(repository=_FailingRepo())
    resp = client.post("/internal/erase", json={"cpf": _CPF_DIGITS}, headers=_H)
    assert resp.status_code == 503 and resp.json()["code"] == "AUDIT_UNAVAILABLE"
    assert len(list(store.iter_snapshots({}))) == 1


def test_api_erase_by_cpf_key_unavailable(client, monkeypatch):
    from src.privacy import pseudonymization as pm

    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v9")
    pm.reset_key_cache()
    try:
        resp = client.post("/internal/erase", json={"cpf": _CPF_DIGITS}, headers=_H)
    finally:
        monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
        pm.reset_key_cache()
    assert resp.status_code == 503 and resp.json()["code"] == "PSEUDONYM_KEY_UNAVAILABLE"


def test_api_erase_by_pseudonym_route_still_works(client, env):
    store, vault, _, _ = env
    pid = vault.store(_CPF)
    _seed_subject(store, pid, "a")
    resp = client.post(f"/internal/erase/{pid}", headers=_H)
    assert resp.status_code == 200 and resp.json()["snapshots_deleted"] == 1


def test_api_export_by_cpf_and_by_pseudonym(client, env):
    store, vault, _, _ = env
    pid = vault.store(_CPF)
    _seed_subject(store, pid, "a")

    by_cpf = client.post("/internal/subject/export", json={"cpf": _CPF}, headers=_H)
    assert by_cpf.status_code == 200
    assert by_cpf.json()["subjects"][0]["pseudonym_id"] == pid
    assert by_cpf.json()["subjects"][0]["key_version"] == "v1"
    assert _CPF_DIGITS not in by_cpf.text

    by_pid = client.post("/internal/subject/export", json={"pseudonym_id": pid}, headers=_H)
    assert by_pid.status_code == 200
    assert [s["_id"] for s in by_pid.json()["subjects"][0]["snapshots"]] == ["s-a"]


def test_api_export_by_cpf_covers_old_key_versions(client, env, pseudonym_v2, monkeypatch):
    store, vault, _, _ = env
    pm = pseudonym_v2
    pid_v1 = vault.store(_CPF)
    _seed_subject(store, pid_v1, "v1")
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v2")
    pm.reset_key_cache()

    resp = client.post("/internal/subject/export", json={"cpf": _CPF}, headers=_H)
    subjects = {s["key_version"]: s for s in resp.json()["subjects"]}
    assert set(subjects) == {"v1", "v2"}
    assert len(subjects["v1"]["snapshots"]) == 1
    assert subjects["v2"]["snapshots"] == []


@pytest.mark.parametrize("payload", [
    {}, {"cpf": "111"}, {"pseudonym_id": "xyz"}, {"pseudonym_id": _CPF_DIGITS},
])
def test_api_export_invalid_subject(client, payload):
    resp = client.post("/internal/subject/export", json=payload, headers=_H)
    assert resp.status_code == 422
    assert resp.json()["code"] in {"INVALID_CPF", "INVALID_SUBJECT"}
    assert _CPF_DIGITS not in resp.text


def test_api_export_requires_token(client):
    assert client.post("/internal/subject/export", json={"cpf": _CPF}).status_code == 401


# ---------------------------------------------------------------------------
# Log de falha de autenticação
# ---------------------------------------------------------------------------

def test_missing_token_emits_auth_failed_line(client, audit_log_lines):
    resp = client.post("/internal/retention/run")
    assert resp.status_code == 401
    (line,) = [json.loads(x) for x in audit_log_lines]
    assert line["event_type"] == "internal.auth_failed"
    assert line["reason"] == "missing_token"
    assert line["method"] == "POST"
    assert line["route"] == "/internal/retention/run"
    assert line["persisted"] is False


def test_invalid_token_line_has_no_token_and_no_path_values(client, audit_log_lines):
    resp = client.post(f"/internal/erase/{_CPF_DIGITS}", headers={"X-Internal-Token": "chute-errado"})
    assert resp.status_code == 401
    (raw,) = audit_log_lines
    line = json.loads(raw)
    assert line["reason"] == "invalid_token"
    assert line["route"] == "/internal/erase/{pseudonym_id}"
    assert "chute-errado" not in raw
    assert _CPF_DIGITS not in raw


def test_auth_failure_is_not_persisted(client, env, audit_log_lines):
    _, _, _, audit_repo = env
    client.post("/internal/erase", json={"cpf": _CPF_DIGITS})
    assert audit_repo._store == []
    assert len(audit_log_lines) == 1


def test_valid_token_emits_no_auth_failed(client, audit_log_lines):
    client.post("/internal/retention/run", headers=_H)
    assert not any("internal.auth_failed" in x for x in audit_log_lines)


def test_security_event_with_pii_is_reduced(audit_log_lines):
    from src.privacy.audit import emit_security_event

    emit_security_event("internal.auth_failed", note=f"cpf {_CPF}")
    line = json.loads(audit_log_lines[0])
    assert line == {"event_type": "internal.auth_failed", "recorded_at": line["recorded_at"],
                    "pii_blocked": True}
