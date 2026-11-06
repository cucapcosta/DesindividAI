"""
Testes de integração para os endpoints de consentimento (M1).

Usa as fixtures `client` e `store` de tests/conftest.py:
  - store: MemoryStore limpo
  - client: TestClient com app.dependency_overrides[get_store] = lambda: store
CPF de teste válido: 52998224725
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import yaml

import src.privacy.pseudonymization as ps
import src.services.consent_service as svc
from src.api.models import ConsentStatus
from src.api.routers.debts import _consent_error
from src.core.config import consents_db, snapshots_db
from src.core.dates import parse_utc
from src.services.consent_service import _is_valid_cpf

OPENAPI_YAML = Path(__file__).resolve().parent.parent / "openapi" / "openapi.yaml"

CPF_VALID = "52998224725"
CPF_BAD_DV = "52998224726"      # dígito verificador errado
CPF_REPEATED = "11111111111"    # sequência repetida


# ===========================================================================
# Helpers
# ===========================================================================

def _assert_tz(value: str, label: str = "") -> None:
    """Data ISO-8601 com fuso explícito (sem parse_utc, que completaria o fuso)."""
    assert datetime.fromisoformat(value).tzinfo is not None, f"{label} sem fuso: {value!r}"


def _post_consent(client, **overrides):
    body = {
        "cpf": CPF_VALID,
        "institutions": ["bb", "itau"],
        "scopes": ["debts:read"],
        **overrides,
    }
    return client.post("/consent", json=body)


def _created(client, **overrides) -> dict:
    resp = _post_consent(client, **overrides)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _get_state(client, monkeypatch, scopes=None) -> tuple[str, str]:
    """Cria consentimento em sandbox e retorna (consent_id, state)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    kw = {}
    if scopes:
        kw["scopes"] = scopes
    body = _created(client, **kw)
    consent_id = body["consent_id"]
    state = parse_qs(urlparse(body["redirect_url"]).query)["state"][0]
    return consent_id, state


def _authorised_consent(client, monkeypatch, scopes=None) -> str:
    """Cria e autoriza um consentimento. Retorna consent_id."""
    consent_id, state = _get_state(client, monkeypatch, scopes=scopes)
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 200, f"callback falhou: {resp.text}"
    return consent_id


# ===========================================================================
# Grupo: create
# ===========================================================================

