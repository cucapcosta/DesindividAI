"""
Testes da trilha de auditoria — M4, Fase 5.

Todos sem rede: InMemoryAuditRepository + fake COS client em memória.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.privacy.audit import (
    AuditLogger,
    CosAuditSink,
    InMemoryAuditRepository,
    NoopGovernanceSink,
    get_cos_sink,
)
from src.privacy.identity_vault import IdentityVault, InMemoryVaultRepository
from src.privacy.key_provider import EnvKeyProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fixed_clock(ts: str = "2025-06-15T12:00:00Z"):
    """Retorna um callable que sempre devolve ts."""
    return lambda: ts


def _make_logger(
    repo=None,
    cos_sink=None,
    governance=None,
    clock=None,
):
    if repo is None:
        repo = InMemoryAuditRepository()
    return AuditLogger(
        repository=repo,
        cos_sink=cos_sink,
        governance=governance or NoopGovernanceSink(),
        clock=clock or _fixed_clock(),
    )


class FakeCosClient:
    """Fake ibm_boto3 client que grava chamadas put_object em memória."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str):
        self.objects[Key] = Body


def _make_cos_sink(prefix: str = "audit/") -> tuple[CosAuditSink, FakeCosClient]:
    fake = FakeCosClient()
    sink = CosAuditSink(bucket="test-bucket", prefix=prefix, client=fake)
    return sink, fake


# ---------------------------------------------------------------------------
# 1. trace_id retornado e registro gravado com campos corretos
# ---------------------------------------------------------------------------

def test_record_event_returns_trace_id_and_stores_record():
    repo = InMemoryAuditRepository()
    al = _make_logger(repo=repo, clock=_fixed_clock("2025-06-15T12:00:00Z"))

    trace_id = al.record_event(
        "vault.reveal",
        pseudonym_id="cpf:abc123",
        actor="agent1",
        purpose="SERASA_QUERY",
        ok=True,
    )

    assert isinstance(trace_id, str) and len(trace_id) == 36  # UUID4

    docs = repo.find({"trace_id": trace_id})
    assert len(docs) == 1
    doc = docs[0]
    assert doc["_id"] == trace_id
    assert doc["type"] == "audit_event"
    assert doc["trace_id"] == trace_id
    assert doc["recorded_at"] == "2025-06-15T12:00:00Z"
    assert doc["event_type"] == "vault.reveal"
    assert doc["pseudonym_id"] == "cpf:abc123"
    assert doc["actor"] == "agent1"
    assert doc["purpose"] == "SERASA_QUERY"
    assert doc["ok"] is True


# ---------------------------------------------------------------------------
# 2. event_type inválido → ValueError
# ---------------------------------------------------------------------------

def test_invalid_event_type_raises_value_error():
    al = _make_logger()
    with pytest.raises(ValueError, match="event_type inválido"):
        al.record_event(
            "invalid.event",
            pseudonym_id="cpf:abc",
            actor="agent1",
        )


# ---------------------------------------------------------------------------
# 3. Registro com CPF → original NÃO gravado; audit.pii_blocked gravado
# ---------------------------------------------------------------------------

def test_pii_blocked_when_cpf_in_fields(caplog):
    repo = InMemoryAuditRepository()
    cos_sink, fake_cos = _make_cos_sink()
    al = _make_logger(repo=repo, cos_sink=cos_sink)

    import logging
    with caplog.at_level(logging.ERROR, logger="src.privacy.audit"):
        trace_id = al.record_event(
            "vault.reveal",
            pseudonym_id="cpf:abc123",
            actor="agent1",
            extra_field="123.456.789-09",  # CPF mascarado → PII!
        )

    # O trace_id retornado corresponde ao evento bloqueado
    docs = repo.find({"trace_id": trace_id})
    assert len(docs) == 1
    blocked = docs[0]
    assert blocked["event_type"] == "audit.pii_blocked"
    assert blocked["blocked_event_type"] == "vault.reveal"
    assert "violation_path" in blocked

    # CPF NÃO deve aparecer em nenhum dado gravado no repositório
    all_data = json.dumps(repo._store)
    assert "123.456.789-09" not in all_data

    # CPF NÃO deve aparecer nos dados do COS
    all_cos_data = b"".join(fake_cos.objects.values()).decode("utf-8")
    assert "123.456.789-09" not in all_cos_data

    # CPF NÃO deve aparecer nos logs
    assert "123.456.789-09" not in caplog.text

    # O log registrou o path (sem o valor)
    assert "audit.pii_blocked" in caplog.text


