"""
Testes unitários para src/privacy/identity_vault.py — Fase 3 do M4.

Todos os testes usam InMemoryVaultRepository (sem rede).
"""

from __future__ import annotations

import base64

import pytest

from src.privacy.identity_vault import (
    IdentityVault,
    InMemoryVaultRepository,
    VaultError,
)
from src.privacy.key_provider import EnvKeyProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CPF = "123.456.789-09"
_CPF2 = "987.654.321-00"

# CPF normalizado (só dígitos) — o que o vault devolve após decifrar
_CPF_DIGITS = "12345678909"
_CPF2_DIGITS = "98765432100"


def _make_vault(audit=None):
    """Cria um IdentityVault com repositório em memória e EnvKeyProvider."""
    repo = InMemoryVaultRepository()
    kp = EnvKeyProvider()
    return IdentityVault(repository=repo, key_provider=kp, audit=audit), repo


# ---------------------------------------------------------------------------
# 1. Round-trip: store → reveal
# ---------------------------------------------------------------------------

def test_round_trip():
    vault, _ = _make_vault()
    pseudonym_id = vault.store(_CPF)
    assert pseudonym_id  # não vazio
    recovered = vault.reveal(pseudonym_id, purpose="SERASA_QUERY", actor="test")
    assert recovered == _CPF_DIGITS  # CPF normalizado (só dígitos)


def test_round_trip_legal_request():
    vault, _ = _make_vault()
    pseudonym_id = vault.store(_CPF)
    recovered = vault.reveal(pseudonym_id, purpose="LEGAL_REQUEST", actor="compliance")
    assert recovered == _CPF_DIGITS


# ---------------------------------------------------------------------------
# 2. store idempotente — mesmo pseudonym_id, documento não regravado
# ---------------------------------------------------------------------------

def test_store_idempotent_returns_same_pseudonym():
    vault, repo = _make_vault()
    pid1 = vault.store(_CPF)
    pid2 = vault.store(_CPF)
    assert pid1 == pid2


def test_store_idempotent_does_not_overwrite_document():
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    doc_before = repo.get(pid)
    created_at_before = doc_before["created_at"]

    # Segunda chamada: não deve regravar
    vault.store(_CPF)
    doc_after = repo.get(pid)
    assert doc_after["created_at"] == created_at_before


# ---------------------------------------------------------------------------
# 3. reveal após shred → VaultError
# ---------------------------------------------------------------------------

def test_reveal_after_shred_raises():
    vault, _ = _make_vault()
    pid = vault.store(_CPF)
    vault.shred(pid)
    with pytest.raises(VaultError):
        vault.reveal(pid, purpose="SERASA_QUERY", actor="test")


# ---------------------------------------------------------------------------
# 4. Integridade: AAD trocado → falha de autenticação
# ---------------------------------------------------------------------------

def test_aad_mismatch_raises_vault_error():
    """
    Trocar o _id do documento (AAD) deve causar falha ao decifrar.
    Simula um documento sendo copiado para outro pseudonym_id.
    """
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    doc = repo.get(pid)

    # Criar um segundo documento com _id diferente mas mesmo ciphertext
    other_pid = "000000000000000000000000000000000000000000000000000000000000dead"
    tampered = dict(doc)
    tampered["_id"] = other_pid
    repo.put(tampered)

    with pytest.raises(VaultError):
        vault.reveal(other_pid, purpose="SERASA_QUERY", actor="test")


# ---------------------------------------------------------------------------
# 5. Ciphertext adulterado → falha de autenticação
# ---------------------------------------------------------------------------

def test_tampered_ciphertext_raises_vault_error():
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    doc = repo.get(pid)

    # Corrompe o ciphertext do CPF (inverte o último byte)
    ct_bytes = base64.b64decode(doc["cpf_ciphertext"])
    bad_ct = ct_bytes[:-1] + bytes([ct_bytes[-1] ^ 0xFF])
    doc["cpf_ciphertext"] = base64.b64encode(bad_ct).decode()
    repo.put(doc)

    with pytest.raises(VaultError):
        vault.reveal(pid, purpose="SERASA_QUERY", actor="test")


