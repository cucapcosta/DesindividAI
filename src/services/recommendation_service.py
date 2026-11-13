"""Caso de uso de POST /recommendation: simulação armazenada -> Agente 2 -> trace de governança."""

from __future__ import annotations

import logging
import re
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from langchain_core.runnables import Runnable

from src.agents.explanation_guard import explanation_issues, fallback_explanation, normalize_brl
from src.agents.recommendation_agent import _CORRECTIVE_INSTRUCTIONS, build_chain, scenario_budget_below
from src.api.models import (
    LanguageLevel,
    RecommendationResponse,
    ScenarioId,
    SimulationScenario,
    product_label_pt,
)
from src.core.errors import ServiceError
from src.services.governance import record_trace
from src.services.simulation_service import decisions_db, ensure_consent, load_snapshot, now_iso
from src.simulation.engine import CONSOLIDATED_DEBT_ID, format_brl, minimum_payments_total
from src.storage.store import DocumentStore

logger = logging.getLogger(__name__)


def _safe_text(valor, limite: int = 40) -> str:
    texto = str(valor)
    texto = re.sub(r"[\x00-\x1f\x7f]", " ", texto)
    texto = texto.strip()
    return texto[:limite]


def _first_steps(scenarios: list[SimulationScenario], debts: list[dict]):
    names = {
        str(d["debt_id"]): f'{_safe_text(d["institution"])} ({product_label_pt(d["product_type"])})'
        for d in debts
    }
    names[CONSOLIDATED_DEBT_ID] = "nova linha de consolidação BB"

    def steps(scenario_id: ScenarioId) -> list[str]:
        scenario = next(s for s in scenarios if s.scenario_id == scenario_id)
        if not scenario.monthly_plan:
            return []
        return [
            f"Mês 1: pagar {format_brl(p.amount)} em {names.get(str(p.debt_id), 'dívida')}"
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
        _mt = minimum_payments_total(debts)
        min_total: float | None = float(
            Decimal(str(_mt)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ) if _mt is not None else None
        budget_below = monthly_budget < (min_total or 0) - 0.005  # EPS
    else:
        # fallback: qualquer cenário com economia negativa indica orçamento insuficiente
        min_total = None
        budget_below = any(s.total_savings_vs_minimum < 0 for s in scenarios)

    chain_inputs = {
        "scenarios": scenarios,
        "language_level": language_level,
        "first_steps": _first_steps(scenarios, debts),
        "orcamento_abaixo_dos_minimos": budget_below,
        "soma_dos_minimos": min_total,
    }

    try:
        result = await build_chain(llm).ainvoke(chain_inputs)
    except Exception as exc:  # falha do provedor LLM
        raise ServiceError(503, "SERVICE_UNAVAILABLE", "Modelo de linguagem indisponível.") from exc
    result["explanation"] = normalize_brl(result["explanation"])

    # A guarda de "economia" vale para o cenário recomendado, que é o que o texto defende;
    # os outros cenários têm o próprio aviso nos FATOS.
    rec_scenario = next(s for s in scenarios if s.scenario_id == result["recommended"])
    guard_budget_below = scenario_budget_below(rec_scenario, budget_below)

    issues_1st = explanation_issues(
        result["explanation"],
        result["facts_json"],
        budget_below=guard_budget_below,
    )

    explanation_prompt = result["prompt_text"]
    retry_error: str | None = None

    if issues_1st:
        logger.warning(
            "explanation_guard issues on 1st attempt: %s simulation_id=%s",
            issues_1st,
            simulation_id,
        )
        # Construir instrução corretiva a partir dos códigos de issue
        corrective_parts = [
            _CORRECTIVE_INSTRUCTIONS[code]
            for code in issues_1st
            if code in _CORRECTIVE_INSTRUCTIONS
        ]
        correcao = " ".join(corrective_parts) if corrective_parts else None

        retry_inputs = {**chain_inputs, "correcao": correcao} if correcao else chain_inputs

        # Retry once with corrective instruction appended to prompt
        try:
            result2 = await build_chain(llm).ainvoke(retry_inputs)
            result2["explanation"] = normalize_brl(result2["explanation"])
            issues_2nd = explanation_issues(
                result2["explanation"],
                result2["facts_json"],
                budget_below=guard_budget_below,
            )
            if not issues_2nd:
                final_explanation = result2["explanation"]
                source = "llm_retry"
                explanation_prompt = result2["prompt_text"]
            else:
                final_explanation = fallback_explanation(result["facts_json"], language_level)
                source = "fallback"
        except Exception as exc:
            # provider failure on retry → fallback + log warning
            logger.warning(
                "explanation_guard retry failed: exc_type=%s simulation_id=%s",
                type(exc).__name__,
                simulation_id,
            )
            final_explanation = fallback_explanation(result["facts_json"], language_level)
            source = "fallback"
            retry_error = type(exc).__name__
    else:
        final_explanation = result["explanation"]
        source = "llm"

    recommendation_id = uuid4()
    trace_extra: dict = {}
    if retry_error is not None:
        trace_extra["retry_error"] = retry_error

    trace_id = await record_trace(
        store,
        recommendation_id=str(recommendation_id),
        simulation_id=str(simulation_id),
        snapshot_id=sim["snapshot_id"],
        pseudonym_id=sim.get("pseudonym_id"),
        language_level=language_level.value,
        recommended_scenario=result["recommended"].value,
        prompt=explanation_prompt,
        output=final_explanation,
        expires_at=sim.get("expires_at"),
        consent_id=sim.get("consent_id"),
        explanation_source=source,
        guard_issues=issues_1st,
        **trace_extra,
    )
    return RecommendationResponse(
        recommendation_id=recommendation_id,
        recommended_scenario=result["recommended"],
        explanation=final_explanation.strip(),
        scenarios=scenarios,
        generated_at=now_iso(),
        trace_id=trace_id,
    )