def test_pii_blocked_original_not_in_any_destination():
    """Garante que o registro original (com CPF) NÃO foi para nenhum destino."""
    repo = InMemoryAuditRepository()
    cos_sink, fake_cos = _make_cos_sink()
    al = _make_logger(repo=repo, cos_sink=cos_sink)

    al.record_event(
        "vault.reveal",
        pseudonym_id="cpf:abc",
        actor="agent1",
        bad_field="123.456.789-09",
    )

    # Somente 1 documento no repo: o audit.pii_blocked
    assert len(repo._store) == 1
    assert repo._store[0]["event_type"] == "audit.pii_blocked"

    # O COS também deve ter somente 1 objeto (o bloqueado)
    assert len(fake_cos.objects) == 1
    cos_body = json.loads(list(fake_cos.objects.values())[0])
    assert cos_body["event_type"] == "audit.pii_blocked"
    assert "bad_field" not in cos_body


# ---------------------------------------------------------------------------
# 4. COS: chave no formato correto e corpo igual ao registro
# ---------------------------------------------------------------------------

def test_cos_key_format_and_body():
    repo = InMemoryAuditRepository()
    cos_sink, fake_cos = _make_cos_sink(prefix="audit/")
    clock = _fixed_clock("2025-06-15T12:00:00Z")
    al = _make_logger(repo=repo, cos_sink=cos_sink, clock=clock)

    trace_id = al.record_event(
        "vault.reveal",
        pseudonym_id="cpf:test",
        actor="agent1",
        purpose="SERASA_QUERY",
        ok=True,
    )

    # Chave no formato audit/AAAA/MM/DD/<trace_id>.json
    expected_key = f"audit/2025/06/15/{trace_id}.json"
    assert expected_key in fake_cos.objects, (
        f"Chave esperada {expected_key!r} não encontrada. "
        f"Chaves presentes: {list(fake_cos.objects.keys())}"
    )

    # Corpo JSON igual ao registro gravado no repo
    body = json.loads(fake_cos.objects[expected_key])
    assert body["trace_id"] == trace_id
    assert body["event_type"] == "vault.reveal"
    assert body["recorded_at"] == "2025-06-15T12:00:00Z"


def test_cos_key_uses_recorded_at_date():
    """O ano/mês/dia da chave COS vem de recorded_at, não da data do sistema."""
    repo = InMemoryAuditRepository()
    cos_sink, fake_cos = _make_cos_sink()
    # Clock fixo em data diferente de hoje
    clock = _fixed_clock("2020-01-31T23:59:59Z")
    al = _make_logger(repo=repo, cos_sink=cos_sink, clock=clock)

    trace_id = al.record_event(
        "retention.run",
        pseudonym_id=None,
        actor="retention_job",
    )

    expected_key = f"audit/2020/01/31/{trace_id}.json"
    assert expected_key in fake_cos.objects


# ---------------------------------------------------------------------------
# 5. Falha do Cloudant não impede COS; falha do COS não propaga
# ---------------------------------------------------------------------------

