"""Testes unitários para src/agents/agent1/repository.py — sem Cloudant real."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agents.agent1.repository import CloudantRepository, InMemoryRepository


# ---------------------------------------------------------------------------
# Fake Cloudant client
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, data: dict):
        self._data = data

    def get_result(self) -> dict:
        return self._data


class FakeCloudantClient:
    """Substituto mínimo do IBM Cloudant SDK."""

    def __init__(self):
        # post_find retorna docs vazios por padrão; pode ser sobrescrito por teste.
        self._post_find_result: dict = {"docs": []}
        self._put_document_calls: list[dict] = []
        self._get_document_result: Any = None  # dict ou exceção

    # -- helpers de configuração ----------------------------------------------

    def configure_post_find(self, docs: list[dict]) -> None:
        self._post_find_result = {"docs": docs}

    def configure_get_document(self, result: Any) -> None:
        """Passe um dict para sucesso ou uma exceção para erro."""
        self._get_document_result = result

    # -- SDK stubs ------------------------------------------------------------

    def post_find(self, db: str, selector: dict, limit: int = 1) -> _FakeResult:
        return _FakeResult(self._post_find_result)

    def put_document(self, db: str, doc_id: str, document: Any) -> _FakeResult:
        self._put_document_calls.append({"db": db, "doc_id": doc_id, "document": document})
        return _FakeResult({"id": doc_id, "rev": "1-abc"})

    def get_document(self, db: str, doc_id: str) -> _FakeResult:
        if isinstance(self._get_document_result, Exception):
            raise self._get_document_result
        return _FakeResult(self._get_document_result or {})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONSENTS_DB = "test_consents"
SNAPSHOTS_DB = "test_snapshots"


@pytest.fixture
def fake_client() -> FakeCloudantClient:
    return FakeCloudantClient()


@pytest.fixture
def repo(fake_client: FakeCloudantClient) -> CloudantRepository:
    return CloudantRepository(
        client=fake_client,
        consents_db=CONSENTS_DB,
        snapshots_db=SNAPSHOTS_DB,
    )


# ---------------------------------------------------------------------------
# InMemoryRepository — smoke tests
# ---------------------------------------------------------------------------

class TestInMemoryRepository:
    def test_get_consent_found(self):
        consent = {"consent_id": "c1", "status": "AUTHORISED"}
        repo = InMemoryRepository(consents=[consent])
        assert repo.get_consent("c1") == consent

    def test_get_consent_not_found(self):
        repo = InMemoryRepository()
        assert repo.get_consent("nope") is None

    def test_save_and_get_snapshot(self):
        repo = InMemoryRepository()
        snap = {"snapshot_id": "s1", "data": 42}
        repo.save_snapshot(snap)
        assert repo.get_snapshot("s1") == snap

    def test_get_snapshot_not_found(self):
        repo = InMemoryRepository()
        assert repo.get_snapshot("missing") is None


# ---------------------------------------------------------------------------
# CloudantRepository.get_consent
# ---------------------------------------------------------------------------

class TestCloudantGetConsent:
    def test_found_returns_first_doc(self, repo, fake_client):
        consent = {"consent_id": "ccc-111", "status": "AUTHORISED"}
        fake_client.configure_post_find([consent])
        result = repo.get_consent("ccc-111")
        assert result == consent

    def test_not_found_returns_none(self, repo, fake_client):
        fake_client.configure_post_find([])
        result = repo.get_consent("ghost-id")
        assert result is None


# ---------------------------------------------------------------------------
# CloudantRepository.save_snapshot
# ---------------------------------------------------------------------------

class TestCloudantSaveSnapshot:
    def test_uses_snapshot_id_as_doc_id(self, repo, fake_client):
        """O _id do documento deve ser igual ao snapshot_id."""
        snap = {"snapshot_id": "snap-abc-123", "pseudonym_id": "u1", "debts": []}
        repo.save_snapshot(snap)

        assert len(fake_client._put_document_calls) == 1
        call = fake_client._put_document_calls[0]
        assert call["doc_id"] == "snap-abc-123"
        assert call["db"] == SNAPSHOTS_DB

    def test_document_contains_snapshot_id_as_id_field(self, repo, fake_client):
        """O Document enviado ao Cloudant deve ter _id = snapshot_id."""
        snap = {"snapshot_id": "snap-xyz", "debts": [{"d": 1}]}
        repo.save_snapshot(snap)

        call = fake_client._put_document_calls[0]
        # Document.from_dict preserva _id; verificamos a struct bruta do doc
        doc = call["document"]
        # from_dict pode devolver objeto ou dict — verificamos _id nos dois casos
        raw = doc.to_dict() if hasattr(doc, "to_dict") else doc
        assert raw.get("_id") == "snap-xyz"


# ---------------------------------------------------------------------------
# CloudantRepository.get_snapshot
# ---------------------------------------------------------------------------

class TestCloudantGetSnapshot:
    def test_found_returns_dict(self, repo, fake_client):
        expected = {"snapshot_id": "s1", "debts": []}
        fake_client.configure_get_document(expected)
        assert repo.get_snapshot("s1") == expected

    def test_404_returns_none(self, repo, fake_client):
        from ibm_cloud_sdk_core import ApiException

        exc = ApiException(404, message="not found")
        fake_client.configure_get_document(exc)
        assert repo.get_snapshot("missing") is None

    def test_other_api_error_propagates(self, repo, fake_client):
        from ibm_cloud_sdk_core import ApiException

        exc = ApiException(500, message="internal error")
        fake_client.configure_get_document(exc)

        with pytest.raises(ApiException) as exc_info:
            repo.get_snapshot("bad")
        assert exc_info.value.status_code == 500
