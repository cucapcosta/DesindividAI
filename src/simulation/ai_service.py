"""
Empacotamento do Motor de Simulação como AI Service do watsonx.ai.

Deploy (resumo; detalhes em docs/m3-simulation-engine.md):
    1. Empacotar engine.py como package extension (ou colar a função e o engine num único arquivo).
    2. client.repository.AIServices.store(...) com `deployable_ai_service`.
    3. client.deployments.create(...) -> endpoint REST em WATSONX_SIMULATION_URL.

Contrato:
    POST <WATSONX_SIMULATION_URL>/predict
    body:     {"debts": [Debt...], "monthly_budget": float, "bb_cet_annual": float?}
    resposta: {"scenarios": [SimulationScenario x3]}
"""

from src.simulation.engine import simulate


def deployable_ai_service(context, **custom):
    def generate(context):
        payload = context.get_json()
        try:
            scenarios = simulate(
                payload["debts"], float(payload["monthly_budget"]), payload.get("bb_cet_annual")
            )
        except (KeyError, ValueError) as exc:
            return {"headers": {"Content-Type": "application/json"},
                    "body": {"code": "INVALID_INPUT", "message": str(exc)}}
        return {"headers": {"Content-Type": "application/json"}, "body": {"scenarios": scenarios}}

    return generate