def test_cloudant_failure_does_not_block_cos(caplog):
    """Falha do Cloudant → COS ainda recebe o registro."""

    class FailingRepo:
        def put(self, doc):
            raise RuntimeError("Cloudant offline")

        def find(self, selector, limit=25):
            return []

    cos_sink, fake_cos = _make_cos_sink()
    al = _make_logger(repo=FailingRepo(), cos_sink=cos_sink)

    import logging
    with caplog.at_level(logging.ERROR, logger="src.privacy.audit"):
        trace_id = al.record_event(
            "vault.reveal",
            pseudonym_id="cpf:abc",
            actor="agent1",
            ok=True,
        )

    # trace_id retornado mesmo com falha do Cloudant
    assert isinstance(trace_id, str) and len(trace_id) == 36

    # COS recebeu o registro
    assert len(fake_cos.objects) == 1

    # Erro logado (sem PII)
    assert "cloudant" in caplog.text.lower()


def test_cos_failure_does_not_propagate(caplog):
    """Falha do COS → trace_id retornado e erro logado."""

    class FailingCosClient:
        def put_object(self, **kwargs):
            raise RuntimeError("COS timeout")

    repo = InMemoryAuditRepository()
    cos_sink = CosAuditSink(bucket="b", client=FailingCosClient())
    al = _make_logger(repo=repo, cos_sink=cos_sink)

    import logging
    with caplog.at_level(logging.ERROR, logger="src.privacy.audit"):
        trace_id = al.record_event(
            "vault.reveal",
            pseudonym_id="cpf:abc",
            actor="agent1",
            ok=True,
        )

    assert isinstance(trace_id, str) and len(trace_id) == 36

    # Cloudant recebeu o registro (COS falhou mas não impediu)
    docs = repo.find({"trace_id": trace_id})
    assert len(docs) == 1

    # Erro logado
    assert "cos" in caplog.text.lower()


def test_both_destinations_fail_returns_trace_id_and_logs_two_errors(caplog):
    """Cloudant e COS ambos falhando → retorna trace_id e loga 2 erros."""

    class FailingRepo:
        def put(self, doc):
            raise RuntimeError("Cloudant offline")

        def find(self, selector, limit=25):
            return []

    class FailingCosClient:
        def put_object(self, **kwargs):
            raise RuntimeError("COS timeout")

    cos_sink = CosAuditSink(bucket="b", client=FailingCosClient())
    al = _make_logger(repo=FailingRepo(), cos_sink=cos_sink)

    import logging
    with caplog.at_level(logging.ERROR, logger="src.privacy.audit"):
        trace_id = al.record_event(
            "vault.reveal",
            pseudonym_id="cpf:abc",
            actor="agent1",
            ok=True,
        )

    assert isinstance(trace_id, str) and len(trace_id) == 36

    # Dois erros logados (cloudant e cos)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) >= 2


# ---------------------------------------------------------------------------
# 6. get_cos_sink() → None sem credenciais
# ---------------------------------------------------------------------------

