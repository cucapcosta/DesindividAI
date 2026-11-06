"""Caso de uso de POST /recommendation: simulação armazenada -> Agente 2 -> trace de governança."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from langchain_core.runnables import Runnable

from src.agents.recommendation_agent import build_chain
from src.api.models import LanguageLevel, RecommendationResponse, ScenarioId, SimulationScenario
from src.core.errors import ServiceError
from src.services.governance import record_trace
from src.services.simulation_service import decisions_db, ensure_consent, load_snapshot, now_iso
from src.simulation.engine import CONSOLIDATED_DEBT_ID, minimum_payments_total
from src.storage.store import DocumentStore


def _safe_text(valor, limite: int = 40) -> str:
    texto = str(valor)
    texto = re.sub(r"[\x00-\x1f\x7f]", " ", texto)
    texto = texto.strip()
    return texto[:limite]


def _first_steps(scenarios: list[SimulationScenario], debts: list[dict]):
    names = {
        str(d["debt_id"]): f'{_safe_text(d["institution"])} ({_safe_text(d["product_type"])})'
        for d in debts
    }
    names[CONSOLIDATED_DEBT_ID] = "nova linha de consolidação BB"

    def steps(scenario_id: ScenarioId) -> list[str]:
        scenario = next(s for s in scenarios if s.scenario_id == scenario_id)
        if not scenario.monthly_plan:
            return []
        return [
            f"Mês 1: pagar R$ {p.amount:.2f} em {names.get(str(p.debt_id), 'dívida')}"
            for p in scenario.monthly_plan[0].payments
        ]

    return steps


async def create_recommendation(
    store: DocumentStore, llm: Runnable, simulation_id: UUID, language_level: LanguageLevel
) -> RecommendationResponse:
    sim = await store.get(decisions_db(), f"simulation:{simulation_id}")
    if not sim:
        raise ServiceError(404, "SIMULATION_NOT_FOUND", "Simulação não encontrada.")
    await ensure_consent(
        store, consent_id=sim.get("consent_id"),
        pseudonym_id=sim.get("pseudonym_id", ""), scope="recommendation:read",
    )
    scenarios = [SimulationScenario(**s) for s in sim["scenarios"]]

    try:
        debts = (await load_snapshot(store, UUID(sim["snapshot_id"])))["debts"]
    except ServiceError:
        debts = []  # sem snapshot ainda dá para explicar; só ficam sem "primeiros passos"

    # Calcular flag de orçamento abaixo dos mínimos (Fix 1c)
    monthly_budget = sim.get("monthly_budget")
    if debts and monthly_budget is not None:
        min_total: float | None = minimum_payments_total(debts)
        budget_below = monthly_budget < min_total - 0.005  # EPS
    else:
        # fallback: qualquer cenário com economia negativa indica orçamento insuficiente
        min_total = None
        budget_below = any(s.total_savings_vs_minimum < 0 for s in scenarios)

    try:
        result = await build_chain(llm).ainvoke(
            {
                "scenarios": scenarios,
                "language_level": language_level,
                "first_steps": _first_steps(scenarios, debts),
                "orcamento_abaixo_dos_minimos": budget_below,
                "soma_dos_minimos": min_total,
            }
        )
    except Exception as exc:  # falha do provedor LLM
        raise ServiceError(503, "SERVICE_UNAVAILABLE", "Modelo de linguagem indisponível.") from exc

    recommendation_id = uuid4()
    trace_id = await record_trace(
        store,
        recommendation_id=str(recommendation_id),
        simulation_id=str(simulation_id),
        snapshot_id=sim["snapshot_id"],
        pseudonym_id=sim.get("pseudonym_id"),
        language_level=language_level.value,
        recommended_scenario=result["recommended"].value,
        prompt=result["prompt_text"],
        output=result["explanation"],
        expires_at=sim.get("expires_at"),
        consent_id=sim.get("consent_id"),
    )
    return RecommendationResponse(
        recommendation_id=recommendation_id,
        recommended_scenario=result["recommended"],
        explanation=result["explanation"].strip(),
        scenarios=scenarios,
        generated_at=now_iso(),
        trace_id=trace_id,
    )