def test_tampered_wrapped_dek_raises_vault_error():
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    doc = repo.get(pid)

    # Corrompe o wrapped_dek
    dek_bytes = base64.b64decode(doc["wrapped_dek"])
    bad_dek = dek_bytes[:-1] + bytes([dek_bytes[-1] ^ 0xFF])
    doc["wrapped_dek"] = base64.b64encode(bad_dek).decode()
    repo.put(doc)

    with pytest.raises(VaultError):
        vault.reveal(pid, purpose="SERASA_QUERY", actor="test")


# ---------------------------------------------------------------------------
# 6. purpose inválido → ValueError
# ---------------------------------------------------------------------------

def test_invalid_purpose_raises_value_error():
    vault, _ = _make_vault()
    pid = vault.store(_CPF)
    with pytest.raises(ValueError, match="purpose inválido"):
        vault.reveal(pid, purpose="UNKNOWN_PURPOSE", actor="test")


def test_empty_purpose_raises_value_error():
    vault, _ = _make_vault()
    pid = vault.store(_CPF)
    with pytest.raises(ValueError):
        vault.reveal(pid, purpose="", actor="test")


# ---------------------------------------------------------------------------
# 7. Auditoria: chamada em sucesso e em falha, sem CPF nos argumentos
# ---------------------------------------------------------------------------

def test_audit_called_on_success():
    events = []

    def audit(event_type, **fields):
        events.append({"event_type": event_type, **fields})

    vault, _ = _make_vault(audit=audit)
    pid = vault.store(_CPF)
    vault.reveal(pid, purpose="SERASA_QUERY", actor="tester")

    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "vault.reveal"
    assert ev["pseudonym_id"] == pid
    assert ev["actor"] == "tester"
    assert ev["purpose"] == "SERASA_QUERY"
    assert ev.get("ok") is True


def test_audit_called_on_failure_shredded():
    events = []

    def audit(event_type, **fields):
        events.append({"event_type": event_type, **fields})

    vault, _ = _make_vault(audit=audit)
    pid = vault.store(_CPF)
    vault.shred(pid)

    with pytest.raises(VaultError):
        vault.reveal(pid, purpose="SERASA_QUERY", actor="tester")

    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "vault.reveal"
    assert ev.get("ok") is False


def test_audit_called_on_failure_not_found():
    events = []

    def audit(event_type, **fields):
        events.append({"event_type": event_type, **fields})

    vault, _ = _make_vault(audit=audit)

    with pytest.raises(VaultError):
        vault.reveal("nonexistent_id", purpose="SERASA_QUERY", actor="tester")

    assert len(events) == 1
    assert events[0]["ok"] is False


def test_audit_no_cpf_in_arguments():
    """O CPF nunca deve aparecer nos argumentos passados ao audit."""
    captured_kwargs = {}

    def audit(event_type, **fields):
        captured_kwargs.update(fields)

    vault, _ = _make_vault(audit=audit)
    pid = vault.store(_CPF)
    vault.reveal(pid, purpose="SERASA_QUERY", actor="tester")

    # Verifica que nenhum valor nos kwargs é o CPF em qualquer forma
    for v in captured_kwargs.values():
        assert _CPF_DIGITS not in str(v), f"CPF encontrado no audit: {v!r}"
        assert _CPF not in str(v), f"CPF mascarado encontrado no audit: {v!r}"


# ---------------------------------------------------------------------------
# 8. Documento persistido não contém CPF em claro
# ---------------------------------------------------------------------------

def test_document_does_not_contain_cpf_in_plain():
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    doc = repo.get(pid)

    # Converte o documento inteiro em string e verifica ausência do CPF
    doc_str = str(doc)
    assert _CPF_DIGITS not in doc_str, "CPF em claro encontrado no documento!"
    assert _CPF not in doc_str, "CPF mascarado encontrado no documento!"


