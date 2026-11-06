"""
Testes de integração para os endpoints de consentimento (M1).

Usa a fixture `client` e `store` de tests/conftest.py:
  - store: MemoryStore limpo
  - client: TestClient com app.dependency_overrides[get_store] = lambda: store
CPF de teste válido: 52998224725
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.api.models import ConsentStatus
from src.core.config import consents_db
from src.storage.store import MemoryStore, get_store
from src.main import app

CPF_VALID = "52998224725"
CPF_BAD_DV = "52998224726"      # dígito verificador errado
CPF_REPEATED = "11111111111"    # sequência repetida


# ===========================================================================
# Helpers
# ===========================================================================

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
    from src.core.dates import parse_utc
    created = parse_utc(body["created_at"])
    expires = parse_utc(body["expires_at"])
    assert created.tzinfo is not None
    assert expires.tzinfo is not None
    delta = expires - created
    assert delta.days == 90  # default expiration_days


def test_create_expires_at_respects_expiration_days(client, monkeypatch, store):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    body = _created(client, expiration_days=30)
    from src.core.dates import parse_utc
    delta = parse_utc(body["expires_at"]) - parse_utc(body["created_at"])
    assert delta.days == 30


def test_create_normalizes_institutions(client, monkeypatch, store):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    _post_consent(client, institutions=[" BB", "bb", "Itau"])
    # find the doc via store
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
    # "529.982.247-25" não passa no pattern ^\d{11}$ → RequestValidationError → 400 INVALID_REQUEST
    resp = _post_consent(client, cpf="529.982.247-25")
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


def test_create_no_sandbox_no_url_returns_503(client, monkeypatch):
    monkeypatch.delenv("OPEN_FINANCE_AUTH_SANDBOX", raising=False)
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "")
    monkeypatch.setenv("OPEN_FINANCE_AUTHORIZE_URL", "")
    resp = _post_consent(client)
    assert resp.status_code == 503
    assert resp.json()["code"] == "NOT_CONFIGURED"


def test_create_real_mode_redirect_url(client, monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "")
    monkeypatch.setenv("OPEN_FINANCE_AUTHORIZE_URL", "https://auth.example.com/authorize")
    monkeypatch.setenv("OPEN_FINANCE_CLIENT_ID", "my-client")
    monkeypatch.setenv("OPEN_FINANCE_REDIRECT_URI", "https://api.example.com/consent/callback")
    resp = _post_consent(client)
    assert resp.status_code == 201
    url = resp.json()["redirect_url"]
    assert url.startswith("https://auth.example.com/authorize")
    assert "state=" in url
    assert "client_id=my-client" in url
    assert "redirect_uri=" in url


# ===========================================================================
# Grupo: callback
# ===========================================================================

def _get_state(client, monkeypatch, store) -> tuple[str, str]:
    """Cria consentimento em sandbox e retorna (consent_id, state)."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    body = _created(client)
    consent_id = body["consent_id"]
    # state está na redirect_url como ?state=<...>&code=sandbox
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(body["redirect_url"])
    state = parse_qs(parsed.query)["state"][0]
    return consent_id, state


def test_callback_code_authorises(client, monkeypatch, store):
    consent_id, state = _get_state(client, monkeypatch, store)
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == ConsentStatus.AUTHORISED.value
    assert body["authorised_at"] is not None
    # state_hash deve ter sido removido do documento
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert "state_hash" not in doc


def test_callback_consumed_state_returns_404(client, monkeypatch, store):
    """Após autorizar, o state_hash é removido — novo callback retorna 404."""
    _, state = _get_state(client, monkeypatch, store)
    client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 404


