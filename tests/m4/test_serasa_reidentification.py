"""
Fase B3 — Reidentificação para o Serasa real.

Testes sem rede (monkeypatch de httpx.post). Verificam:
  1. CPF vai no corpo JSON, NÃO na URL nem nos headers.
  2. Evento vault.reveal emitido com purpose=SERASA_QUERY e ok=True.
  3. Titular shredded → SourceUnavailableError; mensagem sem CPF/pseudonym.
  4. HTTP 500 → SourceUnavailableError; mensagem sem CPF.
  5. build_serasa_client() com SERASA_SANDBOX=true → SandboxSerasaClient.
"""

from __future__ import annotations

import os

import httpx
import pytest

from src.agents.agent1.sources import (
    HttpSerasaClient,
    SandboxSerasaClient,
    SourceUnavailableError,
    build_serasa_client,
)
from src.privacy.identity_vault import (
    IdentityVault,
    InMemoryVaultRepository,
    VaultError,
)
from src.privacy.key_provider import EnvKeyProvider

# CPF de teste (válido para Luhn)
_CPF = "529.982.247-25"
_CPF_DIGITS = "52998224725"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(audit=None):
    """IdentityVault em memória com EnvKeyProvider — sem rede."""
    repo = InMemoryVaultRepository()
    kp = EnvKeyProvider()
    return IdentityVault(repository=repo, key_provider=kp, audit=audit), repo


def _ok_post_response(url, **kwargs):
    """Fake POST que devolve um relatório Serasa mínimo."""
    return httpx.Response(
        200,
        json={"score_band": "A", "negative_records": []},
        request=httpx.Request("POST", url),
    )


# ---------------------------------------------------------------------------
# 1. CPF vai no corpo JSON, NÃO na URL nem nos headers
# ---------------------------------------------------------------------------

def test_cpf_in_json_body_not_in_url(monkeypatch):
    """O CPF deve aparecer no corpo JSON e nunca na URL da requisição."""
    captured: dict = {}

    def fake_post(url, *, headers=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["json"] = json or {}
        return httpx.Response(
            200,
            json={"score_band": "A", "negative_records": []},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = HttpSerasaClient(
        "https://serasa.example",
        "api-key",
        resolver=lambda _: _CPF_DIGITS,
    )
    client.fetch_report("pseudo-abc")

    # CPF deve estar no corpo
    assert captured["json"].get("cpf") == _CPF_DIGITS

    # CPF NÃO deve estar na URL
    assert _CPF_DIGITS not in captured["url"]
    assert _CPF not in captured["url"]

    # CPF NÃO deve estar nos headers
    headers_str = str(captured["headers"])
    assert _CPF_DIGITS not in headers_str
    assert _CPF not in headers_str


# ---------------------------------------------------------------------------
# 2. Evento vault.reveal com purpose=SERASA_QUERY e ok=True
# ---------------------------------------------------------------------------

def test_vault_reveal_event_emitted(monkeypatch):
    """Após fetch_report bem-sucedido, vault.reveal é auditado com ok=True."""
    monkeypatch.setattr(httpx, "post", _ok_post_response)

    events: list[dict] = []

    def audit(event_type, **fields):
        events.append({"event_type": event_type, **fields})

    vault, _ = _make_vault(audit=audit)
    pseudonym_id = vault.store(_CPF)

    client = HttpSerasaClient(
        "https://serasa.example",
        "api-key",
        resolver=vault.resolve_for_serasa,
    )
    client.fetch_report(pseudonym_id)

    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "vault.reveal"
    assert ev["purpose"] == "SERASA_QUERY"
    assert ev["ok"] is True
    # CPF não deve aparecer no evento
    for v in ev.values():
        assert _CPF_DIGITS not in str(v)


# ---------------------------------------------------------------------------
# 3. Titular shredded → SourceUnavailableError sem CPF/pseudonym na mensagem
# ---------------------------------------------------------------------------

def test_shredded_holder_raises_source_unavailable_without_leaking(monkeypatch):
    """Cofre destruído → SourceUnavailableError; mensagem sem CPF nem pseudonym_id."""
    vault, _ = _make_vault()
    pseudonym_id = vault.store(_CPF)
    vault.shred(pseudonym_id)

    client = HttpSerasaClient(
        "https://serasa.example",
        "api-key",
        resolver=vault.resolve_for_serasa,
    )

    with pytest.raises(SourceUnavailableError) as exc_info:
        client.fetch_report(pseudonym_id)

    msg = str(exc_info.value)
    assert _CPF_DIGITS not in msg
    assert _CPF not in msg
    assert pseudonym_id not in msg


# ---------------------------------------------------------------------------
# 4. HTTP 500 → SourceUnavailableError sem CPF na mensagem
# ---------------------------------------------------------------------------

def test_http_500_message_does_not_contain_cpf(monkeypatch):
    """Falha HTTP 500 → SourceUnavailableError; mensagem não contém o CPF."""

    def fake_post(url, **kwargs):
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    client = HttpSerasaClient(
        "https://serasa.example",
        "api-key",
        resolver=lambda _: _CPF_DIGITS,
    )

    with pytest.raises(SourceUnavailableError) as exc_info:
        client.fetch_report("pseudo-xyz")

    msg = str(exc_info.value)
    assert _CPF_DIGITS not in msg
    assert _CPF not in msg


# ---------------------------------------------------------------------------
# 5. build_serasa_client() com SERASA_SANDBOX=true → SandboxSerasaClient
# ---------------------------------------------------------------------------

def test_build_serasa_client_sandbox(monkeypatch):
    """Com SERASA_SANDBOX=true, build_serasa_client retorna SandboxSerasaClient."""
    monkeypatch.setenv("SERASA_SANDBOX", "true")
    client = build_serasa_client()
    assert isinstance(client, SandboxSerasaClient)
