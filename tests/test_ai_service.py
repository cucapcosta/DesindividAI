"""Testes unitários para src/simulation/ai_service.py."""

from __future__ import annotations

from src.simulation.ai_service import deployable_ai_service
from src.simulation.engine import DEFAULT_BB_CET_ANNUAL, simulate
from tests.conftest import DEBTS


# ---------------------------------------------------------------------------
# Fake context (emula o objeto watsonx.ai que expõe get_json())
# ---------------------------------------------------------------------------

class _FakeContext:
    def __init__(self, payload: dict):
        self._payload = payload

    def get_json(self) -> dict:
        return self._payload


def _call(payload: dict) -> dict:
    """Instancia o serviço e invoca generate com o payload dado."""
    generate = deployable_ai_service(None)
    ctx = _FakeContext(payload)
    return generate(ctx)


# ---------------------------------------------------------------------------
# Testes de contrato: cabeçalho Content-Type
# ---------------------------------------------------------------------------

def test_headers_content_type_on_success():
    payload = {"debts": DEBTS, "monthly_budget": 1500.0}
    resp = _call(payload)
    assert resp["headers"]["Content-Type"] == "application/json"


def test_headers_content_type_on_error():
    # payload sem "debts" deve devolver erro com Content-Type correto
    resp = _call({"monthly_budget": 1500.0})
    assert resp["headers"]["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Payload válido: 3 cenários na ordem esperada
# ---------------------------------------------------------------------------

def test_valid_payload_returns_three_scenarios():
    payload = {"debts": DEBTS, "monthly_budget": 1500.0}
    resp = _call(payload)
    assert "scenarios" in resp["body"]
    scenarios = resp["body"]["scenarios"]
    assert len(scenarios) == 3


def test_valid_payload_scenario_order():
    payload = {"debts": DEBTS, "monthly_budget": 1500.0}
    resp = _call(payload)
    ids = [s["scenario_id"] for s in resp["body"]["scenarios"]]
    assert ids == ["aggressive", "consolidation", "minimum_viable"]


def test_valid_payload_with_bb_cet():
    payload = {"debts": DEBTS, "monthly_budget": 1500.0, "bb_cet_annual": 12.0}
    resp = _call(payload)
    assert resp["body"]["scenarios"] == simulate(DEBTS, 1500.0, 12.0)
    assert resp["body"]["scenarios"] != simulate(DEBTS, 1500.0, None)


# ---------------------------------------------------------------------------
# bb_cet_annual ausente usa o default do engine
# ---------------------------------------------------------------------------

def test_missing_bb_cet_uses_engine_default():
    """Sem bb_cet_annual no payload, engine.simulate recebe None e usa DEFAULT_BB_CET_ANNUAL."""
    payload = {"debts": DEBTS, "monthly_budget": 1500.0}
    resp = _call(payload)
    assert resp["body"]["scenarios"] == simulate(DEBTS, 1500.0, DEFAULT_BB_CET_ANNUAL)


# ---------------------------------------------------------------------------
# Entradas inválidas: corpo com code INVALID_INPUT
# ---------------------------------------------------------------------------

def test_missing_debts_returns_invalid_input():
    resp = _call({"monthly_budget": 1500.0})
    assert resp["body"]["code"] == "INVALID_INPUT"


def test_monthly_budget_zero_returns_invalid_input():
    resp = _call({"debts": DEBTS, "monthly_budget": 0.0})
    assert resp["body"]["code"] == "INVALID_INPUT"


def test_monthly_budget_negative_returns_invalid_input():
    resp = _call({"debts": DEBTS, "monthly_budget": -100.0})
    assert resp["body"]["code"] == "INVALID_INPUT"


def test_invalid_input_has_message():
    resp = _call({"monthly_budget": 1500.0})
    assert "message" in resp["body"]
    assert resp["body"]["message"]  # não vazio