def test_callback_error_access_denied(client, monkeypatch, store):
    _, state = _get_state(client, monkeypatch, store)
    resp = client.get("/consent/callback", params={"state": state, "error": "access_denied"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "CONSENT_REJECTED"
    # verifica documento gravado
    docs = asyncio.run(store.find(consents_db(), {"rejection_reason": "ACCESS_DENIED"}, limit=1))
    assert docs and docs[0]["status"] == ConsentStatus.REJECTED.value


def test_callback_expired_awaiting(client, monkeypatch, store):
    consent_id, state = _get_state(client, monkeypatch, store)
    # Retroagir awaiting_expires_at para o passado
    doc = asyncio.run(store.get(consents_db(), consent_id))
    doc["awaiting_expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    asyncio.run(store.put(consents_db(), consent_id, doc))
    resp = client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "CONSENT_AUTHORISATION_EXPIRED"
    updated = asyncio.run(store.get(consents_db(), consent_id))
    assert updated["rejection_reason"] == "AUTHORISATION_TIMEOUT"


def test_callback_unknown_state_returns_404(client, monkeypatch):
    resp = client.get("/consent/callback", params={"state": "totally-unknown", "code": "x"})
    assert resp.status_code == 404


def test_callback_missing_state_returns_400(client):
    resp = client.get("/consent/callback", params={"code": "x"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


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


def test_callback_route_not_captured_as_consent_id(client):
    """A rota /callback não deve ser capturada por /{consent_id}."""
    resp = client.get("/consent/callback", params={"state": "x", "code": "y"})
    # deve cair no handler de callback (404 ou 400), não em 422 de UUID inválido
    assert resp.status_code in (400, 404)
    assert resp.json().get("code") != "INVALID_REQUEST" or "state" not in resp.json().get("message", "")


# ===========================================================================
# Grupo: revoke
# ===========================================================================

def _authorised_consent(client, monkeypatch, store) -> str:
    """Cria e autoriza um consentimento. Retorna consent_id."""
    consent_id, state = _get_state(client, monkeypatch, store)
    client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    return consent_id


def test_revoke_authorised_returns_204(client, monkeypatch, store):
    consent_id = _authorised_consent(client, monkeypatch, store)
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["status"] == ConsentStatus.REVOKED.value
    assert doc["revocation_reason"] == "CUSTOMER_REQUEST"
    assert doc["revoked_by"] == "customer"


def test_revoke_idempotent(client, monkeypatch, store):
    consent_id = _authorised_consent(client, monkeypatch, store)
    client.delete(f"/consent/{consent_id}")
    doc_after_first = asyncio.run(store.get(consents_db(), consent_id))
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc_after_second = asyncio.run(store.get(consents_db(), consent_id))
    assert doc_after_first["revoked_at"] == doc_after_second["revoked_at"]


def test_revoke_unknown_id_returns_404(client):
    resp = client.delete("/consent/00000000-0000-4000-8000-000000000001")
    assert resp.status_code == 404


def test_revoke_with_failing_oauth_url_still_returns_204(client, monkeypatch, store):
    monkeypatch.setenv("OAUTH_REVOKE_URL", "http://nowhere.invalid/revoke")
    consent_id = _authorised_consent(client, monkeypatch, store)

    async def _fail_post(*args, **kwargs):
        raise Exception("network error")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", _fail_post)
    resp = client.delete(f"/consent/{consent_id}")
    assert resp.status_code == 204
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["token_revocation"] == "failed"


def test_revoke_without_oauth_url_skips(client, monkeypatch, store):
    monkeypatch.setenv("OAUTH_REVOKE_URL", "")
    consent_id = _authorised_consent(client, monkeypatch, store)
    client.delete(f"/consent/{consent_id}")
    doc = asyncio.run(store.get(consents_db(), consent_id))
    assert doc["token_revocation"] == "skipped"


# ===========================================================================
# Grupo: integração M2/M3
# ===========================================================================

def test_m1_consent_passes_debts_consent_error(client, monkeypatch, store):
    """O consentimento criado pelo fluxo M1 deve ser aceito por debts._consent_error."""
    consent_id, state = _get_state(client, monkeypatch, store)
    client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    doc = asyncio.run(store.get(consents_db(), consent_id))
    from src.api.routers.debts import _consent_error
    assert _consent_error(doc) is None


def test_require_consent_flow(client, monkeypatch, store):
    """Com REQUIRE_CONSENT=true, consentimento M1 libera /simulation; após DELETE, bloqueia."""
    monkeypatch.setenv("REQUIRE_CONSENT", "true")
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    body = _created(client, scopes=["debts:read", "simulation:read"])
    consent_id = body["consent_id"]
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(body["redirect_url"]).query)["state"][0]
    client.get("/consent/callback", params={"state": state, "code": "sandbox"})
    doc = asyncio.run(store.get(consents_db(), consent_id))

    # Criar snapshot fake referenciando o consent
    from src.core.config import snapshots_db
    from datetime import datetime, timedelta, timezone
    snapshot_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    asyncio.run(store.put(snapshots_db(), snapshot_id, {
        "snapshot_id": snapshot_id,
        "consent_id": consent_id,
        "pseudonym_id": doc["pseudonym_id"],
        "debts": [],
        "total_outstanding": 0.0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    }))

    from src.services.simulation_service import ensure_consent
    # deve passar sem erro
    asyncio.run(ensure_consent(
        store,
        consent_id=consent_id,
        pseudonym_id=doc["pseudonym_id"],
        scope="simulation:read",
    ))

    # revogar e verificar que o consentimento não passa mais
    client.delete(f"/consent/{consent_id}")
    from src.core.errors import ServiceError
    with pytest.raises(ServiceError) as exc_info:
        asyncio.run(ensure_consent(
            store,
            consent_id=consent_id,
            pseudonym_id=doc["pseudonym_id"],
            scope="simulation:read",
        ))
    assert exc_info.value.status_code == 403


# ===========================================================================
# Grupo: lgpd
# ===========================================================================

def test_lgpd_cpf_not_in_store_or_response(client, monkeypatch, store, caplog):
    """CPF não deve aparecer no documento, na resposta nem no log."""
    import logging
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")

    with caplog.at_level(logging.DEBUG):
        resp = _post_consent(client)

    assert CPF_VALID not in resp.text
    # verificar todos os documentos no store
    docs = asyncio.run(store.find(consents_db(), {}, limit=100))
    for doc in docs:
        import json
        doc_str = json.dumps(doc)
        assert CPF_VALID not in doc_str
    # verificar logs
    for record in caplog.records:
        assert CPF_VALID not in record.getMessage()


def test_lgpd_cpf_not_in_validation_error_response(client, monkeypatch):
    """400 de validação não deve ecoar o CPF."""
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    resp = _post_consent(client, cpf=CPF_BAD_DV)
    assert CPF_BAD_DV not in resp.text
