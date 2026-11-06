import os

# Testes NUNCA chamam serviços reais. As variáveis são definidas como vazias (e
# não removidas) porque main.py chama load_dotenv(), que só preenche variáveis
# ausentes — assim o .env local com credenciais reais não vaza para os testes.
# Exceção: RUN_CLOUD_TESTS=1 (tests/test_cloud_integration.py) precisa do .env real.
_REAL_SERVICE_VARS = (
    "WATSONX_URL", "WATSONX_PROJECT_ID", "WATSONX_SPACE_ID", "IBM_CLOUD_API_KEY",
    "CLOUDANT_URL", "CLOUDANT_APIKEY",
    "OPEN_FINANCE_BASE_URL", "OPEN_FINANCE_API_KEY",
    "SERASA_BASE_URL", "SERASA_API_KEY",
    # M1 — evitar vazamento de .env local nos testes
    "OPEN_FINANCE_AUTH_SANDBOX", "PUBLIC_BASE_URL", "OPEN_FINANCE_AUTHORIZE_URL",
    "OPEN_FINANCE_CLIENT_ID", "OPEN_FINANCE_REDIRECT_URI",
    "OAUTH_REVOKE_URL", "REQUIRE_CONSENT",
)
if os.getenv("RUN_CLOUD_TESTS") != "1":
    for var in _REAL_SERVICE_VARS:
        os.environ[var] = ""

# Fontes externas simuladas; Cloudant e Granite são substituídos por fakes injetados.
os.environ["OPEN_FINANCE_SANDBOX"] = "true"
os.environ["SERASA_SANDBOX"] = "true"
# Chave fixa só para testes — precisa existir antes de importar os módulos.
os.environ["PSEUDONYM_HMAC_KEY"] = "ab" * 32
os.environ["APP_ENV"] = "development"

import pytest  # noqa: E402  (o ambiente acima precisa existir antes dos imports da aplicação)
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402

from src.agents.recommendation_agent import build_llm  # noqa: E402
from src.main import app  # noqa: E402
from src.storage.store import MemoryStore, get_store  # noqa: E402

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