def test_document_fields_are_base64_blobs():
    """Garante que cpf_ciphertext, nonce, wrapped_dek e dek_nonce são base64."""
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    doc = repo.get(pid)

    for field in ("cpf_ciphertext", "nonce", "wrapped_dek", "dek_nonce"):
        assert field in doc, f"Campo {field!r} ausente no documento"
        # Não deve lançar exceção ao decodificar base64
        decoded = base64.b64decode(doc[field])
        assert len(decoded) > 0


# ---------------------------------------------------------------------------
# 9. shred — idempotência e tombstone
# ---------------------------------------------------------------------------

def test_shred_returns_true_first_time():
    vault, _ = _make_vault()
    pid = vault.store(_CPF)
    assert vault.shred(pid) is True


def test_shred_returns_false_already_shredded():
    vault, _ = _make_vault()
    pid = vault.store(_CPF)
    vault.shred(pid)
    assert vault.shred(pid) is False


def test_shred_returns_false_nonexistent():
    vault, _ = _make_vault()
    assert vault.shred("nonexistent_id") is False


def test_shred_leaves_tombstone():
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    vault.shred(pid)
    doc = repo.get(pid)
    assert doc is not None, "Tombstone deve existir"
    assert doc.get("shredded_at") is not None
    assert "wrapped_dek" not in doc
    assert "dek_nonce" not in doc
    assert "cpf_ciphertext" not in doc
    assert "nonce" not in doc


# ---------------------------------------------------------------------------
# 10. Recriação após tombstone
# ---------------------------------------------------------------------------

def test_store_recreates_after_shred():
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    vault.shred(pid)

    # Recria com nova DEK
    pid2 = vault.store(_CPF)
    assert pid2 == pid  # mesmo pseudonym_id

    doc = repo.get(pid)
    assert doc.get("shredded_at") is None, "Novo documento deve estar ativo"
    assert "cpf_ciphertext" in doc
    assert "wrapped_dek" in doc
    assert doc.get("previously_shredded_at") is not None


def test_round_trip_after_recreation():
    vault, _ = _make_vault()
    pid = vault.store(_CPF)
    vault.shred(pid)
    vault.store(_CPF)  # recria
    recovered = vault.reveal(pid, purpose="SERASA_QUERY", actor="test")
    assert recovered == _CPF_DIGITS


def test_recreation_uses_new_dek():
    """A DEK após recriação deve ser diferente da DEK original."""
    vault, repo = _make_vault()
    pid = vault.store(_CPF)
    doc1 = repo.get(pid)
    wrapped_dek_1 = doc1["wrapped_dek"]

    vault.shred(pid)
    vault.store(_CPF)
    doc2 = repo.get(pid)
    wrapped_dek_2 = doc2["wrapped_dek"]

    # DEKs envolvidas devem ser diferentes (aleatoriedade do nonce e da DEK)
    assert wrapped_dek_1 != wrapped_dek_2


# ---------------------------------------------------------------------------
# 11. resolve_for_serasa
# ---------------------------------------------------------------------------

def test_resolve_for_serasa():
    vault, _ = _make_vault()
    pid = vault.store(_CPF)
    recovered = vault.resolve_for_serasa(pid)
    assert recovered == _CPF_DIGITS


def test_resolve_for_serasa_after_shred_raises():
    vault, _ = _make_vault()
    pid = vault.store(_CPF)
    vault.shred(pid)
    with pytest.raises(VaultError):
        vault.resolve_for_serasa(pid)


# ---------------------------------------------------------------------------
# 12. Dois titulares diferentes — DEKs independentes
# ---------------------------------------------------------------------------

