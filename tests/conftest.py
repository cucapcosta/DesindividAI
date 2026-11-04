import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.agents.recommendation_agent import build_llm
from src.main import app
from src.storage.store import MemoryStore, get_store

SNAPSHOT_ID = "11111111-1111-1111-1111-111111111111"

DEBTS = [
    {"debt_id": "aaaaaaaa-0000-0000-0000-000000000001", "institution": "itau", "product_type": "CREDIT_CARD",
     "outstanding_balance": 5000.0, "cet_annual": 200.0, "monthly_payment": 300.0,
     "remaining_installments": 24, "priority_rank": 1},
    {"debt_id": "aaaaaaaa-0000-0000-0000-000000000002", "institution": "bb", "product_type": "PERSONAL_LOAN",
     "outstanding_balance": 8000.0, "cet_annual": 60.0, "monthly_payment": 400.0,
     "remaining_installments": 24, "priority_rank": 2},
    {"debt_id": "aaaaaaaa-0000-0000-0000-000000000003", "institution": "caixa", "product_type": "VEHICLE_LOAN",
     "outstanding_balance": 12000.0, "cet_annual": 20.0, "monthly_payment": 500.0,
     "remaining_installments": 30, "priority_rank": 3},
]


@pytest.fixture
def store():
    s = MemoryStore()
    return s


@pytest.fixture
def client(store):
    import asyncio
    asyncio.run(store.put("financial_snapshots", SNAPSHOT_ID, {
        "pseudonym_id": "abc123", "snapshot_id": SNAPSHOT_ID, "debts": DEBTS,
        "total_outstanding": 25000.0, "generated_at": "2026-01-01T00:00:00Z",
    }))
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[build_llm] = lambda: FakeListChatModel(responses=["Explicação de teste."])
    yield TestClient(app)
    app.dependency_overrides.clear()
