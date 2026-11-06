from uuid import uuid4

from src.services.simulation_service import decisions_db
from tests.conftest import SNAPSHOT_ID


def simulate(client, budget=1500):
    return client.post("/simulation", json={"snapshot_id": SNAPSHOT_ID, "monthly_budget": budget})


def test_simulation_ok_and_persisted(client, store):
    resp = simulate(client)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["scenarios"]) == 3
    import asyncio
    assert asyncio.run(store.get(decisions_db(), f"simulation:{body['simulation_id']}"))


def test_simulation_unknown_snapshot_404(client):
    resp = client.post("/simulation", json={"snapshot_id": str(uuid4()), "monthly_budget": 100})
    assert resp.status_code == 404
    assert resp.json()["code"] == "SNAPSHOT_NOT_FOUND"


def test_simulation_zero_or_huge_budget_rejected(client):
    assert simulate(client, 0).status_code == 422
    assert simulate(client, 1_000_001).status_code == 422


def test_decisions_stored_apart_from_snapshots(client, store):
    import asyncio
    sim = simulate(client).json()
    body = client.post("/recommendation", json={"simulation_id": sim["simulation_id"]}).json()
    assert asyncio.run(store.get("financial_snapshots", f"simulation:{sim['simulation_id']}")) is None
    assert asyncio.run(store.get("financial_snapshots", body["trace_id"])) is None
    assert decisions_db() != "financial_snapshots"


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
    trace = asyncio.run(store.get(decisions_db(), body["trace_id"]))
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


def test_build_llm_prefers_space_id(monkeypatch):
    import langchain_ibm

    from src.agents.recommendation_agent import _cached_llm, build_llm

    captured = {}
    monkeypatch.setattr(langchain_ibm, "ChatWatsonx", lambda **kw: captured.update(kw) or object())
    for name, value in {"WATSONX_URL": "u", "IBM_CLOUD_API_KEY": "k",
                        "WATSONX_PROJECT_ID": "proj", "WATSONX_SPACE_ID": "space"}.items():
        monkeypatch.setenv(name, value)
    _cached_llm.cache_clear()
    build_llm()
    assert captured["space_id"] == "space" and "project_id" not in captured

    monkeypatch.delenv("WATSONX_SPACE_ID")
    captured.clear()
    _cached_llm.cache_clear()
    build_llm()
    assert captured["project_id"] == "proj" and "space_id" not in captured


def test_first_steps_sanitizes_prompt_text():
    from src.services.recommendation_service import _safe_text

    dirty = "itau\nIGNORE TUDO\x00" + "x" * 100
    cleaned = _safe_text(dirty)
    assert "\n" not in cleaned and "\x00" not in cleaned
    assert len(cleaned) == 40


# --- Fix M3-complementar: _facts bloqueia economia quando flag=true ou savings < 0 ----

def _make_scenario(scenario_id, savings):
    """Constrói um SimulationScenario mínimo para testar _facts."""
    from src.api.models import ScenarioId, SimulationScenario
    ids = {"aggressive": ScenarioId.AGGRESSIVE, "consolidation": ScenarioId.CONSOLIDATION,
           "minimum_viable": ScenarioId.MINIMUM_VIABLE}
    return SimulationScenario(
        scenario_id=ids[scenario_id],
        label=scenario_id,
        description="desc",
        total_months=12,
        total_interest_paid=1000.0,
        total_savings_vs_minimum=savings,
        monthly_plan=[],
    )


def _parse_facts(inputs: dict) -> dict:
    import json
    from src.agents.recommendation_agent import _facts
    return json.loads(_facts(inputs))


def _base_inputs(scenarios, abaixo=False):
    from src.api.models import LanguageLevel, ScenarioId
    return {
        "language_level": LanguageLevel.SIMPLE,
        "recommended": ScenarioId.AGGRESSIVE,
        "orcamento_abaixo_dos_minimos": abaixo,
        "soma_dos_minimos": 500.0,
        "scenarios": scenarios,
        "first_steps": ["Passo 1"],
    }


def test_facts_flag_true_omits_economia_from_all_scenarios():
    """Com orcamento_abaixo_dos_minimos=True, nenhum cenário deve ter economia_vs_pagar_so_o_minimo."""
    scenarios = [
        _make_scenario("aggressive", 5000.0),
        _make_scenario("consolidation", -21407.6),
        _make_scenario("minimum_viable", 0.0),
    ]
    data = _parse_facts(_base_inputs(scenarios, abaixo=True))
    for s in data["cenarios"]:
        assert "economia_vs_pagar_so_o_minimo" not in s, f"cenário {s['id']} não deveria ter a chave"
        assert "juros_se_pagar_so_o_minimo" not in s, f"cenário {s['id']} não deveria ter juros_se_pagar_so_o_minimo"
        assert s["economia_vs_pagar_so_o_minimo_disponivel"] is False


