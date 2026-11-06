"""
Testes da Fase B2 — Guarda de PII e trilha de auditoria no Agente 2.

Cobre:
  - Fluxo normal: eventos emitidos com ids e hashes corretos, sem prompt/output.
  - LLM fake com CPF no output → 500 PII_BLOCKED, nenhum trace persistido.
  - Snapshot com CPF injetado → 500 PII_BLOCKED, nenhuma simulação persistida.
  - Auditoria indisponível (_get_logger → None) → endpoints continuam 200.
  - Logger cujo record_event lança → endpoints continuam 200.

Todos sem rede; usa as fixtures client/store de tests/conftest.py.
"""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.agents.recommendation_agent import build_llm
from src.main import app
from src.services.simulation_service import decisions_db
from src.storage.store import get_store
from tests.conftest import DEBTS, SNAPSHOT_ID

# ---------------------------------------------------------------------------
# CPF válido para injeção nos testes de PII
# ---------------------------------------------------------------------------

_CPF_VALID = "529.982.247-25"   # CPF com dígitos verificadores corretos
_CPF_DIGITS = "52998224725"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fake AuditLogger que coleta eventos em memória
# ---------------------------------------------------------------------------

class _FakeAuditLogger:
    """Coleta chamadas a record_event para asserções nos testes."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def record_event(
        self,
        event_type: str,
        *,
        pseudonym_id,
        actor: str,
        **fields,
    ) -> str:
        import uuid
        trace_id = str(uuid.uuid4())
        self.events.append(
            {
                "event_type": event_type,
                "pseudonym_id": pseudonym_id,
                "actor": actor,
                "trace_id": trace_id,
                **fields,
            }
        )
        return trace_id


def _make_fake_logger_fixture(monkeypatch):
    """Cria um _FakeAuditLogger e faz monkeypatch em audit_bridge._get_logger."""
    fake = _FakeAuditLogger()
    monkeypatch.setattr("src.privacy.audit_bridge._get_logger", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simulate(client, budget: float = 1500.0):
    return client.post(
        "/simulation",
        json={"snapshot_id": SNAPSHOT_ID, "monthly_budget": budget},
    )


def _recommend(client, simulation_id: str, language_level: str = "simple"):
    return client.post(
        "/recommendation",
        json={"simulation_id": simulation_id, "language_level": language_level},
    )


# ---------------------------------------------------------------------------
# 1. Fluxo normal — eventos emitidos com ids e hashes corretos
# ---------------------------------------------------------------------------

def test_normal_flow_events_emitted(client, store, monkeypatch):
    """
    Fluxo /simulation → /recommendation: dois eventos emitidos com campos corretos.
    - agent2.simulation_created: contém simulation_id, snapshot_id, consent_id.
    - agent2.recommendation_generated: contém prompt_sha256, output_sha256, sem prompt/output.
    """
    fake = _make_fake_logger_fixture(monkeypatch)

    sim_resp = _simulate(client)
    assert sim_resp.status_code == 200
    sim_body = sim_resp.json()
    simulation_id = sim_body["simulation_id"]

    rec_resp = _recommend(client, simulation_id)
    assert rec_resp.status_code == 200
    rec_body = rec_resp.json()

    # Dois eventos: simulation_created e recommendation_generated
    event_types = [e["event_type"] for e in fake.events]
    assert "agent2.simulation_created" in event_types
    assert "agent2.recommendation_generated" in event_types

    # ----- agent2.simulation_created -----
    sim_event = next(e for e in fake.events if e["event_type"] == "agent2.simulation_created")
    assert sim_event["actor"] == "agent2"
    assert sim_event["simulation_id"] == simulation_id
    assert sim_event["snapshot_id"] == SNAPSHOT_ID

    # ----- agent2.recommendation_generated -----
    rec_event = next(e for e in fake.events if e["event_type"] == "agent2.recommendation_generated")
    assert rec_event["actor"] == "agent2"
    assert rec_event["decision_trace_id"] == rec_body["trace_id"]
    assert rec_event["simulation_id"] == simulation_id

    # prompt e output NÃO devem estar no evento
    assert "prompt" not in rec_event
    assert "output" not in rec_event

    # Verificar hashes no trace gravado no store
    trace = asyncio.run(store.get(decisions_db(), rec_body["trace_id"]))
    assert trace is not None
    assert "prompt_sha256" in trace
    assert "output_sha256" in trace

    # Os hashes no evento devem coincidir com os do trace
    assert rec_event["prompt_sha256"] == trace["prompt_sha256"]
    assert rec_event["output_sha256"] == trace["output_sha256"]

    # Verificar que output_sha256 corresponde ao output real
    assert trace["output_sha256"] == _sha256("Explicação de teste.")
    assert rec_event["output_sha256"] == _sha256("Explicação de teste.")


# ---------------------------------------------------------------------------
# 2. LLM fake com CPF no output → 500 PII_BLOCKED, sem trace, evento pii_blocked
# ---------------------------------------------------------------------------

def test_recommendation_cpf_in_llm_output_blocked(client, store, monkeypatch):
    """
    LLM retorna texto com CPF válido → POST /recommendation retorna 500 PII_BLOCKED.
    Nenhum trace deve ser persistido no store.
    O evento audit.pii_blocked deve ser emitido.
    A resposta não deve conter o CPF.
    """
    fake = _make_fake_logger_fixture(monkeypatch)

    # LLM que responde com CPF válido
    app.dependency_overrides[build_llm] = lambda: FakeListChatModel(
        responses=[f"Recomendamos quitar. seu CPF {_CPF_VALID} foi processado."]
    )

    sim_resp = _simulate(client)
    assert sim_resp.status_code == 200
    simulation_id = sim_resp.json()["simulation_id"]

    rec_resp = _recommend(client, simulation_id)
    assert rec_resp.status_code == 500
    rec_body = rec_resp.json()
    assert rec_body["code"] == "PII_BLOCKED"

    # O CPF não deve aparecer na resposta
    assert _CPF_VALID not in str(rec_body)
    assert _CPF_DIGITS not in str(rec_body)

    # Nenhum trace gravado no store (a guarda bloqueou antes do put)
    # Todos os docs em decision_traces que são traces
    all_traces = asyncio.run(store.find(decisions_db(), {"type": "trace"}, limit=100))
    assert all_traces == [], f"Trace não deveria ter sido persistido: {all_traces}"

    # Evento audit.pii_blocked emitido
    pii_events = [e for e in fake.events if e["event_type"] == "audit.pii_blocked"]
    assert len(pii_events) >= 1
    pii_event = pii_events[0]
    assert pii_event["blocked_event_type"] == "agent2.recommendation_generated"
    # O CPF não deve estar no evento
    assert _CPF_VALID not in str(pii_event)
    assert _CPF_DIGITS not in str(pii_event)


# ---------------------------------------------------------------------------
# 3. Snapshot com CPF injetado → 500 PII_BLOCKED, sem simulação persistida
# ---------------------------------------------------------------------------

def test_simulation_cpf_in_snapshot_blocked(client, store, monkeypatch):
    """
    Snapshot cujo pseudonym_id contém um CPF válido (valor CPF-formatado) →
    POST /simulation retorna 500 PII_BLOCKED e nenhuma simulação é gravada.
    """
    fake = _make_fake_logger_fixture(monkeypatch)

    # Injetar CPF como valor do pseudonym_id no snapshot
    snap = asyncio.run(store.get("financial_snapshots", SNAPSHOT_ID))
    snap["pseudonym_id"] = _CPF_VALID   # CPF mascarado → pii_guard detecta
    asyncio.run(store.put("financial_snapshots", SNAPSHOT_ID, snap))

    sim_resp = _simulate(client)
    assert sim_resp.status_code == 500
    sim_body = sim_resp.json()
    assert sim_body["code"] == "PII_BLOCKED"

    # O CPF não deve aparecer na resposta
    assert _CPF_VALID not in str(sim_body)
    assert _CPF_DIGITS not in str(sim_body)

    # Nenhuma simulação gravada no store
    all_sims = asyncio.run(store.find(decisions_db(), {"type": "simulation"}, limit=100))
    assert all_sims == [], f"Simulação não deveria ter sido persistida: {all_sims}"

    # Evento audit.pii_blocked emitido
    pii_events = [e for e in fake.events if e["event_type"] == "audit.pii_blocked"]
    assert len(pii_events) >= 1
    pii_event = pii_events[0]
    assert pii_event["blocked_event_type"] == "agent2.simulation_created"


# ---------------------------------------------------------------------------
# 4. Auditoria indisponível (_get_logger → None) → endpoints continuam 200
# ---------------------------------------------------------------------------

def test_audit_unavailable_endpoints_still_200(client, store, monkeypatch):
    """
    _get_logger retorna None (auditoria indisponível) →
    /simulation e /recommendation continuam respondendo 200.
    """
    monkeypatch.setattr("src.privacy.audit_bridge._get_logger", lambda: None)

    sim_resp = _simulate(client)
    assert sim_resp.status_code == 200
    simulation_id = sim_resp.json()["simulation_id"]

    rec_resp = _recommend(client, simulation_id)
    assert rec_resp.status_code == 200


# ---------------------------------------------------------------------------
# 5. Logger cujo record_event lança → endpoints continuam 200
# ---------------------------------------------------------------------------

def test_audit_record_event_raises_endpoints_still_200(client, store, monkeypatch):
    """
    Logger cujo record_event sempre lança RuntimeError →
    /simulation e /recommendation continuam respondendo 200.
    """
    class _BrokenLogger:
        def record_event(self, event_type, *, pseudonym_id, actor, **fields):
            raise RuntimeError("audit indisponível simulado")

    monkeypatch.setattr("src.privacy.audit_bridge._get_logger", lambda: _BrokenLogger())

    sim_resp = _simulate(client)
    assert sim_resp.status_code == 200
    simulation_id = sim_resp.json()["simulation_id"]

    rec_resp = _recommend(client, simulation_id)
    assert rec_resp.status_code == 200