def test_two_holders_independent():
    vault, repo = _make_vault()
    pid1 = vault.store(_CPF)
    pid2 = vault.store(_CPF2)

    assert pid1 != pid2

    cpf1 = vault.reveal(pid1, purpose="SERASA_QUERY", actor="test")
    cpf2 = vault.reveal(pid2, purpose="SERASA_QUERY", actor="test")

    assert cpf1 == _CPF_DIGITS
    assert cpf2 == _CPF2_DIGITS

    doc1 = repo.get(pid1)
    doc2 = repo.get(pid2)
    assert doc1["wrapped_dek"] != doc2["wrapped_dek"]


# ---------------------------------------------------------------------------
# Ajustes M4 — Fase 4 (PARTE B)
# ---------------------------------------------------------------------------

# B1: reveal com purpose inválido deve chamar audit ANTES de levantar ValueError
# -------------------------------------------------------------------------------

def test_audit_called_before_raise_on_invalid_purpose():
    """
    Garante que audit("vault.reveal", ..., ok=False, reason="invalid_purpose")
    é chamado ANTES de ValueError ser levantado.
    """
    events = []

    def audit(event_type, **fields):
        events.append({"event_type": event_type, **fields})

    vault, _ = _make_vault(audit=audit)
    pid = vault.store(_CPF)

    with pytest.raises(ValueError, match="purpose inválido"):
        vault.reveal(pid, purpose="INVALID_PURPOSE", actor="test")

    # Auditoria deve ter sido chamada
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "vault.reveal"
    assert ev["pseudonym_id"] == pid
    assert ev["actor"] == "test"
    assert ev["purpose"] == "INVALID_PURPOSE"
    assert ev["ok"] is False
    assert ev.get("reason") == "invalid_purpose"


def test_audit_called_before_raise_on_empty_purpose():
    """purpose vazio também deve disparar o audit antes do raise."""
    events = []

    def audit(event_type, **fields):
        events.append({"event_type": event_type, **fields})

    vault, _ = _make_vault(audit=audit)
    pid = vault.store(_CPF)

    with pytest.raises(ValueError):
        vault.reveal(pid, purpose="", actor="system")

    assert len(events) == 1
    assert events[0]["ok"] is False
    assert events[0].get("reason") == "invalid_purpose"


# B2: store com 409 ApiException na criação concorrente
# -------------------------------------------------------------------------------

class _FakeApiException(Exception):
    """Simula ibm_cloud_sdk_core.ApiException com atributo .code."""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"ApiException code={code}")


class _ConflictOnFirstPutRepository:
    """
    Repositório fake que levanta _FakeApiException(409) no PRIMEIRO put,
    depois persiste o documento normalmente (como se outro processo tivesse
    criado o documento entre o get e o put).
    """

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self._put_calls = 0

    def get(self, pseudonym_id: str):
        doc = self._store.get(pseudonym_id)
        return dict(doc) if doc is not None else None

    def put(self, doc: dict) -> None:
        self._put_calls += 1
        if self._put_calls == 1:
            # Simula que outro processo criou o documento com sucesso
            # (o vault vai ler e retornar esse documento)
            self._store[doc["_id"]] = dict(doc)
            raise _FakeApiException(409)
        self._store[doc["_id"]] = dict(doc)

    def delete(self, pseudonym_id: str) -> None:
        self._store.pop(pseudonym_id, None)


def test_store_409_concurrent_active_returns_pseudonym_id():
    """
    Quando put levanta uma exceção com code=409 e o documento está ativo,
    store deve retornar o pseudonym_id sem propagar a exceção.
    """
    from src.privacy.key_provider import EnvKeyProvider

    repo = _ConflictOnFirstPutRepository()
    kp = EnvKeyProvider()
    vault = IdentityVault(repository=repo, key_provider=kp)

    pid = vault.store(_CPF)
    assert pid  # retorna normalmente, sem levantar


def test_store_409_concurrent_returns_same_pseudonym_as_normal_store():
    """O pseudonym_id retornado no cenário 409 é idêntico ao gerado normalmente."""
    from src.privacy.key_provider import EnvKeyProvider
    from src.privacy.pseudonymization import pseudonymize_cpf

    repo = _ConflictOnFirstPutRepository()
    kp = EnvKeyProvider()
    vault = IdentityVault(repository=repo, key_provider=kp)

    expected_pid = pseudonymize_cpf(_CPF)
    actual_pid = vault.store(_CPF)
    assert actual_pid == expected_pid


