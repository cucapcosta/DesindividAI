"""
Testes unitários offline para src/agents/explanation_guard.py (B4).

Cobre:
  - parse_brl_amounts: todos os formatos especificados
  - explanation_issues: caso real do defeito B4 (budget abaixo, LLM inventa economia)
  - explanation_issues: negativa "Não economiza nada" não dispara SAVINGS_CLAIM
  - explanation_issues: texto com valores corretos dos FATOS → sem issues
  - fallback_explanation: simple/technical, com e sem budget_below
    - sem issues (passa explanation_issues)
    - sem "economiz" quando budget_below=True
"""

from __future__ import annotations

import json

import pytest

from src.agents.explanation_guard import (
    explanation_issues,
    fallback_explanation,
    grounded_amounts,
    parse_brl_amounts,
)
from src.api.models import LanguageLevel, ScenarioId, SimulationScenario


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_facts(
    *,
    budget_below: bool = False,
    soma_dos_minimos: float | None = None,
    cenario_recomendado: str = "consolidation",
    cenarios: list[dict] | None = None,
    primeiros_passos: list[str] | None = None,
) -> str:
    if cenarios is None:
        cenarios = [
            {
                "id": "aggressive",
                "nome": "Agressivo",
                "descricao": "Paga tudo rápido",
                "prazo_meses": 48,
                "juros_totais": 105952.71,
                "economia_vs_pagar_so_o_minimo_disponivel": False,
            },
            {
                "id": "consolidation",
                "nome": "Consolidação",
                "descricao": "Unifica as dívidas",
                "prazo_meses": 60,
                "juros_totais": 56667.67,
                "economia_vs_pagar_so_o_minimo_disponivel": False,
            },
            {
                "id": "minimum_viable",
                "nome": "Mínimo viável",
                "descricao": "Paga o mínimo",
                "prazo_meses": 72,
                "juros_totais": 106004.36,
                "economia_vs_pagar_so_o_minimo_disponivel": False,
            },
        ]
    if primeiros_passos is None:
        primeiros_passos = ["Mês 1: pagar R$ 1.500,00 em itau (CREDIT_CARD)"]

    return json.dumps(
        {
            "nivel_de_linguagem": "simple",
            "cenario_recomendado": cenario_recomendado,
            "orcamento_abaixo_dos_minimos": budget_below,
            "soma_dos_minimos": soma_dos_minimos,
            "cenarios": cenarios,
            "primeiros_passos": primeiros_passos,
        },
        ensure_ascii=False,
    )