def test_create_returns_201_awaiting(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == ConsentStatus.AWAITING_AUTHORISATION.value
    assert "/consent/callback?state=" in body["redirect_url"]


def test_create_doc_stored_with_id_as_key(client, monkeypatch, store):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    body = _created(client)
    consent_id = body["consent_id"]
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc is not None
    assert doc["_id"] == consent_id
    assert doc["consent_id"] == consent_id


def test_create_dates_have_timezone(client, monkeypatch, store):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    body = _created(client)
    _assert_tz(body["created_at"], "created_at")
    _assert_tz(body["expires_at"], "expires_at")
    # also check stored doc dates
    doc = asyncio.run(store.get(consents_db(), body["consent_id"]))
    for field in ("created_at", "expires_at", "awaiting_expires_at"):
        _assert_tz(doc[field], field)
    delta = parse_utc(body["expires_at"]) - parse_utc(body["created_at"])
    assert delta.days == 90  # default expiration_days


def test_create_expires_at_respects_expiration_days(client, monkeypatch, store):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    body = _created(client, expiration_days=30)
    delta = parse_utc(body["expires_at"]) - parse_utc(body["created_at"])
    assert delta.days == 30


def test_create_normalizes_institutions(client, monkeypatch, store):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    _post_consent(client, institutions=[" BB", "bb", "Itau"])
    docs = asyncio.run(store.find(consents_db(), {"status": "AWAITING_AUTHORISATION"}, limit=10))
    assert len(docs) == 1
    assert docs[0]["institutions"] == ["bb", "itau"]


def test_create_unknown_scope_returns_400_invalid_scope(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, scopes=["unknown:read"])
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_SCOPE"


def test_create_bad_cpf_dv_returns_400_invalid_cpf(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, cpf=CPF_BAD_DV)
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_CPF"


def test_create_repeated_cpf_returns_400_invalid_cpf(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, cpf=CPF_REPEATED)
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_CPF"


def test_create_formatted_cpf_returns_400_invalid_request(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    # "529.982.247-25" não passa no pattern → RequestValidationError → 400 INVALID_REQUEST
    resp = _post_consent(client, cpf="529.982.247-25")
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_create_12digit_cpf_returns_400_invalid_request(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, cpf="529982247256")  # 12 digits
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_create_no_sandbox_no_url_returns_503(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "")
    monkeypatch.setenv("OPEN_FINANCE_AUTHORIZE_URL", "")
    resp = _post_consent(client)
    assert resp.status_code == 503
    assert resp.json()["code"] == "NOT_CONFIGURED"


def test_create_real_mode_redirect_url(client, monkeypatch, store):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "")
    monkeypatch.setenv("OPEN_FINANCE_AUTHORIZE_URL", "https://auth.example.com/authorize")
    monkeypatch.setenv("OPEN_FINANCE_CLIENT_ID", "my-client")
    monkeypatch.setenv("OPEN_FINANCE_REDIRECT_URI", "https://api.example.com/consent/callback")
    resp = _post_consent(client)
    assert resp.status_code == 201
    url = resp.json()["redirect_url"]
    assert url.startswith("https://auth.example.com/authorize")
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["my-client"]
    assert qs["redirect_uri"] == ["https://api.example.com/consent/callback"]
    state = qs["state"][0]
    assert len(state) > 0
    # state_hash gravado deve ser sha256(state) (F7)
    consent_id = resp.json()["consent_id"]
    doc = asyncio.run(store.get(consents_db(), consent_id))
    expected_hash = hashlib.sha256(state.encode()).hexdigest()
    assert doc["state_hash"] == expected_hash, (
        f"state_hash no doc ({doc['state_hash']!r}) != sha256(state) ({expected_hash!r})"
    )


def test_create_real_mode_missing_client_id_returns_503(client, monkeypatch):
    """Modo real sem CLIENT_ID/REDIRECT_URI → 503 NOT_CONFIGURED (B1)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "")
    monkeypatch.setenv("OPEN_FINANCE_AUTHORIZE_URL", "https://auth.example.com/authorize")
    monkeypatch.setenv("OPEN_FINANCE_CLIENT_ID", "")
    monkeypatch.setenv("OPEN_FINANCE_REDIRECT_URI", "")
    resp = _post_consent(client)
    assert resp.status_code == 503
    assert resp.json()["code"] == "NOT_CONFIGURED"


def test_create_institution_with_newline_returns_400(client, monkeypatch):
    """Instituição com \\n → 400 INVALID_REQUEST (C1)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, institutions=["bb\nINFO inject"])
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_create_institution_that_is_cpf_returns_400(client, monkeypatch):
    """CPF como instituição → 400 INVALID_REQUEST (C1)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, institutions=[CPF_VALID])
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_create_institution_blank_returns_400(client, monkeypatch):
    """Instituição só espaços (normaliza para vazio) → 400 INVALID_REQUEST (C1)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, institutions=["  "])
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_create_institution_compe_code_accepted(client, monkeypatch, store):
    """Código COMPE numérico ("001") é instituição válida (S2)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, institutions=["001"])
    assert resp.status_code == 201
    doc = asyncio.run(store.get(consents_db(), resp.json()["consent_id"]))
    assert doc["institutions"] == ["001"]


def test_create_institution_with_embedded_cpf_returns_400(client, monkeypatch, store):
    """CPF com prefixo de letra ("a52998224725") não pode virar instituição (S2)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, institutions=["a" + CPF_VALID])
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"
    assert CPF_VALID not in resp.text
    assert not store._dbs.get(consents_db())


def test_create_sandbox_base_url_trailing_slash(client, monkeypatch):
    """PUBLIC_BASE_URL com / final não gera //consent/callback (B7)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000/")
    body = _created(client)
    assert "//consent" not in body["redirect_url"]


def test_create_scopes_deduplicated(client, monkeypatch, store):
    """Escopos duplicados são gravados sem repetição (B6)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    body = _created(client, scopes=["debts:read", "debts:read", "simulation:read"])
    consent_id = body["consent_id"]
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["scopes"].count("debts:read") == 1


