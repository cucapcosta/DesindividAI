import hashlib

import pytest

from src.privacy import pseudonymization as p


def test_cpf_pseudonym_is_deterministic_and_format_independent():
    assert p.pseudonymize_cpf("123.456.789-09") == p.pseudonymize_cpf("12345678909")


def test_cpf_pseudonym_is_not_a_plain_hash():
    assert p.pseudonymize_cpf("12345678909") != hashlib.sha256(b"12345678909").hexdigest()


def test_contract_ref_is_scoped_by_institution():
    assert p.pseudonymize_contract("bb", "123") != p.pseudonymize_contract("itau", "123")
    assert len(p.pseudonymize_contract("bb", "123")) == 32


def test_invalid_cpf_rejected():
    with pytest.raises(ValueError):
        p.pseudonymize_cpf("123")


def test_missing_key_in_production_is_fatal(monkeypatch):
    monkeypatch.delenv("PSEUDONYM_HMAC_KEY")
    monkeypatch.setenv("APP_ENV", "production")
    p.reset_key_cache()
    try:
        with pytest.raises(p.PseudonymizationKeyError):
            p.pseudonymize_cpf("12345678909")
    finally:
        monkeypatch.undo()
        p.reset_key_cache()
