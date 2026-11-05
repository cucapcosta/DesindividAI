"""Consentimento do /simulation e /recommendation: vem do snapshot (consent_id) e vale a mesma regra do /debts."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.core.config import consents_db, snapshots_db
from tests.conftest import SNAPSHOT_ID
from tests.test_api import simulate

CONSENT_ID = "22222222-2222-2222-2222-222222222222"
PSEUDONYM = "abc123"  # o mesmo do snapshot da fixture `client`


def future(days=30):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def past(days=1):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture
def enforced(client, store, monkeypatch):
    """REQUIRE_CONSENT ligado; snapshot da fixture ligado a CONSENT_ID."""
    monkeypatch.setenv("REQUIRE_CONSENT", "true")

    def setup(snapshot_extra=None, **consent):
        snapshot = asyncio.run(store.get(snapshots_db(), SNAPSHOT_ID))
        asyncio.run(store.put(snapshots_db(), SNAPSHOT_ID, {**snapshot, "consent_id": CONSENT_ID, **(snapshot_extra or {})}))
        doc = {"consent_id": CONSENT_ID, "pseudonym_id": PSEUDONYM, "status": "AUTHORISED",
               "scopes": ["simulation:read", "recommendation:read"], "expires_at": future(), **consent}
        asyncio.run(store.put(consents_db(), CONSENT_ID, doc))
        return client

    return setup


def test_valid_consent_allows_simulation_and_recommendation(enforced, store):
    client = enforced()
    resp = simulate(client)
    assert resp.status_code == 200
    stored = asyncio.run(store.get("decision_traces", f"simulation:{resp.json()['simulation_id']}"))
    assert stored["consent_id"] == CONSENT_ID
    assert client.post("/recommendation", json={"simulation_id": resp.json()["simulation_id"]}).status_code == 200


def test_revoked_consent_blocks_simulation_even_if_another_authorised_exists(enforced, store):
    client = enforced(status="REVOKED")
    # outro consentimento AUTHORISED do mesmo titular não pode "salvar" o snapshot revogado
    asyncio.run(store.put(consents_db(), "other", {"consent_id": "other", "pseudonym_id": PSEUDONYM,
                                                    "status": "AUTHORISED", "scopes": ["simulation:read"],
                                                    "expires_at": future()}))
    resp = simulate(client)
    assert resp.status_code == 403 and resp.json()["code"] == "CONSENT_REQUIRED"


def test_expired_consent_blocks(enforced):
    resp = simulate(enforced(expires_at=past()))
    assert resp.status_code == 403 and resp.json()["code"] == "CONSENT_EXPIRED"


def test_missing_scope_blocks_recommendation_only(enforced):
    client = enforced(scopes=["simulation:read"])
    sim = simulate(client)
    assert sim.status_code == 200
    resp = client.post("/recommendation", json={"simulation_id": sim.json()["simulation_id"]})
    assert resp.status_code == 403 and resp.json()["code"] == "INSUFFICIENT_SCOPE"


def test_consent_of_another_holder_does_not_apply(enforced):
    resp = simulate(enforced(pseudonym_id="outro-titular"))
    assert resp.status_code == 403 and resp.json()["code"] == "CONSENT_REQUIRED"


def test_naive_expiry_is_read_as_utc(enforced):
    naive = (datetime.now(timezone.utc) + timedelta(days=5)).replace(tzinfo=None).isoformat()
    assert simulate(enforced(expires_at=naive)).status_code == 200


def test_invalid_expiry_blocks(enforced):
    resp = simulate(enforced(expires_at="amanhã"))
    assert resp.status_code == 403


def test_snapshot_pointing_to_unknown_consent_blocks(enforced):
    client = enforced(snapshot_extra={"consent_id": "33333333-3333-3333-3333-333333333333"})
    resp = simulate(client)
    assert resp.status_code == 403 and resp.json()["code"] == "CONSENT_REQUIRED"


def test_expired_snapshot_is_not_served_even_without_consent_enforcement(client, store):
    snapshot = asyncio.run(store.get(snapshots_db(), SNAPSHOT_ID))
    asyncio.run(store.put(snapshots_db(), SNAPSHOT_ID, {**snapshot, "expires_at": past()}))
    resp = simulate(client)
    assert resp.status_code == 404 and resp.json()["code"] == "SNAPSHOT_NOT_FOUND"


def test_snapshot_with_future_expiry_is_served(client, store):
    snapshot = asyncio.run(store.get(snapshots_db(), SNAPSHOT_ID))
    asyncio.run(store.put(snapshots_db(), SNAPSHOT_ID, {**snapshot, "expires_at": future(90)}))
    assert simulate(client).status_code == 200
