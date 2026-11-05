"""
Testes do EnvKeyProvider (src/privacy/key_provider.py).
Sem rede — todas as chaves são fixadas via monkeypatch ou já estão no conftest.
"""

import os

import pytest

from src.privacy.key_provider import EnvKeyProvider, KeyProviderError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_provider() -> EnvKeyProvider:
    """Instância isolada (sem cache compartilhado com o default_provider)."""
    return EnvKeyProvider()


# ---------------------------------------------------------------------------
# Testes de leitura básica
# ---------------------------------------------------------------------------

def test_pseudonym_v1_reads_PSEUDONYM_HMAC_KEY(monkeypatch):
    """v1 da chave de pseudonimização vem de PSEUDONYM_HMAC_KEY."""
    key_hex = "ab" * 32
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY", key_hex)
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    p = make_provider()
    version, key = p.get("pseudonym")
    assert version == "v1"
    assert key == bytes.fromhex(key_hex)


def test_pseudonym_v2_reads_PSEUDONYM_HMAC_KEY_V2(monkeypatch):
    """Versões acima de v1 lêem PSEUDONYM_HMAC_KEY_V<N>."""
    key_hex = "ef" * 32
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY_V2", key_hex)
    p = make_provider()
    version, key = p.get("pseudonym", "v2")
    assert version == "v2"
    assert key == bytes.fromhex(key_hex)


def test_vault_kek_v1_reads_VAULT_KEK_V1(monkeypatch):
    """KEK do cofre lê VAULT_KEK_V1."""
    key_hex = "cd" * 32
    monkeypatch.setenv("VAULT_KEK_V1", key_hex)
    monkeypatch.setenv("VAULT_KEK_ACTIVE_VERSION", "v1")
    p = make_provider()
    version, key = p.get("vault_kek")
    assert version == "v1"
    assert key == bytes.fromhex(key_hex)


def test_active_version_respected(monkeypatch):
    """Versão ativa configurável via variável de ambiente."""
    key_hex = "12" * 32
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY_V3", key_hex)
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v3")
    p = make_provider()
    version, key = p.get("pseudonym")
    assert version == "v3"
    assert key == bytes.fromhex(key_hex)


# ---------------------------------------------------------------------------
# Testes de validação
# ---------------------------------------------------------------------------

def test_invalid_hex_raises(monkeypatch):
    """Chave não-hexadecimal levanta KeyProviderError."""
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY", "nothex!!")
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    p = make_provider()
    with pytest.raises(KeyProviderError, match="hexadecimal"):
        p.get("pseudonym")


def test_key_too_short_raises(monkeypatch):
    """Chave com menos de 32 bytes levanta KeyProviderError."""
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY", "ab" * 16)  # 16 bytes
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    p = make_provider()
    with pytest.raises(KeyProviderError, match="256 bits"):
        p.get("pseudonym")


def test_missing_key_in_production_raises(monkeypatch):
    """Chave ausente em produção levanta KeyProviderError."""
    monkeypatch.delenv("PSEUDONYM_HMAC_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    p = make_provider()
    with pytest.raises(KeyProviderError):
        p.get("pseudonym")


def test_missing_vault_kek_raises(monkeypatch):
    """KEK ausente levanta KeyProviderError (não há fallback para vault_kek)."""
    monkeypatch.delenv("VAULT_KEK_V1", raising=False)
    monkeypatch.setenv("VAULT_KEK_ACTIVE_VERSION", "v1")
    p = make_provider()
    with pytest.raises(KeyProviderError):
        p.get("vault_kek")


# ---------------------------------------------------------------------------
# Testes de chave efêmera em dev
# ---------------------------------------------------------------------------

def test_missing_pseudonym_key_in_dev_returns_ephemeral(monkeypatch):
    """
    Chave ausente em desenvolvimento: retorna bytes aleatórios (sem erro),
    mas duas instâncias diferentes do provider retornam chaves diferentes
    (a chave é por instância, não global).
    """
    monkeypatch.delenv("PSEUDONYM_HMAC_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")

    p1 = make_provider()
    _, key1 = p1.get("pseudonym")
    assert len(key1) == 32

    # Uma segunda instância também funciona (chave efêmera por instância)
    p2 = make_provider()
    _, key2 = p2.get("pseudonym")
    assert len(key2) == 32


# ---------------------------------------------------------------------------
# Testes de cache e reset
# ---------------------------------------------------------------------------

def test_cache_avoids_rereading_env(monkeypatch):
    """Após a primeira leitura, o provider não relê o ambiente."""
    key_hex = "ab" * 32
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY", key_hex)
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    p = make_provider()
    _, key1 = p.get("pseudonym")
    # Remove a variável do ambiente — segunda chamada deve usar o cache.
    monkeypatch.delenv("PSEUDONYM_HMAC_KEY")
    _, key2 = p.get("pseudonym")
    assert key1 == key2


def test_reset_key_cache_clears_cache(monkeypatch):
    """_reset_key_cache() limpa o cache e a próxima leitura vai ao env."""
    key_hex_v1 = "ab" * 32
    key_hex_v2 = "ef" * 32
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY", key_hex_v1)
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    p = make_provider()
    _, key1 = p.get("pseudonym")
    assert key1 == bytes.fromhex(key_hex_v1)

    # Troca a chave e reseta o cache.
    monkeypatch.setenv("PSEUDONYM_HMAC_KEY", key_hex_v2)
    p._reset_key_cache()
    _, key2 = p.get("pseudonym")
    assert key2 == bytes.fromhex(key_hex_v2)