def test_get_cos_sink_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("COS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("COS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("COS_BUCKET_NAME", raising=False)

    # Reset lru_cache is not needed here; get_cos_sink is not cached
    result = get_cos_sink()
    assert result is None


def test_get_cos_sink_returns_none_missing_bucket(monkeypatch):
    monkeypatch.setenv("COS_ACCESS_KEY_ID", "fake_access")
    monkeypatch.setenv("COS_SECRET_ACCESS_KEY", "fake_secret")
    monkeypatch.delenv("COS_BUCKET_NAME", raising=False)

    result = get_cos_sink()
    assert result is None


def test_get_cos_sink_returns_none_missing_one_key(monkeypatch):
    monkeypatch.setenv("COS_ACCESS_KEY_ID", "fake_access")
    monkeypatch.delenv("COS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("COS_BUCKET_NAME", "my-bucket")

    result = get_cos_sink()
    assert result is None


# ---------------------------------------------------------------------------
# 7. Integração com IdentityVault: reveal gera evento vault.reveal gravado
# ---------------------------------------------------------------------------

def test_integration_with_identity_vault(monkeypatch):
    """
    reveal() no IdentityVault gera evento vault.reveal gravado no repositório
    de auditoria e sem CPF nos dados gravados.
    """
    # Configurar EnvKeyProvider com KEK de teste
    monkeypatch.setenv("VAULT_KEK_V1", "a" * 64)  # 32 bytes em hex
    monkeypatch.setenv("VAULT_KEK_ACTIVE_VERSION", "v1")

    from src.privacy.key_provider import EnvKeyProvider

    repo = InMemoryAuditRepository()
    al = AuditLogger(repository=repo, clock=_fixed_clock())

    vault_repo = InMemoryVaultRepository()
    vault = IdentityVault(
        repository=vault_repo,
        key_provider=EnvKeyProvider(),
        audit=al.record_event,
    )

    # Armazenar CPF (não grava auditoria em store)
    cpf = "529.982.247-25"  # CPF válido para teste
    pseudonym_id = vault.store(cpf)

    # reveal deve gerar evento vault.reveal
    revealed = vault.reveal(pseudonym_id, purpose="SERASA_QUERY", actor="agent1")
    assert revealed == "52998224725"  # CPF normalizado

    # Evento gravado no repositório de auditoria
    events = repo.find({"event_type": "vault.reveal"})
    assert len(events) == 1
    evt = events[0]
    assert evt["event_type"] == "vault.reveal"
    assert evt["pseudonym_id"] == pseudonym_id
    assert evt["actor"] == "agent1"
    assert evt["ok"] is True

    # CPF NÃO deve aparecer em nenhum dado gravado
    all_data = json.dumps(repo._store)
    assert "529.982.247-25" not in all_data
    assert "52998224725" not in all_data


def test_vault_reveal_audit_on_failure(monkeypatch):
    """reveal() após shred gera evento vault.reveal com ok=False."""
    monkeypatch.setenv("VAULT_KEK_V1", "b" * 64)
    monkeypatch.setenv("VAULT_KEK_ACTIVE_VERSION", "v1")

    from src.privacy.key_provider import EnvKeyProvider
    from src.privacy.identity_vault import VaultError

    repo = InMemoryAuditRepository()
    al = AuditLogger(repository=repo, clock=_fixed_clock())

    vault_repo = InMemoryVaultRepository()
    vault = IdentityVault(
        repository=vault_repo,
        key_provider=EnvKeyProvider(),
        audit=al.record_event,
    )

    cpf = "529.982.247-25"
    pseudonym_id = vault.store(cpf)
    vault.shred(pseudonym_id)

    with pytest.raises(VaultError):
        vault.reveal(pseudonym_id, purpose="SERASA_QUERY", actor="agent1")

    events = repo.find({"event_type": "vault.reveal"})
    assert len(events) == 1
    assert events[0]["ok"] is False


# ---------------------------------------------------------------------------
# 8. Todos os event_types permitidos são aceitos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("event_type", [
    "consent.expired",
    "consent.erasure_requested",
    "erasure.completed",
    "vault.reveal",
    "retention.run",
    "agent1.snapshot_persisted",
    "audit.pii_blocked",
])
def test_all_allowed_event_types_accepted(event_type):
    repo = InMemoryAuditRepository()
    al = _make_logger(repo=repo)
    trace_id = al.record_event(
        event_type,
        pseudonym_id="cpf:test",
        actor="test",
    )
    assert isinstance(trace_id, str) and len(trace_id) == 36
    docs = repo.find({"trace_id": trace_id})
    assert len(docs) == 1
    assert docs[0]["event_type"] == event_type


# ---------------------------------------------------------------------------
# 9. Repositório em memória: find com selector e limit
# ---------------------------------------------------------------------------

def test_in_memory_repository_find_and_limit():
    repo = InMemoryAuditRepository()
    for i in range(5):
        repo.put({
            "_id": str(i),
            "event_type": "vault.reveal",
            "actor": "agent1",
            "trace_id": str(i),
        })

    results = repo.find({"event_type": "vault.reveal"}, limit=3)
    assert len(results) == 3

    results_all = repo.find({"event_type": "vault.reveal"})
    assert len(results_all) == 5


# ---------------------------------------------------------------------------
# 10. PII bloqueado: violation_path no evento substituto, nunca o valor
# ---------------------------------------------------------------------------