def test_create_invalid_scope_message_does_not_echo_value(client, monkeypatch):
    """Mensagem de INVALID_SCOPE lista escopos aceitos, não o valor recebido (B6)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    bad_scope = "bad:write"
    resp = _post_consent(client, scopes=[bad_scope])
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_SCOPE"
    assert bad_scope not in resp.json()["message"]


@pytest.mark.parametrize("cpf_input", [
    "５２９９８２２４７２５",   # fullwidth digits (B4)
    "٥٢٩٩٨٢٢٤٧٢٥",         # Arabic-Indic digits (F6)
])
def test_create_cpf_unicode_digits_returns_400(client, monkeypatch, cpf_input):
    """CPF com dígitos não-ASCII é barrado pelo pattern do modelo → 400 INVALID_REQUEST (B4/F6)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, cpf=cpf_input)
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_is_valid_cpf_rejects_non_ascii_digits():
    """_is_valid_cpf deve retornar False para dígitos não-ASCII (F6)."""
    assert not _is_valid_cpf("٥٢٩٩٨٢٢٤٧٢٥")    # Arabic-Indic
    assert not _is_valid_cpf("５２９９８２２４７２５")  # fullwidth
    assert _is_valid_cpf(CPF_VALID)               # controle positivo


# ===========================================================================
# Grupo: callback
# ===========================================================================

def test_callback_code_authorises(client, monkeypatch, store):
    consent_id, state = _get_state(client, monkeypatch)
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == ConsentStatus.AUTHORISED.value
    assert body["authorised_at"] is not None
    # state_hash deve ter sido removido do documento
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert "state_hash" not in doc
    # data com fuso no documento (F1)
    _assert_tz(doc["authorised_at"], "authorised_at")


def test_callback_consumed_state_returns_404(client, monkeypatch):
    """Após autorizar, o state_hash é removido — novo callback retorna 404."""
    _, state = _get_state(client, monkeypatch)
    client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "CONSENT_NOT_FOUND"


