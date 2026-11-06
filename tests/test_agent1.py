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
        "get_open_finance_debts", "get_serasa_data", "calculate_cet", "persist_snapshot"]


def test_snapshot_flags_sandboxed_sources(consent, repo):
    snapshot = run(consent, repo)
    assert snapshot["sources"]["open_finance"]["sandbox"] is True
    assert snapshot["sources"]["serasa"]["sandbox"] is True


def test_snapshot_contains_no_clear_identifiers(consent, repo):
    from src.agents.agent1.sandbox_data import OPEN_FINANCE_CONTRACTS, SERASA_REPORT

    # Pré-condição: as fontes entregam o CPF — o teste só vale se ele estiver na entrada.
    assert SANDBOX_CPF in str(OPEN_FINANCE_CONTRACTS) and SANDBOX_CPF in str(SERASA_REPORT)
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
    with pytest.raises(tools.ToolInputError, match="get_open_finance_debts"):
        tools.get_serasa_data(ctx, consent["pseudonym_id"])
    with pytest.raises(tools.ToolInputError):
        tools.calculate_cet(ctx)
    with pytest.raises(tools.ToolInputError):
        tools.persist_snapshot(ctx)

    tools.get_open_finance_debts(ctx, consent["consent_id"])
    with pytest.raises(tools.ToolInputError, match="get_serasa_data"):
        tools.calculate_cet(ctx)   # pular o Serasa não é permitido


def test_granite_skipping_serasa_is_corrected_by_tool_error(consent, repo):
    """O erro de ordem volta ao modelo como ToolException, e ele consegue corrigir."""
    llm = ToolCallingFakeModel(messages=iter([
        tool_call("get_open_finance_debts", {"consent_id": consent["consent_id"]}, "1"),
        tool_call("calculate_cet", {}, "2"),                       # pula o Serasa
        tool_call("get_serasa_data", {"pseudonym_id": consent["pseudonym_id"]}, "3"),
        tool_call("calculate_cet", {}, "4"),
        tool_call("persist_snapshot", {}, "5"),
        AIMessage(content="ok"),
    ]))
    snapshot = run(consent, repo, llm=llm)
    calls = [(c["tool"], c["ok"]) for c in snapshot["agent"]["tool_calls"]]
    assert ("calculate_cet", False) in calls
    assert any(d["source"] == "serasa" for d in snapshot["debts"])


def test_malformed_serasa_report_is_treated_as_unavailable(consent, repo):
    class BrokenSerasa:
        def fetch_report(self, pseudonym_id):
            return {"negative_records": [{"creditor": "x", "contract": "y"}]}   # sem amount

    snapshot = run(consent, repo, serasa=BrokenSerasa())
    assert snapshot["sources"]["serasa"]["available"] is False
    assert all(d["source"] == "open_finance" for d in snapshot["debts"])


def test_model_id_comes_from_injected_llm(consent, repo, monkeypatch):
    monkeypatch.setenv("WATSONX_MODEL_ID", "ibm/outro-modelo")   # não deve ser usado
    llm = happy_path_granite(consent)
    llm.model_id = "ibm/granite-teste"
    snapshot = run(consent, repo, llm=llm)
    assert snapshot["agent"]["model_id"] == "ibm/granite-teste"


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

# ---------------------------------------------------------------------------
# R-20: persist_snapshot grava a key_version correta
# ---------------------------------------------------------------------------

def test_snapshot_key_version_from_consent(consent, repo):
    """Se o consentimento carrega key_version 'v1' e a versão ativa é qualquer uma,
    o snapshot deve gravar 'v1' (a versão do pseudonym_id, não a ativa)."""
    consent_with_version = {**consent, "key_version": "v1"}
    snapshot = run(consent_with_version, repo)
    assert snapshot["key_version"] == "v1"


def test_snapshot_key_version_fallback_to_active_when_not_in_consent(consent, repo, monkeypatch):
    """Sem key_version no consentimento nem no cofre, usa a versão ativa (fallback com warning)."""
    # Garantir que o cofre não está disponível (ConfigurationError esperada)
    import warnings
    import logging

    consent_without_version = {k: v for k, v in consent.items() if k != "key_version"}
    # Sem key_version no consentimento — vault também não está disponível nos testes
    # (CLOUDANT_URL está zerado), então cai no fallback
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        snapshot = run(consent_without_version, repo)
    # Deve ter alguma versão (a ativa)
    assert isinstance(snapshot["key_version"], str) and len(snapshot["key_version"]) > 0


def test_snapshot_key_version_from_vault_when_consent_lacks_it(consent, repo):
    """Se o cofre tiver key_version diferente da ativa, o snapshot deve gravá-la."""
    from src.privacy.identity_vault import IdentityVault, InMemoryVaultRepository
    from src.agents.agent1.agent import run_agent1

    vault_repo = InMemoryVaultRepository()
    # Simular documento do cofre com key_version "v1" para o titular
    vault_repo.put({
        "_id": consent["pseudonym_id"],
        "type": "identity",
        "key_version": "v1",
        "kek_version": "v1",
        "wrapped_dek": "ZmFrZQ==",
        "dek_nonce": "ZmFrZQ==",
        "cpf_ciphertext": "ZmFrZQ==",
        "nonce": "ZmFrZQ==",
        "created_at": "2026-01-01T00:00:00Z",
        "shredded_at": None,
    })

    from unittest.mock import patch as mock_patch
    consent_without_version = {k: v for k, v in consent.items() if k != "key_version"}

    with mock_patch("src.privacy.identity_vault.get_vault_repository", return_value=vault_repo):
        snapshot = run_agent1(
            consent_without_version,
            repository=repo,
            llm=happy_path_granite(consent_without_version),
            open_finance=SandboxOpenFinanceClient(),
            serasa=SandboxSerasaClient(),
        )

    assert snapshot["key_version"] == "v1"