def test_store_non_409_exception_is_reraised():
    """Exceções com código diferente de 409 devem ser propagadas normalmente."""
    from src.privacy.key_provider import EnvKeyProvider

    class _AlwaysFailRepository:
        def get(self, _):
            return None

        def put(self, _):
            raise _FakeApiException(500)

        def delete(self, _):
            pass

    repo = _AlwaysFailRepository()
    kp = EnvKeyProvider()
    vault = IdentityVault(repository=repo, key_provider=kp)

    with pytest.raises(_FakeApiException) as exc_info:
        vault.store(_CPF)
    assert exc_info.value.code == 500


# B3: get_vault_repository() usa ConfigurationError
# -------------------------------------------------------------------------------

def test_get_vault_repository_raises_configuration_error(monkeypatch):
    """
    Sem CLOUDANT_URL/CLOUDANT_APIKEY, get_vault_repository deve levantar
    ConfigurationError (de src/agents/agent1/config.py), não KeyProviderError.
    """
    from src.agents.agent1.config import ConfigurationError
    from src.privacy.identity_vault import get_vault_repository

    monkeypatch.delenv("CLOUDANT_URL", raising=False)
    monkeypatch.setenv("CLOUDANT_URL", "")
    monkeypatch.setenv("CLOUDANT_APIKEY", "")
    get_vault_repository.cache_clear()
    try:
        with pytest.raises(ConfigurationError, match="CLOUDANT_URL"):
            get_vault_repository()
    finally:
        get_vault_repository.cache_clear()


def test_get_vault_repository_not_key_provider_error(monkeypatch):
    """Garante que KeyProviderError NÃO é levantado (foi substituído)."""
    from src.privacy.key_provider import KeyProviderError
    from src.privacy.identity_vault import get_vault_repository

    monkeypatch.setenv("CLOUDANT_URL", "")
    monkeypatch.setenv("CLOUDANT_APIKEY", "")
    get_vault_repository.cache_clear()
    try:
        with pytest.raises(Exception) as exc_info:
            get_vault_repository()
        assert not isinstance(exc_info.value, KeyProviderError), (
            "get_vault_repository não deve levantar KeyProviderError"
        )
    finally:
        get_vault_repository.cache_clear()


# ---------------------------------------------------------------------------
# 13. get_identity_vault() — integração com get_audit_logger (monkeypatched)
# ---------------------------------------------------------------------------

