"""
Testes dos achados #1, #5, #6, #7 de docs/revisao-bob-privacy.md.

Todos sem rede.
"""

from __future__ import annotations

import base64

import pytest

from src.privacy.audit import AuditLogger, InMemoryAuditRepository, NoopGovernanceSink
from src.privacy.identity_vault import (
    IdentityVault,
    InMemoryVaultRepository,
    VaultError,
)
from src.privacy.key_provider import EnvKeyProvider
from src.privacy.pii_guard import PIIViolation, assert_no_pii


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault():
    repo = InMemoryVaultRepository()
    kp = EnvKeyProvider()
    return IdentityVault(repository=repo, key_provider=kp), repo


def _make_logger():
    repo = InMemoryAuditRepository()
    return AuditLogger(
        repository=repo,
        cos_sink=None,
        governance=NoopGovernanceSink(),
    ), repo


# ---------------------------------------------------------------------------
# #1 — InMemoryAuditRepository.__init__ não usa dict vazio
# ---------------------------------------------------------------------------

class TestAchado1InMemoryAuditRepository:
    def test_store_is_list_after_init(self):
        """`_store` deve ser uma lista, não um dict."""
        repo = InMemoryAuditRepository()
        assert isinstance(repo._store, list)

    def test_store_is_empty_after_init(self):
        repo = InMemoryAuditRepository()
        assert repo._store == []

    def test_store_accepts_put(self):
        """Garantia de regressão: put/find continuam funcionando."""
        repo = InMemoryAuditRepository()
        repo.put({"_id": "x", "event_type": "vault.store"})
        results = repo.find({"_id": "x"})
        assert len(results) == 1


# ---------------------------------------------------------------------------
# #5 — VaultError não deve conter o pseudonym_id
# ---------------------------------------------------------------------------

class TestAchado5VaultErrorSemPseudonymId:
    _CPF = "123.456.789-09"

    def test_not_found_message_does_not_contain_pseudonym_id(self):
        """reveal de pseudonym_id inexistente não deve expor o id na exceção."""
        vault, _ = _make_vault()
        fake_pid = "a" * 64
        with pytest.raises(VaultError) as exc_info:
            vault.reveal(fake_pid, purpose="SERASA_QUERY", actor="test")
        assert fake_pid not in str(exc_info.value)

    def test_decrypt_failure_message_does_not_contain_pseudonym_id(self):
        """Falha de decifração não deve expor o pseudonym_id na exceção."""
        vault, repo = _make_vault()
        pid = vault.store(self._CPF)
        doc = repo.get(pid)

        # Corrompe ciphertext para forçar InvalidTag durante decrypt
        ct_bytes = base64.b64decode(doc["cpf_ciphertext"])
        bad_ct = ct_bytes[:-1] + bytes([ct_bytes[-1] ^ 0xFF])
        doc["cpf_ciphertext"] = base64.b64encode(bad_ct).decode()
        repo.put(doc)

        with pytest.raises(VaultError) as exc_info:
            vault.reveal(pid, purpose="SERASA_QUERY", actor="test")
        assert pid not in str(exc_info.value)


# ---------------------------------------------------------------------------
# #6 — _FORBIDDEN_KEYS inclui chaves de data de nascimento
# ---------------------------------------------------------------------------

class TestAchado6ForbiddenKeys:
    @pytest.mark.parametrize("key", ["data_nascimento", "birth_date", "date_of_birth", "dob"])
    def test_flat_date_key_raises(self, key):
        """Campos de data de nascimento no nível raiz devem ser bloqueados."""
        with pytest.raises(PIIViolation):
            assert_no_pii({key: "1990-01-01"})

    @pytest.mark.parametrize("key", ["data_nascimento", "birth_date", "date_of_birth", "dob"])
    def test_nested_date_key_raises(self, key):
        """Campos de data de nascimento aninhados devem ser bloqueados."""
        with pytest.raises(PIIViolation):
            assert_no_pii({"holder": {key: "1990-01-01"}})


# ---------------------------------------------------------------------------
# #7 — Loop do sanitizador tem limite de iterações (fail-closed)
# ---------------------------------------------------------------------------

class TestAchado7SanitizadorLimiteIteracoes:
    def test_non_converging_sanitizer_does_not_loop_forever(self, monkeypatch):
        """
        Se assert_no_pii nunca passa (monkeypatched para sempre lançar em
        um caminho que o sanitizador não sabe remover), o loop deve terminar
        e gravar só o registro mínimo de audit.pii_blocked: o bloqueio fica na
        trilha, sem nenhum valor vindo do chamador.
        """
        logger_inst, repo = _make_logger()

        dispatch_calls: list[dict] = []
        original_dispatch = logger_inst._dispatch

        def _spy_dispatch(record):
            dispatch_calls.append(record)
            original_dispatch(record)

        monkeypatch.setattr(logger_inst, "_dispatch", _spy_dispatch)

        # Força assert_no_pii a sempre lançar no caminho "some_unknown_field"
        # (o sanitizador remove o campo, mas o mock sempre relança com o mesmo path,
        # simulando um caminho que não converge).
        call_count = 0

        def _always_raises(doc):
            nonlocal call_count
            call_count += 1
            raise PIIViolation("some_unknown_field", "pii_value")

        monkeypatch.setattr("src.privacy.audit.assert_no_pii", _always_raises)

        # record_event com um campo qualquer; a primeira chamada a assert_no_pii
        # (dentro de record_event) vai lançar, acionando o bloco de sanitização.
        trace_id = logger_inst.record_event(
            "vault.reveal",
            pseudonym_id="a" * 64,
            actor="test",
        )

        assert call_count == 21  # 1 verificação do evento + 20 iterações do sanitizador
        assert len(dispatch_calls) == 1
        assert dispatch_calls[0] == {
            "_id": trace_id,
            "type": "audit_event",
            "trace_id": trace_id,
            "recorded_at": dispatch_calls[0]["recorded_at"],
            "event_type": "audit.pii_blocked",
            "pseudonym_id": None,
            "actor": "redacted",
            "blocked_event_type": "vault.reveal",
        }
