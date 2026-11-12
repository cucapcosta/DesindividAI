"""
Testes dos achados #1, #5, #6, #7 de docs/revisao-bob-privacy.md.

Todos sem rede.
"""

from __future__ import annotations

import base64
import json

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
# #6 — _FORBIDDEN_KEYS inclui chaves de data de nascimento e outras
# ---------------------------------------------------------------------------

class TestAchado6ForbiddenKeys:
    @pytest.mark.parametrize("key", [
        "data_nascimento", "birth_date", "date_of_birth", "dob",
        "birthdate", "dateofbirth", "cns", "passport", "passaporte",
        "birthDate", "dateOfBirth",  # camelCase do Open Finance (lower() normaliza)
    ])
    def test_flat_date_key_raises(self, key):
        """Campos de data de nascimento / docs no nível raiz devem ser bloqueados."""
        with pytest.raises(PIIViolation):
            assert_no_pii({key: "1990-01-01"})

    @pytest.mark.parametrize("key", [
        "data_nascimento", "birth_date", "date_of_birth", "dob",
        "birthdate", "dateofbirth", "cns", "passport", "passaporte",
        "birthDate", "dateOfBirth",
    ])
    def test_nested_date_key_raises(self, key):
        """Campos de data de nascimento aninhados devem ser bloqueados."""
        with pytest.raises(PIIViolation):
            assert_no_pii({"holder": {key: "1990-01-01"}})


# ---------------------------------------------------------------------------
# #7 — Sanitizador: prefixo + limite de iterações
# ---------------------------------------------------------------------------

_CPF_VALID = "529.982.247-25"  # CPF válido para testes de PII real


class TestAchado7SanitizadorPrefixo:
    """Sanitizador trata sub-paths como pseudonym_id[0] e actor.cpf."""

    def test_pseudonym_id_list_is_sanitized(self):
        """pseudonym_id como lista com CPF válido → audit.pii_blocked sem PII no registro."""
        logger_inst, repo = _make_logger()
        logger_inst.record_event(
            "vault.reveal",
            pseudonym_id=[_CPF_VALID],
            actor="safe_actor",
        )
        records = repo.find({"event_type": "audit.pii_blocked"})
        assert len(records) == 1, "deve gravar exatamente 1 registro audit.pii_blocked"
        blob = json.dumps(records[0])
        assert "529" not in blob, f"CPF parcial '529' vazou no registro: {blob}"
        assert "52998224725" not in blob
        assert records[0]["violation_path"] == "pseudonym_id[0]"
        assert records[0]["pseudonym_id"] is None
        assert records[0]["actor"] == "safe_actor"  # só o campo violado é saneado

    def test_actor_dict_with_cpf_is_sanitized(self):
        """actor como dict com CPF válido em chave proibida → audit.pii_blocked sem PII."""
        logger_inst, repo = _make_logger()
        logger_inst.record_event(
            "vault.reveal",
            pseudonym_id="a" * 64,
            actor={"cpf": _CPF_VALID},
        )
        records = repo.find({"event_type": "audit.pii_blocked"})
        assert len(records) == 1
        blob = json.dumps(records[0])
        assert "529" not in blob, f"CPF parcial '529' vazou no registro: {blob}"
        assert "52998224725" not in blob
        assert records[0]["violation_path"] == "actor.cpf"
        assert records[0]["actor"] == "redacted"
        assert records[0]["pseudonym_id"] == "a" * 64  # só o campo violado é saneado


class TestAchado7SanitizadorLimiteIteracoes:
    def test_non_converging_sanitizer_does_not_loop_forever(self, monkeypatch):
        """
        Se assert_no_pii nunca passa (monkeypatched para sempre lançar em
        um caminho que o sanitizador não sabe remover), o loop deve terminar
        e gravar só o registro mínimo de audit.pii_blocked.
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
        def _always_raises(doc):
            raise PIIViolation("some_unknown_field", "pii_value")

        monkeypatch.setattr("src.privacy.audit.assert_no_pii", _always_raises)

        # record_event com um campo qualquer; a primeira chamada a assert_no_pii
        # (dentro de record_event) vai lançar, acionando o bloco de sanitização.
        trace_id = logger_inst.record_event(
            "vault.reveal",
            pseudonym_id="a" * 64,
            actor="test",
        )

        # Deve terminar e gravar exatamente 1 blocked_record.
        assert len(dispatch_calls) == 1
        rec = dispatch_calls[0]
        assert rec["event_type"] == "audit.pii_blocked"
        assert rec["pseudonym_id"] is None
        assert rec["actor"] == "redacted"
        assert rec["blocked_event_type"] == "vault.reveal"
        assert rec["_id"] == trace_id