class _FakeAuditLogger:
    """Audit logger fake para testar get_identity_vault() sem rede."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def record_event(self, event_type: str, *, pseudonym_id, actor, **fields) -> str:
        import uuid as _uuid
        self.events.append({
            "event_type": event_type,
            "pseudonym_id": pseudonym_id,
            "actor": actor,
            **fields,
        })
        return str(_uuid.uuid4())


def _make_vault_via_factory(monkeypatch, fake_logger, cpf_to_store=None):
    """
    Cria um IdentityVault via get_identity_vault() com:
      - get_vault_repository monkeypatchado para InMemoryVaultRepository
      - get_audit_logger monkeypatchado para retornar fake_logger
    Retorna (vault, pseudonym_id_or_None).
    """
    from src.privacy.identity_vault import (
        InMemoryVaultRepository,
        get_identity_vault,
        get_vault_repository,
    )
    from src.privacy import audit as audit_mod

    mem_repo = InMemoryVaultRepository()
    monkeypatch.setattr(
        "src.privacy.identity_vault.get_vault_repository",
        lambda: mem_repo,
    )
    monkeypatch.setattr(audit_mod, "get_audit_logger", lambda: fake_logger)
    # Also clear lru_cache on get_audit_logger if it was cached
    try:
        audit_mod.get_audit_logger.cache_clear()
    except AttributeError:
        pass

    vault = get_identity_vault()
    pid = vault.store(cpf_to_store) if cpf_to_store else None
    return vault, pid


def test_get_identity_vault_reveal_serasa_query_generates_audit_event(monkeypatch):
    """
    get_identity_vault() com get_audit_logger monkeypatchado:
    reveal(SERASA_QUERY) gera evento vault.reveal com purpose e ok=True.
    """
    fake = _FakeAuditLogger()
    vault, pid = _make_vault_via_factory(monkeypatch, fake, cpf_to_store=_CPF)

    vault.reveal(pid, purpose="SERASA_QUERY", actor="agent1")

    assert len(fake.events) == 1
    ev = fake.events[0]
    assert ev["event_type"] == "vault.reveal"
    assert ev["pseudonym_id"] == pid
    assert ev["actor"] == "agent1"
    assert ev["purpose"] == "SERASA_QUERY"
    assert ev["ok"] is True


def test_get_identity_vault_reveal_nonexistent_generates_audit_event_ok_false(monkeypatch):
    """
    get_identity_vault() com get_audit_logger monkeypatchado:
    reveal de titular inexistente gera vault.reveal ok=False.
    """
    from src.privacy.identity_vault import VaultError

    fake = _FakeAuditLogger()
    vault, _ = _make_vault_via_factory(monkeypatch, fake)

    with pytest.raises(VaultError):
        vault.reveal("nonexistent_id", purpose="SERASA_QUERY", actor="agent1")

    assert len(fake.events) == 1
    ev = fake.events[0]
    assert ev["event_type"] == "vault.reveal"
    assert ev["ok"] is False


def test_get_identity_vault_configuration_error_uses_noop(monkeypatch):
    """
    Quando get_audit_logger levanta ConfigurationError,
    get_identity_vault() ainda funciona (usa no-op silencioso).
    """
    from src.agents.agent1.config import ConfigurationError
    from src.privacy.identity_vault import (
        InMemoryVaultRepository,
        get_identity_vault,
    )
    from src.privacy import audit as audit_mod

    mem_repo = InMemoryVaultRepository()
    monkeypatch.setattr(
        "src.privacy.identity_vault.get_vault_repository",
        lambda: mem_repo,
    )

    def _raise():
        raise ConfigurationError("sem credenciais")

    monkeypatch.setattr(audit_mod, "get_audit_logger", _raise)
    try:
        audit_mod.get_audit_logger.cache_clear()
    except AttributeError:
        pass

    # Deve funcionar sem levantar
    vault = get_identity_vault()
    pid = vault.store(_CPF)
    recovered = vault.reveal(pid, purpose="SERASA_QUERY", actor="agent1")
    assert recovered == _CPF_DIGITS


def test_get_identity_vault_audit_callback_raises_reveal_still_returns(monkeypatch):
    """
    Se o callback de auditoria levantar, reveal ainda retorna o CPF corretamente.
    """
    from src.privacy.identity_vault import (
        InMemoryVaultRepository,
        get_identity_vault,
    )
    from src.privacy import audit as audit_mod

    class _BrokenLogger:
        def record_event(self, event_type, *, pseudonym_id, actor, **fields):
            raise RuntimeError("audit store indisponível")

    mem_repo = InMemoryVaultRepository()
    monkeypatch.setattr(
        "src.privacy.identity_vault.get_vault_repository",
        lambda: mem_repo,
    )
    monkeypatch.setattr(audit_mod, "get_audit_logger", lambda: _BrokenLogger())
    try:
        audit_mod.get_audit_logger.cache_clear()
    except AttributeError:
        pass

    vault = get_identity_vault()
    pid = vault.store(_CPF)
    # Audit vai levantar, mas reveal deve retornar normalmente
    recovered = vault.reveal(pid, purpose="SERASA_QUERY", actor="agent1")
    assert recovered == _CPF_DIGITS