def test_pii_blocked_violation_path_present_not_value(caplog):
    repo = InMemoryAuditRepository()
    al = _make_logger(repo=repo)

    import logging
    with caplog.at_level(logging.ERROR, logger="src.privacy.audit"):
        al.record_event(
            "vault.reveal",
            pseudonym_id="cpf:abc",
            actor="agent1",
            cpf="123.456.789-09",  # chave proibida
        )

    blocked = repo.find({"event_type": "audit.pii_blocked"})
    assert len(blocked) == 1
    assert "violation_path" in blocked[0]
    # O path deve identificar a chave, não o valor
    assert "cpf" in blocked[0]["violation_path"]
    # O valor CPF nunca deve aparecer em nenhum log ou registro
    assert "123.456.789-09" not in caplog.text
    assert "123.456.789-09" not in json.dumps(repo._store)


# ---------------------------------------------------------------------------
# 11. PII em pseudonym_id → blocked_record não vaza o CPF
# ---------------------------------------------------------------------------

def test_pii_in_pseudonym_id_no_leak(caplog):
    """
    CPF passado como pseudonym_id → evento audit.pii_blocked gravado com
    pseudonym_id=None; nenhum destino (Cloudant, COS, log) contém o CPF.
    """
    import logging

    repo = InMemoryAuditRepository()
    cos_sink, fake_cos = _make_cos_sink()
    al = _make_logger(repo=repo, cos_sink=cos_sink)

    CPF = "123.456.789-09"

    with caplog.at_level(logging.ERROR, logger="src.privacy.audit"):
        trace_id = al.record_event(
            "vault.reveal",
            pseudonym_id=CPF,
            actor="agent1",
        )

    # Exactly one doc in Cloudant: the blocked event
    assert len(repo._store) == 1
    blocked = repo._store[0]
    assert blocked["event_type"] == "audit.pii_blocked"
    assert blocked["pseudonym_id"] is None
    assert blocked["violation_path"] == "pseudonym_id"

    # CPF must not appear anywhere
    assert CPF not in json.dumps(repo._store)
    all_cos = b"".join(fake_cos.objects.values()).decode("utf-8")
    assert CPF not in all_cos
    assert CPF not in caplog.text


# ---------------------------------------------------------------------------
# 12. Campos reservados em **fields → ValueError
# ---------------------------------------------------------------------------

def test_reserved_fields_raise_value_error():
    """Passar campo reservado em **fields deve levantar ValueError antes de gravar."""
    al = _make_logger()
    # event_type, pseudonym_id and actor are explicit parameters of record_event;
    # Python raises TypeError before our code runs if duplicated, so only test
    # the keys that can actually reach **fields.
    reserved_keys = ["_id", "_rev", "type", "trace_id", "recorded_at"]
    for key in reserved_keys:
        with pytest.raises(ValueError, match="reservad"):
            al.record_event(
                "vault.reveal",
                pseudonym_id="cpf:abc",
                actor="agent1",
                **{key: "value"},
            )


# ---------------------------------------------------------------------------
# 13. Mensagem de log inclui destino, trace_id, event_type e exc_type
# ---------------------------------------------------------------------------

def test_log_message_contains_context_fields(caplog):
    """Mensagem de erro deve incluir destino=, trace_id=, event_type= e exc=."""
    import logging

    class FailingRepo:
        def put(self, doc):
            raise RuntimeError("boom")

        def find(self, selector, limit=25):
            return []

    al = _make_logger(repo=FailingRepo())

    with caplog.at_level(logging.ERROR, logger="src.privacy.audit"):
        al.record_event(
            "vault.reveal",
            pseudonym_id="cpf:abc",
            actor="agent1",
        )

    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_msgs, "Nenhum erro logado"
    msg = error_msgs[0]
    assert "destino=cloudant" in msg
    assert "event_type=vault.reveal" in msg
    assert "exc=RuntimeError" in msg
    # trace_id deve estar presente (valor dinâmico, só verificar a chave)
    assert "trace_id=" in msg