# Facts with positive savings for "consolidation" scenario
def _make_facts_with_savings() -> str:
    return json.dumps(
        {
            "nivel_de_linguagem": "simple",
            "cenario_recomendado": "consolidation",
            "orcamento_abaixo_dos_minimos": False,
            "soma_dos_minimos": 1200.0,
            "cenarios": [
                {
                    "id": "consolidation",
                    "nome": "Consolidação",
                    "descricao": "Unifica as dívidas",
                    "prazo_meses": 60,
                    "juros_totais": 56667.67,
                    "juros_se_pagar_so_o_minimo": 106004.36,
                    "economia_vs_pagar_so_o_minimo": 49336.69,
                },
            ],
            "primeiros_passos": ["Mês 1: pagar R$ 1.200,00 em bb (PERSONAL_LOAN)"],
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# parse_brl_amounts
# ---------------------------------------------------------------------------

class TestParseBrlAmounts:
    def test_milhar_ponto_decimal_virgula(self):
        result = parse_brl_amounts("R$ 49.335,04")
        assert abs(result[0] - 49335.04) < 0.001

    def test_milhar_ponto_sem_decimal(self):
        result = parse_brl_amounts("R$ 1.500")
        assert abs(result[0] - 1500.0) < 0.001

    def test_decimal_virgula_sem_milhar(self):
        result = parse_brl_amounts("R$ 594,43")
        assert abs(result[0] - 594.43) < 0.001

    def test_decimal_ponto_primeiros_passos(self):
        # Formato usado em primeiros_passos: "R$ 594.43"
        result = parse_brl_amounts("R$ 594.43")
        assert abs(result[0] - 594.43) < 0.001

    def test_sem_espaco_apos_rs(self):
        result = parse_brl_amounts("R$1.234,56")
        assert abs(result[0] - 1234.56) < 0.001

    def test_mil_suffix(self):
        result = parse_brl_amounts("R$ 50 mil")
        assert abs(result[0] - 50000.0) < 0.001

    def test_multiple_values(self):
        text = "Pague R$ 1.500,00 e economize R$ 594,43 por mês."
        result = parse_brl_amounts(text)
        assert len(result) == 2
        assert abs(result[0] - 1500.00) < 0.001
        assert abs(result[1] - 594.43) < 0.001

    def test_integer_value(self):
        result = parse_brl_amounts("R$ 1.500")
        assert abs(result[0] - 1500.0) < 0.001

    def test_large_milhar(self):
        result = parse_brl_amounts("R$ 105.952,71")
        assert abs(result[0] - 105952.71) < 0.001

    def test_no_value(self):
        result = parse_brl_amounts("Texto sem valores monetários.")
        assert result == []


    # ------------------------------------------------------------------
    # Parametrized: 5 previously-failing cases + 7 regression cases
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "text, expected",
        [
            # Previously failing (prefix-only match bug)
            ("R$ 1500,00",    1500.0),
            ("R$ 63914.50",   63914.5),
            ("R$ 1500",       1500.0),
            ("R$ 7494,75",    7494.75),
            ("R$ 12345",      12345.0),
            # Regression: must continue to work
            ("R$ 49.335,04",  49335.04),
            ("R$ 594.43",     594.43),
            ("R$ 50 mil",     50000.0),
            ("R$ 1.234.567,89", 1234567.89),
            ("R$1.234,56",    1234.56),
            # Trailing punctuation must not be captured
            ("R$ 1.500.",     1500.0),
            ("custa R$ 1.500, e algo", 1500.0),
        ],
    )
    def test_parametrized_formats(self, text: str, expected: float):
        result = parse_brl_amounts(text)
        assert len(result) == 1, f"Expected 1 match in {text!r}, got {result}"
        assert abs(result[0] - expected) < 0.001, (
            f"parse_brl_amounts({text!r}) = {result[0]}, expected {expected}"
        )


# ---------------------------------------------------------------------------
# grounded_amounts
# ---------------------------------------------------------------------------

class TestGroundedAmounts:
    def test_collects_numeric_fields(self):
        facts = _make_facts(soma_dos_minimos=1500.0)
        grounded = grounded_amounts(facts)
        assert 1500.0 in grounded
        assert 105952.71 in grounded
        assert 56667.67 in grounded
        assert 106004.36 in grounded

    def test_collects_brl_in_strings(self):
        facts = _make_facts(primeiros_passos=["Mês 1: pagar R$ 1.500,00 em itau"])
        grounded = grounded_amounts(facts)
        assert 1500.0 in grounded

    def test_empty_json(self):
        assert grounded_amounts("{}") == set()

    def test_invalid_json(self):
        assert grounded_amounts("not-json") == set()


# ---------------------------------------------------------------------------
# explanation_issues — caso real do defeito B4
# ---------------------------------------------------------------------------

class TestExplanationIssues:
    def test_real_defect_ungrounded_and_savings_below(self):
        """
        Caso real: LLM escreveu economia inventada quando budget está abaixo dos mínimos.
        O valor R$ 49.335,04 não está nos FATOS (a diferença real seria 49.336,69),
        e a afirmação de economia viola a regra budget_below.
        """
        facts_json = _make_facts(budget_below=True, soma_dos_minimos=1700.0)
        bad_text = (
            "O cenário de consolidação é o recomendado. "
            "A consolidação economiza R$ 49.335,04 em juros em comparação com "
            "pagar só os mínimos."
        )
        issues = explanation_issues(bad_text, facts_json, budget_below=True)
        assert "UNGROUNDED_AMOUNT" in issues
        assert "SAVINGS_CLAIM_BELOW_MINIMUMS" in issues

    def test_savings_claim_below_minimums_no_value(self):
        """Afirmação de economia sem valor R$ também dispara SAVINGS_CLAIM."""
        # Precisa ter o padrão: economiz* + R$ + dígito
        # Sem R$, só economiz não dispara
        facts_json = _make_facts(budget_below=True)
        text = "Este plano economiza muito dinheiro sem citar valores."
        issues = explanation_issues(text, facts_json, budget_below=True)
        # Sem R$ no padrão de savings, não deve disparar SAVINGS_CLAIM
        assert "SAVINGS_CLAIM_BELOW_MINIMUMS" not in issues

    def test_savings_claim_with_rs_triggers(self):
        """'economiza R$ X' com budget_below=True dispara SAVINGS_CLAIM."""
        facts_json = _make_facts(budget_below=True, soma_dos_minimos=1700.0)
        text = "Este plano economiza R$ 1.500,00 em juros."
        issues = explanation_issues(text, facts_json, budget_below=True)
        assert "SAVINGS_CLAIM_BELOW_MINIMUMS" in issues

    def test_negativa_nao_economiza_nada_nao_dispara(self):
        """'Não economiza nada' com budget_below=True NÃO deve disparar SAVINGS_CLAIM."""
        facts_json = _make_facts(budget_below=True)
        text = "Não economiza nada com este orçamento. Renegocie as dívidas."
        issues = explanation_issues(text, facts_json, budget_below=True)
        assert "SAVINGS_CLAIM_BELOW_MINIMUMS" not in issues

    def test_nao_economiza_variante(self):
        """'nao economiza R$ nada' — sem dígito após R$ não dispara."""
        facts_json = _make_facts(budget_below=True)
        text = "Você não economiza R$ nada aqui."
        issues = explanation_issues(text, facts_json, budget_below=True)
        assert "SAVINGS_CLAIM_BELOW_MINIMUMS" not in issues

    def test_grounded_values_no_issues(self):
        """Texto que cita apenas juros_totais e primeiros_passos corretos → sem issues."""
        facts_json = _make_facts(
            budget_below=False,
            soma_dos_minimos=1700.0,
            cenarios=[
                {
                    "id": "consolidation",
                    "nome": "Consolidação",
                    "descricao": "desc",
                    "prazo_meses": 60,
                    "juros_totais": 56667.67,
                    "economia_vs_pagar_so_o_minimo_disponivel": False,
                }
            ],
            primeiros_passos=["Mês 1: pagar R$ 1.700,00 em bb"],
        )
        # Cita juros_totais (56667.67) e primeiros_passos (1700.0) — ambos fundamentados
        text = (
            "O cenário consolidação tem juros totais de R$ 56.667,67. "
            "Primeiros passos: pagar R$ 1.700,00 no primeiro mês."
        )
        issues = explanation_issues(text, facts_json, budget_below=False)
        assert issues == []

    def test_no_budget_below_savings_ok(self):
        """Economia mencionada com budget_below=False e valor fundamentado → sem issues."""
        facts_json = _make_facts_with_savings()
        text = (
            "A consolidação tem juros totais de R$ 56.667,67 e "
            "economiza R$ 49.336,69 em comparação com pagar só o mínimo."
        )
        issues = explanation_issues(text, facts_json, budget_below=False)
        assert issues == []

    def test_ungrounded_amount_only(self):
        """Valor inventado sem afirmação de economia → só UNGROUNDED_AMOUNT."""
        facts_json = _make_facts(budget_below=False, soma_dos_minimos=1700.0)
        text = "O cenário custa R$ 99.999,99 em juros totais."
        issues = explanation_issues(text, facts_json, budget_below=False)
        assert "UNGROUNDED_AMOUNT" in issues
        assert "SAVINGS_CLAIM_BELOW_MINIMUMS" not in issues

    def test_empty_text_no_issues(self):
        facts_json = _make_facts()
        issues = explanation_issues("", facts_json, budget_below=False)
        assert issues == []

    def test_tolerance_allows_rounding(self):
        """Valor dentro de ±1,00 do fundamentado → sem UNGROUNDED_AMOUNT."""
        facts_json = _make_facts(soma_dos_minimos=1700.0)
        # 56667.67 ± 1.0 → 56668.00 deve passar
        text = "Juros de R$ 56.668,00 no total."
        issues = explanation_issues(text, facts_json, budget_below=False)
        assert "UNGROUNDED_AMOUNT" not in issues


# ---------------------------------------------------------------------------
# fallback_explanation
# ---------------------------------------------------------------------------

class TestFallbackExplanation:
    def test_simple_no_budget_below_no_issues(self):
        facts_json = _make_facts_with_savings()
        result = fallback_explanation(facts_json, LanguageLevel.SIMPLE)
        assert isinstance(result, str)
        assert len(result) > 0
        issues = explanation_issues(result, facts_json, budget_below=False)
        assert issues == [], f"fallback tem issues: {issues}\ntexto: {result}"

    def test_technical_no_budget_below_no_issues(self):
        facts_json = _make_facts_with_savings()
        result = fallback_explanation(facts_json, LanguageLevel.TECHNICAL)
        assert isinstance(result, str)
        issues = explanation_issues(result, facts_json, budget_below=False)
        assert issues == [], f"fallback tem issues: {issues}\ntexto: {result}"

    def test_simple_with_budget_below_no_issues(self):
        facts_json = _make_facts(budget_below=True, soma_dos_minimos=1700.0)
        result = fallback_explanation(facts_json, LanguageLevel.SIMPLE)
        issues = explanation_issues(result, facts_json, budget_below=True)
        assert issues == [], f"fallback tem issues: {issues}\ntexto: {result}"

    def test_technical_with_budget_below_no_issues(self):
        facts_json = _make_facts(budget_below=True, soma_dos_minimos=1700.0)
        result = fallback_explanation(facts_json, LanguageLevel.TECHNICAL)
        issues = explanation_issues(result, facts_json, budget_below=True)
        assert issues == [], f"fallback tem issues: {issues}\ntexto: {result}"

    def test_budget_below_sem_economiz(self):
        """Fallback com budget_below=True não deve usar a palavra 'economiz'."""
        facts_json = _make_facts(budget_below=True, soma_dos_minimos=1700.0)
        result = fallback_explanation(facts_json, LanguageLevel.SIMPLE)
        import re
        assert not re.search(r"\beconomiz", result, re.IGNORECASE), (
            f"Fallback não deveria mencionar economia quando budget_below=True.\nTexto: {result}"
        )

    def test_budget_below_technical_sem_economiz(self):
        facts_json = _make_facts(budget_below=True, soma_dos_minimos=1700.0)
        result = fallback_explanation(facts_json, LanguageLevel.TECHNICAL)
        import re
        assert not re.search(r"\beconomiz", result, re.IGNORECASE), (
            f"Fallback técnico não deveria mencionar economia quando budget_below=True.\nTexto: {result}"
        )

    def test_word_count_below_180(self):
        """Fallback deve ter ≤ 180 palavras."""
        facts_json = _make_facts_with_savings()
        result = fallback_explanation(facts_json, LanguageLevel.SIMPLE)
        word_count = len(result.split())
        assert word_count <= 180, f"Fallback tem {word_count} palavras (limite: 180).\nTexto: {result}"

    def test_contains_scenario_name(self):
        facts_json = _make_facts_with_savings()
        result = fallback_explanation(facts_json, LanguageLevel.SIMPLE)
        assert "Consolidação" in result

    def test_consolidation_warns_approval(self):
        """Fallback para cenário consolidation deve avisar sobre aprovação de crédito."""
        facts_json = _make_facts_with_savings()
        result = fallback_explanation(facts_json, LanguageLevel.SIMPLE)
        assert "aprovação de crédito" in result.lower() or "aprovação" in result.lower()

    def test_budget_below_mentions_renegociation(self):
        """Fallback com budget_below deve mencionar renegociação."""
        facts_json = _make_facts(budget_below=True, soma_dos_minimos=1700.0)
        result = fallback_explanation(facts_json, LanguageLevel.SIMPLE)
        assert "renegoci" in result.lower()

    def test_invalid_json_returns_fallback_message(self):
        result = fallback_explanation("not-json", LanguageLevel.SIMPLE)
        assert "Não foi possível" in result or len(result) > 0
