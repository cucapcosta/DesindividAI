from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.agents.agent1 import run_agent1
from src.agents.agent1.repository import InMemoryRepository
from src.agents.agent1.sandbox_data import SANDBOX_CONSENT_ID, SANDBOX_CPF, sandbox_consents
from src.agents.agent1.sources import SourceUnavailableError
from tests.fakes import happy_path_granite
from src.main import app
from src.privacy.pseudonymization import pseudonymize_cpf

OTHER_ID = "00000000-0000-4000-8000-0000000000aa"


@pytest.fixture
def client(monkeypatch):
    consents = sandbox_consents(pseudonymize_cpf(SANDBOX_CPF))
    repo = InMemoryRepository(consents)
    monkeypatch.setattr("src.api.routers.debts.get_repository", lambda: repo)
    # Um Granite fake novo por requisição, roteirizado para o consentimento consultado.
    monkeypatch.setattr(
        "src.api.routers.debts.run_agent1",
        lambda consent, **kw: run_agent1(consent, llm=happy_path_granite(consent), **kw),
    )
    monkeypatch.setattr("src.api.routers.debts._jobs", {})
    client = TestClient(app)
    client.repo = repo
    client.consents = consents
    return client


def add_consent(client, **overrides):
    consent = {**client.consents[0], "consent_id": OTHER_ID, **overrides}
    client.repo._consents[OTHER_ID] = consent


def test_returns_prioritized_debts(client):
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 200
    body = response.json()
    assert [d["priority_rank"] for d in body["debts"]] == list(range(1, len(body["debts"]) + 1))
    assert body["debts"][0]["cet_annual"] > 100      # % a.a., não fração
    assert body["total_outstanding"] == pytest.approx(sum(d["outstanding_balance"] for d in body["debts"]))
    assert client.repo.get_snapshot(body["snapshot_id"]) is not None


def test_unknown_consent_is_404(client):
    assert client.get("/debts", params={"consent_id": OTHER_ID}).status_code == 404


@pytest.mark.parametrize("overrides, code", [
    ({"status": "REVOKED"}, "CONSENT_NOT_AUTHORISED"),
    ({"scopes": ["simulation:read"]}, "INSUFFICIENT_SCOPE"),
    ({"expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}, "CONSENT_EXPIRED"),
])
def test_invalid_consent_is_403(client, overrides, code):
    add_consent(client, **overrides)
    response = client.get("/debts", params={"consent_id": OTHER_ID})
    assert response.status_code == 403
    assert response.json()["code"] == code


def test_open_finance_down_is_503(client, monkeypatch):
    def down(*args, **kwargs):
        raise SourceUnavailableError("receptor fora do ar")

    monkeypatch.setattr("src.api.routers.debts.run_agent1", down)
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 503


def test_slow_agent_returns_202_then_result_on_next_call(client, monkeypatch):
    import threading

    from src.api.routers import debts

    release = threading.Event()
    fast_run = debts.run_agent1

    def slow_run(consent, **kw):
        release.wait(5)
        return fast_run(consent, **kw)

    monkeypatch.setattr(debts, "run_agent1", slow_run)
    monkeypatch.setenv("DEBTS_SYNC_TIMEOUT_S", "0.05")

    first = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert first.status_code == 202
    assert first.json()["code"] == "PROCESSING"

    release.set()
    monkeypatch.setenv("DEBTS_SYNC_TIMEOUT_S", "5")
    second = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert second.status_code == 200
    assert debts._jobs == {}           # job liberado após entregar o resultado


def test_agent_failure_is_502(client, monkeypatch):
    from src.agents.agent1.agent import AgentExecutionError

    def failing(*args, **kwargs):
        raise AgentExecutionError("Granite parou antes do persist_snapshot")

    monkeypatch.setattr("src.api.routers.debts.run_agent1", failing)
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 502
    assert response.json()["code"] == "AGENT_FAILED"