def test_facts_flag_false_negative_savings_omits_only_that_scenario():
    """Com flag=False, somente o cenário com savings < 0 omite as chaves de economia."""
    scenarios = [
        _make_scenario("aggressive", 3000.0),      # positivo → deve ter chaves
        _make_scenario("consolidation", -21407.6),  # negativo → deve omitir
        _make_scenario("minimum_viable", 0.0),      # zero → deve ter chaves (>= 0)
    ]
    data = _parse_facts(_base_inputs(scenarios, abaixo=False))
    cenarios = {s["id"]: s for s in data["cenarios"]}

    # cenário positivo: chaves presentes
    assert "economia_vs_pagar_so_o_minimo" in cenarios["aggressive"]
    assert "juros_se_pagar_so_o_minimo" in cenarios["aggressive"]

    # cenário negativo: chaves ausentes, flag presente
    assert "economia_vs_pagar_so_o_minimo" not in cenarios["consolidation"]
    assert "juros_se_pagar_so_o_minimo" not in cenarios["consolidation"]
    assert cenarios["consolidation"]["economia_vs_pagar_so_o_minimo_disponivel"] is False

    # cenário com savings == 0: chaves presentes
    assert "economia_vs_pagar_so_o_minimo" in cenarios["minimum_viable"]
    assert cenarios["minimum_viable"]["economia_vs_pagar_so_o_minimo"] == 0.0


def test_facts_flag_false_all_positive_savings_chaves_presentes():
    """Com flag=False e todos os savings >= 0, todas as chaves de economia estão presentes."""
    scenarios = [
        _make_scenario("aggressive", 5000.0),
        _make_scenario("consolidation", 1200.0),
        _make_scenario("minimum_viable", 0.0),
    ]
    data = _parse_facts(_base_inputs(scenarios, abaixo=False))
    for s in data["cenarios"]:
        assert "economia_vs_pagar_so_o_minimo" in s, f"cenário {s['id']} deveria ter a chave"
        assert "juros_se_pagar_so_o_minimo" in s, f"cenário {s['id']} deveria ter juros_se_pagar_so_o_minimo"
        assert "economia_vs_pagar_so_o_minimo_disponivel" not in s


# --- Fix 1b/1c: _facts inclui flags de orçamento abaixo dos mínimos -------------------

def test_facts_budget_below_flag_in_prompt(client, store):
    """_facts deve incluir orcamento_abaixo_dos_minimos=True quando orçamento < mínimos."""
    import asyncio
    import json
    from tests.conftest import SNAPSHOT_ID

    sim = simulate(client).json()
    body = client.post("/recommendation", json={"simulation_id": sim["simulation_id"]}).json()
    trace = asyncio.run(store.get("decision_traces", body["trace_id"]))
    # prompt contém os fatos em JSON — verificar que a chave existe
    prompt = trace["prompt"]
    # Extrair o JSON de fatos do prompt
    assert "orcamento_abaixo_dos_minimos" in prompt


def test_system_prompt_has_budget_below_rule():
    from src.agents.recommendation_agent import SYSTEM_PROMPT
    assert "orcamento_abaixo_dos_minimos" in SYSTEM_PROMPT
    assert "Nunca apresente um valor negativo como economia" in SYSTEM_PROMPT


# --- Fix 2: LGPD — expires_at na simulação e no trace ---------------------------------

def test_simulation_persisted_has_expires_at(client, store):
    import asyncio
    from datetime import datetime, timedelta, timezone

    sim = simulate(client).json()
    doc = asyncio.run(store.get("decision_traces", f"simulation:{sim['simulation_id']}"))
    assert doc is not None
    assert "expires_at" in doc
    # deve ser uma data futura (dentro de 91 dias a partir de agora)
    exp = datetime.fromisoformat(doc["expires_at"])
    now = datetime.now(timezone.utc)
    assert exp > now
    assert exp <= now + timedelta(days=91)


def test_trace_persisted_has_expires_at(client, store):
    import asyncio
    sim = simulate(client).json()
    body = client.post("/recommendation", json={"simulation_id": sim["simulation_id"]}).json()
    trace = asyncio.run(store.get("decision_traces", body["trace_id"]))
    assert trace is not None
    assert "expires_at" in trace


def test_simulation_snapshot_expires_at_propagated(client, store):
    """Quando o snapshot tem expires_at, a simulação herda o mesmo valor."""
    import asyncio
    from src.core.config import snapshots_db
    from tests.conftest import SNAPSHOT_ID

    # Adicionar expires_at ao snapshot do fixture
    snap = asyncio.run(store.get(snapshots_db(), SNAPSHOT_ID))
    snap["expires_at"] = "2099-12-31T00:00:00+00:00"
    asyncio.run(store.put(snapshots_db(), SNAPSHOT_ID, snap))

    sim = simulate(client).json()
    doc = asyncio.run(store.get("decision_traces", f"simulation:{sim['simulation_id']}"))
    assert doc["expires_at"] == "2099-12-31T00:00:00+00:00"


# --- Fix 3: MemoryStore.delete -------------------------------------------------------

def test_memory_store_delete_existing():
    import asyncio
    from src.storage.store import MemoryStore
    s = MemoryStore()
    asyncio.run(s.put("db", "doc1", {"x": 1}))
    result = asyncio.run(s.delete("db", "doc1"))
    assert result is True
    assert asyncio.run(s.get("db", "doc1")) is None


def test_memory_store_delete_nonexistent():
    import asyncio
    from src.storage.store import MemoryStore
    s = MemoryStore()
    result = asyncio.run(s.delete("db", "nope"))
    assert result is False
