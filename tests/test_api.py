from uuid import uuid4

from tests.conftest import SNAPSHOT_ID


def simulate(client, budget=1500):
    return client.post("/simulation", json={"snapshot_id": SNAPSHOT_ID, "monthly_budget": budget})


def test_simulation_ok_and_persisted(client, store):
    resp = simulate(client)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["scenarios"]) == 3
    import asyncio
    assert asyncio.run(store.get("financial_snapshots", f"simulation:{body['simulation_id']}"))


def test_simulation_unknown_snapshot_404(client):
    resp = client.post("/simulation", json={"snapshot_id": str(uuid4()), "monthly_budget": 100})
    assert resp.status_code == 404
    assert resp.json()["code"] == "SNAPSHOT_NOT_FOUND"


def test_simulation_zero_budget_400(client):
    resp = simulate(client, 0)
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_INPUT"


def test_consent_enforced_when_enabled(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_CONSENT", "true")
    assert simulate(client).status_code == 403


def test_recommendation_ok(client):
    sim = simulate(client).json()
    resp = client.post("/recommendation", json={"simulation_id": sim["simulation_id"], "language_level": "simple"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["explanation"] == "Explicação de teste."
    assert body["trace_id"].startswith("trace-")
    assert body["recommended_scenario"] in {"aggressive", "consolidation", "minimum_viable"}
    assert len(body["scenarios"]) == 3


def test_recommendation_unknown_simulation_404(client):
    resp = client.post("/recommendation", json={"simulation_id": str(uuid4())})
    assert resp.status_code == 404


def test_recommendation_trace_has_prompt_and_output(client, store):
    import asyncio
    sim = simulate(client).json()
    body = client.post("/recommendation", json={"simulation_id": sim["simulation_id"]}).json()
    trace = asyncio.run(store.get("financial_snapshots", body["trace_id"]))
    assert trace["output"] == "Explicação de teste."
    assert "FATOS" in trace["prompt"] and "Mês 1" in trace["prompt"]
    assert "cpf" not in trace["prompt"].lower()


def test_recommendation_without_llm_config_503(client, monkeypatch):
    monkeypatch.delenv("WATSONX_URL", raising=False)
    from src.agents.recommendation_agent import build_llm
    from src.main import app
    app.dependency_overrides.pop(build_llm)
    resp = client.post("/recommendation", json={"simulation_id": str(uuid4())})
    assert resp.status_code == 503
