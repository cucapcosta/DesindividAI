import pytest
from langchain_core.messages import AIMessage

from src.agents.agent1 import run_agent1, tools
from src.agents.agent1.agent import AgentExecutionError
from src.agents.agent1.config import ConfigurationError
from src.agents.agent1.repository import InMemoryRepository, get_repository
from src.agents.agent1.sandbox_data import SANDBOX_CPF, sandbox_consents
from src.agents.agent1.sources import (
    SandboxOpenFinanceClient,
    SandboxSerasaClient,
    SourceUnavailableError,
    build_open_finance_client,
    build_serasa_client,
)
from tests.fakes import ToolCallingFakeModel, happy_path_granite, tool_call
from src.privacy.pseudonymization import pseudonymize_cpf


@pytest.fixture
def consent():
    return sandbox_consents(pseudonymize_cpf(SANDBOX_CPF))[0]


@pytest.fixture
def repo(consent):
    return InMemoryRepository([consent])


def run(consent, repo, llm=None, **kwargs):
    kwargs.setdefault("open_finance", SandboxOpenFinanceClient())
    kwargs.setdefault("serasa", SandboxSerasaClient())
    return run_agent1(consent, repository=repo, llm=llm or happy_path_granite(consent), **kwargs)


def test_granite_drives_tools_and_persists_prioritized_snapshot(consent, repo):
    snapshot = run(consent, repo)

    assert repo.get_snapshot(snapshot["snapshot_id"]) is snapshot
    assert snapshot["pseudonym_id"] == consent["pseudonym_id"]
    institutions = {d["institution"] for d in snapshot["debts"]}
    assert "santander" not in institutions          # fora do consentimento
    assert "lojas-exemplo" in institutions          # só no Serasa
    cets = [d["cet_annual"] for d in snapshot["debts"]]
    assert cets == sorted(cets, reverse=True)
    assert snapshot["debts"][0]["product_type"] == "CREDIT_CARD"
    assert snapshot["debts"][0]["is_negativado"]
    assert [c["tool"] for c in snapshot["agent"]["tool_calls"]] == [
        "get_open_finance_debts", "get_serasa_data", "calculate_cet"]


def test_snapshot_flags_sandboxed_sources(consent, repo):
    snapshot = run(consent, repo)
    assert snapshot["sources"]["open_finance"]["sandbox"] is True
    assert snapshot["sources"]["serasa"]["sandbox"] is True


def test_snapshot_contains_no_clear_identifiers(consent, repo):
    text = str(run(consent, repo))
    assert SANDBOX_CPF not in text
    assert "ITAU-CP-2025-000123" not in text
    assert "LJX-CREDIARIO-889900" not in text


def test_llm_cannot_query_other_customer_and_run_fails_loudly(consent, repo):
    llm = ToolCallingFakeModel(messages=iter([
        tool_call("get_open_finance_debts", {"consent_id": "outro-consentimento"}, "1"),
        AIMessage(content="Não consegui."),
    ]))
    with pytest.raises(AgentExecutionError):
        run(consent, repo, llm=llm)
    assert repo._snapshots == {}   # nada persistido sem o agente concluir


def test_serasa_unavailable_still_produces_snapshot(consent, repo):
    class DownSerasa:
        def fetch_report(self, pseudonym_id):
            raise SourceUnavailableError("timeout")

    snapshot = run(consent, repo, serasa=DownSerasa())
    assert snapshot["sources"]["serasa"] == {"available": False, "sandbox": False}
    assert all(d["source"] == "open_finance" for d in snapshot["debts"])


def test_tools_enforce_order(consent, repo):
    ctx = tools.RunContext(consent=consent, repository=repo,
                           open_finance=SandboxOpenFinanceClient(), serasa=SandboxSerasaClient())
    with pytest.raises(tools.ToolInputError):
        tools.calculate_cet(ctx)
    with pytest.raises(tools.ToolInputError):
        tools.persist_snapshot(ctx)


def test_sources_require_credentials_unless_sandbox_flag_set(monkeypatch):
    monkeypatch.setenv("OPEN_FINANCE_SANDBOX", "")
    monkeypatch.setenv("SERASA_SANDBOX", "")
    with pytest.raises(ConfigurationError, match="OPEN_FINANCE_BASE_URL"):
        build_open_finance_client()
    with pytest.raises(ConfigurationError, match="SERASA_BASE_URL"):
        build_serasa_client()


def test_sandbox_flags_are_independent(monkeypatch):
    monkeypatch.setenv("SERASA_SANDBOX", "")
    assert isinstance(build_open_finance_client(), SandboxOpenFinanceClient)
    with pytest.raises(ConfigurationError, match="SERASA_BASE_URL"):
        build_serasa_client()


def test_cloudant_is_always_required():
    get_repository.cache_clear()
    try:
        with pytest.raises(ConfigurationError, match="CLOUDANT_URL"):
            get_repository()
    finally:
        get_repository.cache_clear()


def test_granite_is_always_required(consent, repo):
    with pytest.raises(ConfigurationError, match="WATSONX_URL"):
        run_agent1(consent, repository=repo, open_finance=SandboxOpenFinanceClient(),
                   serasa=SandboxSerasaClient())


def test_snapshot_debts_follow_api_contract_consumed_by_agent2(consent, repo):
    """O Agente 2 (M3) lê snapshot["debts"] com o shape e as unidades do schema Debt da API."""
    from src.api.models import Debt

    snapshot = run(consent, repo)
    card = snapshot["debts"][0]
    assert card["cet_annual"] == 369.07                 # % a.a., não fração
    assert snapshot["weighted_avg_cet"] > 1             # % a.a.
    for debt in snapshot["debts"]:
        Debt.model_validate(debt)                       # campos extras são ignorados