def test_callback_error_access_denied(client, monkeypatch, store):
    _, state = _get_state(client, monkeypatch)
    resp = client.get("/consent/callback", params={"state": state, "error": "access_denied"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "CONSENT_REJECTED"
    docs = asyncio.run(store.find(consents_db(), {"rejection_reason": "ACCESS_DENIED"}, limit=1))
    assert docs and docs[0]["status"] == ConsentStatus.REJECTED.value


def test_callback_expired_awaiting(client, monkeypatch, store):
    consent_id, state = _get_state(client, monkeypatch)
    doc = asyncio.run(store.get(consents_db(), consent_id))
    doc["awaiting_expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    asyncio.run(store.put(consents_db(), consent_id, doc))
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "CONSENT_AUTHORISATION_EXPIRED"
    updated = asyncio.run(store.get(consents_db(), consent_id))
    assert updated["status"] == ConsentStatus.REJECTED.value          # T6
    assert updated["rejection_reason"] == "AUTHORISATION_TIMEOUT"
    # data com fuso (T6/F1)
    _assert_tz(updated["rejected_at"], "rejected_at")


def test_callback_missing_awaiting_expires_treated_as_expired(client, monkeypatch, store):
    """awaiting_expires_at ausente → trata como vencido → REJECTED (B5)."""
    consent_id, state = _get_state(client, monkeypatch)
    doc = asyncio.run(store.get(consents_db(), consent_id))
    del doc["awaiting_expires_at"]
    asyncio.run(store.put(consents_db(), consent_id, doc))
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "CONSENT_AUTHORISATION_EXPIRED"


def test_callback_unknown_state_returns_404(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    # code=sandbox é válido em modo sandbox; state desconhecido → 404
    resp = client.get("/consent/callback", params={"state": "totally-unknown", "code": "sandbox"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "CONSENT_NOT_FOUND"


def test_callback_missing_state_returns_400(client):
    resp = client.get("/consent/callback", params={"code": "x"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_callback_missing_code_and_error_returns_400(client, monkeypatch):
    """sem code e sem error → 400 (T5)."""
    _, state = _get_state(client, monkeypatch)
    resp = client.get("/consent/callback", params={"state": state})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_callback_after_rejected_returns_409(client, monkeypatch, store):
    """Callback depois de REJECTED → 409 CONSENT_INVALID_STATE.
    O documento inteiro deve permanecer inalterado (F2).
    """
    consent_id, state = _get_state(client, monkeypatch)
    # forçar REJECTED direto no store
    doc = asyncio.run(store.get(consents_db(), consent_id))
    doc["status"] = ConsentStatus.REJECTED.value
    asyncio.run(store.put(consents_db(), consent_id, doc))
    # snapshot antes do callback
    before = asyncio.run(store.get(consents_db(), consent_id)).copy()
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSENT_INVALID_STATE"
    # documento inteiro não alterado (F2)
    after = asyncio.run(store.get(consents_db(), consent_id))
    assert after == before


def test_callback_after_revoked_returns_409(client, monkeypatch, store):
    """Callback depois de REVOKED → 409 CONSENT_INVALID_STATE.
    Usa fluxo real: POST → DELETE → callback com state original (F3).
    """
    # fluxo real: não forçar status no store
    consent_id, state = _get_state(client, monkeypatch)
    client.delete(f"/consent/{consent_id}")
    # snapshot do doc após revogação
    before = asyncio.run(store.get(consents_db(), consent_id)).copy()
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSENT_INVALID_STATE"
    # documento não alterado pelo callback
    after = asyncio.run(store.get(consents_db(), consent_id))
    assert after == before


def test_callback_sandbox_rejects_non_sandbox_code(client, monkeypatch):
    """Modo sandbox: code != 'sandbox' → recusado (B2)."""
    _, state = _get_state(client, monkeypatch)
    resp = client.get("/consent/callback", params={"state": state, "code": "not-sandbox"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_callback_real_mode_rejects_sandbox_code(client, monkeypatch, store):
    """Modo real: code == 'sandbox' → recusado (B2)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "")
    monkeypatch.setenv("OPEN_FINANCE_AUTHORIZE_URL", "https://auth.example.com/authorize")
    monkeypatch.setenv("OPEN_FINANCE_CLIENT_ID", "c")
    monkeypatch.setenv("OPEN_FINANCE_REDIRECT_URI", "https://r.example.com/cb")
    body = _created(client)
    consent_id = body["consent_id"]
    state = parse_qs(urlparse(body["redirect_url"]).query)["state"][0]
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["status"] == ConsentStatus.AWAITING_AUTHORISATION.value


# ===========================================================================
# Grupo: status
# ===========================================================================

def test_status_returns_fields_without_private(client, monkeypatch, store):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    body = _created(client)
    consent_id = body["consent_id"]
    resp = client.get(f"/consent/{consent_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "pseudonym_id" not in data
    assert "state_hash" not in data
    assert data["consent_id"] == consent_id


def test_status_unknown_id_returns_404(client):
    resp = client.get("/consent/00000000-0000-4000-8000-000000000000")
    assert resp.status_code == 404


def test_callback_route_not_captured_as_consent_id(client, monkeypatch):  # T1 hardened
    """
    /callback deve ser capturado pelo handler de callback (state desconhecido → 404 CONSENT_NOT_FOUND),
    não pelo /{consent_id} (que retornaria 400 INVALID_REQUEST por UUID inválido — S4).
    """
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    # state desconhecido → 404
    resp_404 = client.get("/consent/callback", params={"state": "unk", "code": "sandbox"})
    assert resp_404.status_code == 404
    assert resp_404.json()["code"] == "CONSENT_NOT_FOUND"

    # callback válido → 200 AUTHORISED
    consent_id, state = _get_state(client, monkeypatch)
    resp_200 = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp_200.status_code == 200
    assert resp_200.json()["status"] == ConsentStatus.AUTHORISED.value


# ===========================================================================
# Grupo: revoke
# ===========================================================================

def test_revoke_authorised_returns_204(client, monkeypatch, store):
    consent_id = _authorised_consent(client, monkeypatch)
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["status"] == ConsentStatus.REVOKED.value
    assert doc["revocation_reason"] == "CUSTOMER_REQUEST"
    assert doc["revoked_by"] == "customer"
    # data com fuso (T6)
    # data com fuso (T6/F1)
    _assert_tz(doc["revoked_at"], "revoked_at")


def test_revoke_awaiting_returns_204(client, monkeypatch, store):
    """DELETE de AWAITING → 204 + REVOKED (T5)."""
    consent_id, _ = _get_state(client, monkeypatch)
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["status"] == ConsentStatus.REVOKED.value


def test_revoke_idempotent(client, monkeypatch, store):
    """Segundo DELETE → 204; documento inteiro igual ao do primeiro (T6)."""
    consent_id = _authorised_consent(client, monkeypatch)
    client.delete(f"/consent/{consent_id}")
    doc_first = asyncio.run(store.get(consents_db(), consent_id))
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc_second = asyncio.run(store.get(consents_db(), consent_id))
    # documento inteiro igual
    assert doc_first == doc_second


def test_revoke_rejected_is_idempotent(client, monkeypatch, store):
    """DELETE de REJECTED → 204 e documento inalterado (T5)."""
    consent_id, state = _get_state(client, monkeypatch)
    client.get("/consent/callback", params={"state": state, "error": "access_denied"})
    doc_before = asyncio.run(store.get(consents_db(), consent_id))
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc_after = asyncio.run(store.get(consents_db(), consent_id))
    assert doc_before == doc_after


def test_revoke_unknown_id_returns_404(client):
    resp = client.delete("/consent/00000000-0000-4000-8000-000000000001")
    assert resp.status_code == 404


def test_revoke_with_failing_oauth_url_still_returns_204(client, monkeypatch, store):
    monkeypatch.setenv("OAUTH_REVOKE_URL", "http://nowhere.invalid/revoke")
    consent_id = _authorised_consent(client, monkeypatch)


    async def _fail_post(*args, **kwargs):
        raise httpx.HTTPError("network error")

    monkeypatch.setattr(httpx.AsyncClient, "post", _fail_post)
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["token_revocation"] == "failed"


def test_revoke_without_oauth_url_skips(client, monkeypatch, store):
    monkeypatch.setenv("OAUTH_REVOKE_URL", "")
    consent_id = _authorised_consent(client, monkeypatch)
    client.delete(f"/consent/{consent_id}")
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["token_revocation"] == "skipped"


def test_revoke_persists_revoked_before_token_call(client, monkeypatch, store):
    """R1: o documento REVOKED deve estar gravado antes que _revoke_tokens seja chamado."""
    monkeypatch.setenv("OAUTH_REVOKE_URL", "http://example.com/revoke")
    consent_id = _authorised_consent(client, monkeypatch)

    status_seen_during_revoke: list[str] = []

    async def capturing_revoke(cid):
        # Lê o documento do store no momento em que _revoke_tokens é chamado
        doc = await store.get(consents_db(), cid)
        status_seen_during_revoke.append(doc["status"] if doc else "NOT_FOUND")
        return "skipped"

    monkeypatch.setattr(svc, "_revoke_tokens", capturing_revoke)
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    assert status_seen_during_revoke == [ConsentStatus.REVOKED.value], (
        f"_revoke_tokens viu status={status_seen_during_revoke!r}, esperava REVOKED"
    )


def test_revoke_malformed_revoke_url_still_revokes(client, monkeypatch, store):
    """R2: URL malformada de verdade → 204, documento REVOKED, token_revocation=failed."""
    monkeypatch.setenv("OAUTH_REVOKE_URL", "http://[::1")  # URL inválida real
    consent_id = _authorised_consent(client, monkeypatch)
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["status"] == ConsentStatus.REVOKED.value
    assert doc["token_revocation"] == "failed"


def test_revoke_reapplies_revoked_if_callback_overwrites(client, monkeypatch, store):
    """Callback concorrente grava AUTHORISED entre as duas escritas do DELETE → REVOKED é reaplicado."""
    consent_id = _authorised_consent(client, monkeypatch)

    async def callback_overwrites(cid):
        doc = await store.get(consents_db(), cid)
        doc["status"] = ConsentStatus.AUTHORISED.value
        await store.put(consents_db(), cid, doc)
        return "skipped"

    monkeypatch.setattr(svc, "_revoke_tokens", callback_overwrites)
    assert client.delete(f"/consent/{consent_id}").status_code == 204
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["status"] == ConsentStatus.REVOKED.value
    assert doc["revocation_reason"] == "CUSTOMER_REQUEST"
    assert doc["token_revocation"] == "skipped"


# ===========================================================================
# Grupo: integração M2/M3
# ===========================================================================

def test_m1_consent_passes_debts_consent_error(client, monkeypatch, store):
    """O consentimento criado pelo fluxo M1 deve ser aceito por debts._consent_error."""
    consent_id, state = _get_state(client, monkeypatch)
    client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert _consent_error(doc) is None


def test_require_consent_flow(client, monkeypatch, store):
    """Com REQUIRE_CONSENT=true: POST /simulation OK antes, 403 após DELETE (T4)."""
    monkeypatch.setenv("REQUIRE_CONSENT", "true")
    consent_id = _authorised_consent(
        client, monkeypatch, scopes=["debts:read", "simulation:read"]
    )
    doc = asyncio.run(store.get(consents_db(), consent_id))

    snapshot_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    asyncio.run(store.put(snapshots_db(), snapshot_id, {
        "snapshot_id": snapshot_id,
        "consent_id": consent_id,
        "pseudonym_id": doc["pseudonym_id"],
        "debts": [{
            "debt_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "institution": "itau", "product_type": "CREDIT_CARD",
            "outstanding_balance": 1000.0, "cet_annual": 100.0,
            "monthly_payment": 100.0, "remaining_installments": 12, "priority_rank": 1,
        }],
        "total_outstanding": 1000.0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    }))

    # libera /simulation
    resp_before = client.post("/simulation", json={"snapshot_id": snapshot_id, "monthly_budget": 500.0})
    assert resp_before.status_code == 200, resp_before.text

    # revogar
    client.delete(f"/consent/{consent_id}")

    # bloqueia /simulation
    resp_after = client.post("/simulation", json={"snapshot_id": snapshot_id, "monthly_budget": 500.0})
    assert resp_after.status_code == 403
    assert resp_after.json()["code"] == "CONSENT_REQUIRED"


# ===========================================================================
# Grupo: lgpd  — T2, T3 hardened
# ===========================================================================

def test_lgpd_full_flow_no_cpf_anywhere(client, monkeypatch, store, caplog):
    """Fluxo completo: create→callback→status→revoke→POST inválido.
    CPF não aparece em nenhuma resposta, doc, banco, nem log (T3)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")

    all_responses: list[str] = []

    with caplog.at_level(logging.DEBUG):
        # 1. create
        resp = _post_consent(client)
        all_responses.append(resp.text)
        consent_id = resp.json()["consent_id"]
        state = parse_qs(urlparse(resp.json()["redirect_url"]).query)["state"][0]

        # 2. callback
        r2 = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
        all_responses.append(r2.text)

        # 3. status
        r3 = client.get(f"/consent/{consent_id}")
        all_responses.append(r3.text)

        # 4. revoke
        r4 = client.delete(f"/consent/{consent_id}")
        all_responses.append(r4.text)

        # 5. POST inválido (DV errado — fica no serviço, não chega ao handler de validação)
        r5 = _post_consent(client, cpf=CPF_BAD_DV)
        all_responses.append(r5.text)

        # 6. POST com CPF formatado — cai no RequestValidationError handler (F4)
        r6 = _post_consent(client, cpf="529.982.247-25")
        assert r6.status_code == 400
        assert r6.json()["code"] == "INVALID_REQUEST"
        assert "529.982.247-25" not in r6.text
        all_responses.append(r6.text)

    cpf_formats = [
        CPF_VALID,
        "529.982.247-25",   # com pontuação
        "529982247-25",
        "5299822472",       # truncado
    ]
    for cpf_fmt in cpf_formats:
        for resp_text in all_responses:
            assert cpf_fmt not in resp_text, f"CPF {cpf_fmt!r} encontrado em resp: {resp_text[:200]}"

    # todos os bancos do store
    for db, docs in store._dbs.items():
        for doc_str in (json.dumps(d) for d in docs.values()):
            for cpf_fmt in cpf_formats:
                assert cpf_fmt not in doc_str, f"CPF {cpf_fmt!r} encontrado em {db}: {doc_str[:200]}"

    # logs
    for cpf_fmt in cpf_formats:
        assert cpf_fmt not in caplog.text, f"CPF {cpf_fmt!r} encontrado nos logs"


def test_lgpd_validation_error_no_cpf_in_response(client, monkeypatch):
    """400 de RequestValidationError (pattern) não ecoa o CPF (T2)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")

    for bad_cpf in ("529.982.247-25", "529982247256"):
        resp = _post_consent(client, cpf=bad_cpf)
        assert resp.status_code == 400
        assert resp.json()["code"] == "INVALID_REQUEST"
        assert bad_cpf not in resp.text
        assert CPF_VALID not in resp.text


# ===========================================================================
# Grupo: openapi contract  (O8)
# ===========================================================================

# Códigos de erro por operação que o YAML e o app.openapi() precisam declarar
# (sem 200/201/204, 401 e 429).
_CONSENT_EXPECTED_ERRORS: dict[str, dict[str, set[str]]] = {
    "/consent": {
        "post": {"400", "503"},
    },
    "/consent/callback": {
        "get": {"400", "403", "404", "409", "503"},
    },
    "/consent/{consentId}": {
        "get": {"400", "404", "503"},
        "delete": {"400", "404", "503"},
    },
}


def _yaml_error_codes(route: str, method: str) -> set[str]:
    """Códigos de erro do openapi.yaml (ignora sucesso, 401 e 429, que vêm do gateway)."""
    spec = yaml.safe_load(OPENAPI_YAML.read_text())
    responses = spec["paths"][route][method]["responses"]
    return {
        str(k) for k in responses
        if str(k) not in ("200", "201", "204", "401", "429")
    }


def _fastapi_error_codes(app_spec: dict, route: str, method: str) -> set[str]:
    """Códigos de erro do spec gerado pelo FastAPI (rota com {consent_id}; no YAML é {consentId})."""
    fastapi_route = route.replace("{consentId}", "{consent_id}")
    responses = app_spec["paths"][fastapi_route][method]["responses"]
    return {
        str(k) for k in responses
        if str(k) not in ("200", "201", "204", "401", "429")
    }


def test_openapi_consent_responses_match_yaml_and_code(client):
    """O8: YAML, app.openapi() e a tabela têm exatamente os mesmos códigos de erro."""
    app_spec = client.app.openapi()

    for route, methods in _CONSENT_EXPECTED_ERRORS.items():
        for method, expected_errors in methods.items():
            yaml_codes = _yaml_error_codes(route, method)
            fastapi_codes = _fastapi_error_codes(app_spec, route, method)
            assert yaml_codes == expected_errors, (
                f"{method.upper()} {route}: YAML has {yaml_codes}, expected {expected_errors}"
            )
            assert fastapi_codes == expected_errors, (
                f"{method.upper()} {route}: FastAPI spec has {fastapi_codes}, expected {expected_errors}"
            )

    # nenhuma operação /consent* anuncia 422 (o handler converte em 400)
    for route in app_spec["paths"]:
        if route.startswith("/consent"):
            for method_obj in app_spec["paths"][route].values():
                assert "422" not in method_obj.get("responses", {}), (
                    f"{route}: FastAPI spec announces 422 (should be suppressed)"
                )

    # toda resposta de erro de /consent* referencia ErrorResponse
    error_response_ref = "#/components/schemas/ErrorResponse"
    for route in app_spec["paths"]:
        if not route.startswith("/consent"):
            continue
        for method_obj in app_spec["paths"][route].values():
            for code, resp_obj in method_obj.get("responses", {}).items():
                if str(code) in ("200", "201", "204"):
                    continue
                schema = resp_obj.get("content", {}).get("application/json", {}).get("schema", {})
                assert schema.get("$ref") == error_response_ref, (
                    f"{route} HTTP {code}: schema ref is {schema.get('$ref')!r}, expected {error_response_ref!r}"
                )


# ===========================================================================
# Grupo: configuração de produção  (C2, F5)
# ===========================================================================

@pytest.mark.parametrize("app_env, key", [
    ("production", None),               # produção sem chave
    ("development", "not-hex-value"),   # chave não-hex
], ids=["producao-sem-chave", "chave-nao-hex"])
def test_pseudonymization_key_error_returns_503(client, monkeypatch, app_env, key):
    """PseudonymizationKeyError → 503 NOT_CONFIGURED, sem vazar a mensagem original (C2/F5)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    monkeypatch.setenv("APP_ENV", app_env)
    if key is None:
        monkeypatch.delenv("PSEUDONYM_HMAC_KEY", raising=False)
    else:
        monkeypatch.setenv("PSEUDONYM_HMAC_KEY", key)
    ps._master_key.cache_clear()
    try:
        resp = _post_consent(client)
    finally:
        ps._master_key.cache_clear()
    assert resp.status_code == 503
    assert resp.json()["code"] == "NOT_CONFIGURED"
    # nome da variável de config não deve vazar na resposta (F5)
    assert "PSEUDONYM_HMAC_KEY" not in resp.text
