"""Token IAM do canal nas rotas de consentimento (src/core/channel_auth.py)."""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import src.services.consent_service as consent_service
from src.core import channel_auth
from src.privacy.identity_vault import IdentityVault, InMemoryVaultRepository

CHANNEL = "iam-ServiceId-canal-teste"
CONSENT_BODY = {"cpf": "52998224725", "institutions": ["bb"], "scopes": ["debts:read"]}


def _keypair(kid: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return private, {**jwk, "kid": kid, "alg": "RS256"}


SIGNING_KEY, PUBLIC_JWK = _keypair("iam-key-1")
OTHER_KEY, _ = _keypair("iam-key-1")


def _token(*, key=SIGNING_KEY, kid="iam-key-1", iam_id=CHANNEL, iss=channel_auth.ISSUER, exp_in=600):
    now = int(time.time())
    claims = {"iam_id": iam_id, "iss": iss, "iat": now, "exp": now + exp_in}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture(autouse=True)
def memory_vault(monkeypatch):
    vault = IdentityVault(repository=InMemoryVaultRepository())
    monkeypatch.setattr(consent_service, "_identity_vault", lambda: vault)


@pytest.fixture
def jwks_calls(monkeypatch):
    calls = []

    async def fake_fetch():
        calls.append(1)
        return [PUBLIC_JWK]

    monkeypatch.setattr(channel_auth, "_fetch_jwks", fake_fetch)
    monkeypatch.setattr(channel_auth, "_jwks_cache", {"keys": [], "fetched_at": 0.0})
    return calls


@pytest.fixture
def secured(client, monkeypatch, jwks_calls):
    monkeypatch.setenv("REQUIRE_CHANNEL_AUTH", "true")
    monkeypatch.setenv("CHANNEL_IAM_IDS", f"outro-canal, {CHANNEL}")
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    return client


def _post(client, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/consent", json=CONSENT_BODY, headers=headers)


def test_valid_channel_token_creates_consent(secured):
    assert _post(secured, _token()).status_code == 201


@pytest.mark.parametrize("token", [
    None,
    "nao-e-um-jwt",
    _token(key=OTHER_KEY),                       # assinatura de outra chave com o mesmo kid
    _token(exp_in=-60),                          # expirado
    _token(iss="https://emissor.falso/identity"),
    _token(iam_id="iam-ServiceId-nao-autorizado"),
    _token(kid="kid-desconhecido"),
], ids=["sem-token", "malformado", "assinatura", "expirado", "emissor", "canal", "kid"])
def test_invalid_channel_token_is_401(secured, token):
    resp = _post(secured, token)
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


def test_get_and_delete_also_require_token_but_callback_does_not(secured):
    created = _post(secured, _token()).json()
    consent_id = created["consent_id"]
    assert secured.get(f"/consent/{consent_id}").status_code == 401
    assert secured.delete(f"/consent/{consent_id}").status_code == 401
    state = created["redirect_url"].split("state=")[1].split("&")[0]
    assert secured.get("/consent/callback", params={"state": state, "code": "sandbox"}).status_code == 200
    auth = {"Authorization": f"Bearer {_token()}"}
    assert secured.get(f"/consent/{consent_id}", headers=auth).json()["status"] == "AUTHORISED"
    assert secured.delete(f"/consent/{consent_id}", headers=auth).status_code == 204


def test_required_without_allowed_channels_is_503(secured, monkeypatch):
    monkeypatch.setenv("CHANNEL_IAM_IDS", "")
    resp = _post(secured, _token())
    assert resp.status_code == 503
    assert resp.json()["code"] == "NOT_CONFIGURED"


def test_production_requires_token_by_default(client, monkeypatch, jwks_calls):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REQUIRE_CHANNEL_AUTH", "")
    monkeypatch.setenv("CHANNEL_IAM_IDS", CHANNEL)
    assert _post(client).status_code == 401


def test_development_does_not_require_token_by_default(client, monkeypatch, jwks_calls):
    monkeypatch.setenv("OPEN_FINANCE_AUTH_SANDBOX", "true")
    assert _post(client).status_code == 201
    assert jwks_calls == []


def test_jwks_is_cached_and_unknown_kid_refetch_is_throttled(secured, jwks_calls):
    for _ in range(3):
        assert _post(secured, _token()).status_code == 201
    assert len(jwks_calls) == 1
    for _ in range(3):
        assert _post(secured, _token(kid="kid-desconhecido")).status_code == 401
    assert len(jwks_calls) == 1  # releitura no máximo uma vez por minuto


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer "])
def test_missing_bearer_token_message(secured, header):
    headers = {"Authorization": header} if header else {}
    resp = secured.post("/consent", json=CONSENT_BODY, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["message"] == "Token Bearer ausente."
