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
