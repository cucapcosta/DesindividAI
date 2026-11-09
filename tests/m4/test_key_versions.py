"""
Versões da chave de pseudonimização — base do apagamento/exportação por CPF.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from src.privacy.key_provider import EnvKeyProvider

_CPF = "529.982.247-25"
_CPF_DIGITS = "52998224725"


def _expected(key_hex: str) -> str:
    return hmac.new(bytes.fromhex(key_hex), f"cpf:{_CPF_DIGITS}".encode(), hashlib.sha256).hexdigest()


def test_list_versions_orders_numerically_and_includes_active(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY", "ab" * 32)
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY_V2", "bb" * 32)
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY_V10", "cc" * 32)
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v3")  # ativa sem chave
    assert EnvKeyProvider().list_versions("pseudonym") == ["v1", "v2", "v3", "v10"]


def test_list_versions_ignores_empty_vars_and_other_purposes(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY", "ab" * 32)
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY_V2", "")
    monkeypatch.setenv("VAULT_KEK_V5", "cd" * 32)
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    assert EnvKeyProvider().list_versions("pseudonym") == ["v1"]


def test_list_versions_vault_kek(monkeypatch):
    monkeypatch.setenv("VAULT_KEK_V1", "cd" * 32)
    monkeypatch.setenv("VAULT_KEK_V2", "ce" * 32)
    monkeypatch.setenv("VAULT_KEK_ACTIVE_VERSION", "v2")
    assert EnvKeyProvider().list_versions("vault_kek") == ["v1", "v2"]


def test_list_versions_without_v1_key_in_dev_still_lists_active(monkeypatch):
    """Sem PSEUDONYM_HMAC_KEY (dev, chave efêmera), a ativa v1 aparece."""
    monkeypatch.delenv("PSEUDONYM_HMAC_KEY", raising=False)
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    assert EnvKeyProvider().list_versions("pseudonym") == ["v1"]


def test_all_versions_returns_one_pseudonym_per_key(pseudonym_v2):
    pm = pseudonym_v2
    result = pm.pseudonymize_cpf_all_versions(_CPF)
    assert result == [(_expected("ab" * 32), "v1"), (_expected("ef" * 32), "v2")]
    # o da versão ativa bate com pseudonymize_cpf
    assert result[0][0] == pm.pseudonymize_cpf(_CPF)


def test_all_versions_after_rotation_still_covers_old_version(pseudonym_v2, monkeypatch):
    pm = pseudonym_v2
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v2")
    pm.reset_key_cache()
    pids = dict((v, p) for p, v in pm.pseudonymize_cpf_all_versions(_CPF))
    assert pids["v1"] == _expected("ab" * 32)
    assert pids["v2"] == _expected("ef" * 32) == pm.pseudonymize_cpf(_CPF)


def test_all_versions_single_key_returns_only_active():
    from src.privacy import pseudonymization as pm

    pm.reset_key_cache()
    assert pm.pseudonymize_cpf_all_versions(_CPF) == [(pm.pseudonymize_cpf(_CPF), "v1")]


def test_all_versions_missing_key_fails_closed(monkeypatch):
    from src.privacy import pseudonymization as pm

    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v3")  # sem PSEUDONYM_HMAC_KEY_V3
    pm.reset_key_cache()
    try:
        with pytest.raises(pm.PseudonymizationKeyError):
            pm.pseudonymize_cpf_all_versions(_CPF)
    finally:
        monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
        pm.reset_key_cache()


def test_provider_without_list_versions_uses_active_only(monkeypatch):
    from src.privacy import key_provider as kp_mod
    from src.privacy import pseudonymization as pm

    class _OnlyGet:
        def get(self, purpose, version=None):
            return "v7", bytes.fromhex("aa" * 32)

    monkeypatch.setattr(kp_mod, "default_provider", _OnlyGet())
    pm.reset_key_cache()
    try:
        assert pm.pseudonym_key_versions() == ["v7"]
        assert pm.pseudonymize_cpf_all_versions(_CPF) == [(_expected("aa" * 32), "v7")]
    finally:
        monkeypatch.undo()
        pm.reset_key_cache()


def test_invalid_cpf_raises_value_error():
    from src.privacy import pseudonymization as pm

    with pytest.raises(ValueError):
        pm.pseudonymize_cpf_all_versions("123")
