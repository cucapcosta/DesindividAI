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


# --- Jobs: resultado não buscado expira (🔴1) ---------------------------------

def _start_slow_job(client, monkeypatch):
    """Dispara um job que só termina após release.set(); devolve (release, contador)."""
    import threading

    from src.api.routers import debts

    release, runs = threading.Event(), []
    fast_run = debts.run_agent1

    def slow_run(consent, **kw):
        runs.append(1)
        release.wait(5)
        return fast_run(consent, **kw)

    monkeypatch.setattr(debts, "run_agent1", slow_run)
    monkeypatch.setenv("DEBTS_SYNC_TIMEOUT_S", "0.05")
    assert client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID}).status_code == 202
    return release, runs


def _finish(release):
    from src.api.routers import debts

    release.set()
    debts._jobs[SANDBOX_CONSENT_ID].future.result(timeout=5)


def test_unfetched_result_expires_and_next_call_recollects(client, monkeypatch):
    release, runs = _start_slow_job(client, monkeypatch)
    _finish(release)                      # terminou, mas ninguém buscou o resultado

    monkeypatch.setenv("DEBTS_RESULT_TTL_S", "-1")   # já expirado
    monkeypatch.setenv("DEBTS_SYNC_TIMEOUT_S", "5")
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 200
    assert len(runs) == 2                 # nova coleta, não o retrato velho


def test_recent_result_is_delivered_within_ttl(client, monkeypatch):
    release, runs = _start_slow_job(client, monkeypatch)
    _finish(release)

    monkeypatch.setenv("DEBTS_SYNC_TIMEOUT_S", "5")
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 200
    assert len(runs) == 1


def test_expired_error_is_not_returned_to_later_calls(client, monkeypatch):
    from src.api.routers import debts

    import threading

    calls, release = [], threading.Event()
    good_run = debts.run_agent1

    def flaky(consent, **kw):
        calls.append(1)
        if len(calls) == 1:
            release.wait(5)
            raise SourceUnavailableError("instabilidade")
        return good_run(consent, **kw)

    monkeypatch.setattr(debts, "run_agent1", flaky)
    monkeypatch.setenv("DEBTS_SYNC_TIMEOUT_S", "0.05")   # cliente desiste antes do fim
    assert client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID}).status_code == 202
    release.set()
    debts._jobs[SANDBOX_CONSENT_ID].future.exception(timeout=5)

    monkeypatch.setenv("DEBTS_RESULT_TTL_S", "-1")
    monkeypatch.setenv("DEBTS_SYNC_TIMEOUT_S", "5")
    assert client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID}).status_code == 200


# --- Erros sem tratamento específico (🔴2) ------------------------------------

def test_unexpected_agent_error_is_500_with_code(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr("src.api.routers.debts.run_agent1", boom)
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "bug" not in response.json()["message"]       # sem detalhe interno


def test_repository_failure_is_500_with_code(client, monkeypatch):
    def broken(consent_id):
        raise ConnectionError("cloudant")

    monkeypatch.setattr(client.repo, "get_consent", broken)
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"


@pytest.mark.parametrize("expires_at, expected", [
    ((datetime.now(timezone.utc) + timedelta(days=30)).replace(tzinfo=None).isoformat(), 200),
    ((datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None).isoformat(), 403),
    ("amanhã", 403),
])
def test_expires_at_without_timezone_is_read_as_utc(client, expires_at, expected):
    add_consent(client, expires_at=expires_at)
    assert client.get("/debts", params={"consent_id": OTHER_ID}).status_code == expected


# --- 502/503 passando pelo AgentExecutor -------------------------------------

def test_granite_stopping_early_is_502_through_executor(client, monkeypatch):
    from langchain_core.messages import AIMessage

    from tests.fakes import ToolCallingFakeModel, tool_call

    def lazy_granite(consent, **kw):
        llm = ToolCallingFakeModel(messages=iter([
            tool_call("get_open_finance_debts", {"consent_id": consent["consent_id"]}, "1"),
            AIMessage(content="Pronto."),                 # para sem persistir
        ]))
        return run_agent1(consent, llm=llm, **kw)

    monkeypatch.setattr("src.api.routers.debts.run_agent1", lazy_granite)
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 502
    assert response.json()["code"] == "AGENT_FAILED"
    assert client.repo._snapshots == {}


@pytest.mark.parametrize("client_behaviour", [
    "down",          # receptor fora do ar
    "malformed",     # payload sem campos obrigatórios
])
def test_open_finance_failure_is_503_through_executor(client, monkeypatch, client_behaviour):
    class BrokenOpenFinance:
        def fetch_contracts(self, consent):
            if client_behaviour == "down":
                raise SourceUnavailableError("receptor fora do ar")
            return [{"productSubType": "CHEQUE_ESPECIAL"}]

    monkeypatch.setattr("src.agents.agent1.agent.build_open_finance_client", BrokenOpenFinance)
    response = client.get("/debts", params={"consent_id": SANDBOX_CONSENT_ID})
    assert response.status_code == 503
    assert response.json()["code"] == "SOURCE_UNAVAILABLE"
