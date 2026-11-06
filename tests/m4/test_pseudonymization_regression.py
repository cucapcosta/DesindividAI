"""
Regression tests — verifica que a saída de pseudonymization.py não mudou.

Os valores esperados são calculados no próprio teste com hmac/hashlib usando a
chave fixa definida em conftest.py ("ab" * 32), de modo que qualquer mudança na
lógica de construção da mensagem ou no algoritmo quebrará imediatamente estes
testes.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from src.privacy import pseudonymization as p


# ---------------------------------------------------------------------------
# Chave de referência (espelho do conftest.py — não lê .env)
# ---------------------------------------------------------------------------
_KEY = bytes.fromhex("ab" * 32)


# ---------------------------------------------------------------------------
# Fixture: isola o cache de chave antes e depois de cada teste
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_key_cache():
    p.reset_key_cache()
    yield
    p.reset_key_cache()


# ---------------------------------------------------------------------------
# Testes de regressão
# ---------------------------------------------------------------------------

def test_pseudonymize_cpf_regression():
    expected = hmac.new(_KEY, b"cpf:12345678909", hashlib.sha256).hexdigest()
    assert p.pseudonymize_cpf("123.456.789-09") == expected


def test_pseudonymize_contract_regression():
    expected = hmac.new(_KEY, b"contract:itau:CTR-001", hashlib.sha256).hexdigest()[:32]
    assert p.pseudonymize_contract("Itau", "CTR-001") == expected


def test_pseudonymize_cpf_with_version_regression():
    expected_hex = hmac.new(_KEY, b"cpf:12345678909", hashlib.sha256).hexdigest()
    pseudo_id, version = p.pseudonymize_cpf_with_version("12345678909")
    assert pseudo_id == expected_hex
    assert version == "v1"


def test_current_key_version_regression():
    assert p.current_key_version() == "v1"
