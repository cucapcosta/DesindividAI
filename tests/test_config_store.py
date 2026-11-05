import pytest

from src.agents.agent1.config import ConfigurationError
from src.core.config import consents_db, decisions_db, snapshots_db, watsonx_scope
from src.core.errors import ServiceError
from src.storage.store import CloudantStore, MemoryStore, build_store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("STORE_BACKEND", "CLOUDANT_URL", "CLOUDANT_APIKEY", "WATSONX_SPACE_ID", "WATSONX_PROJECT_ID",
                "CLOUDANT_DB_SNAPSHOTS", "CLOUDANT_DB_CONSENTS", "CLOUDANT_DB_DECISIONS"):
        monkeypatch.delenv(var, raising=False)


def test_store_without_credentials_fails_instead_of_silent_memory():
    with pytest.raises(ServiceError) as exc:
        build_store()
    assert exc.value.status_code == 503
    assert "STORE_BACKEND=memory" in exc.value.message


def test_store_memory_is_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("STORE_BACKEND", "memory")
    assert isinstance(build_store(), MemoryStore)


def test_store_cloudant_with_credentials(monkeypatch):
    monkeypatch.setenv("CLOUDANT_URL", "https://example.cloudant.test")
    monkeypatch.setenv("CLOUDANT_APIKEY", "k")
    assert isinstance(build_store(), CloudantStore)


def test_db_names_are_shared_and_overridable(monkeypatch):
    assert (snapshots_db(), consents_db(), decisions_db()) == ("financial_snapshots", "consent_registry", "decision_traces")
    monkeypatch.setenv("CLOUDANT_DB_SNAPSHOTS", "snap_x")
    assert snapshots_db() == "snap_x"


def test_watsonx_scope_prefers_space_then_project(monkeypatch):
    with pytest.raises(ConfigurationError, match="WATSONX_PROJECT_ID ou WATSONX_SPACE_ID"):
        watsonx_scope()
    monkeypatch.setenv("WATSONX_PROJECT_ID", "proj")
    assert watsonx_scope() == {"project_id": "proj"}
    monkeypatch.setenv("WATSONX_SPACE_ID", "space")
    assert watsonx_scope() == {"space_id": "space"}
