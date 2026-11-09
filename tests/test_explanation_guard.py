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
        # Formato real de _first_steps (src/services/recommendation_service.py):
        # f"Mês 1: pagar R$ {p.amount:.2f} em <institution> (<produto em português>)"
        primeiros_passos = ["Mês 1: pagar R$ 1500.00 em itau (cartão de crédito)"]

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
            "primeiros_passos": ["Mês 1: pagar R$ 1200.00 em bb (empréstimo pessoal)"],
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


# ---------------------------------------------------------------------------
# _fmt_brl — R-10 parametrized
# ---------------------------------------------------------------------------

from src.agents.explanation_guard import _fmt_brl  # noqa: E402


@pytest.mark.parametrize(
    "value, expected",
    [
        (1999.995,     "R$ 2.000,00"),
        (1999.994,     "R$ 1.999,99"),
        (-12.5,        "-R$ 12,50"),
        (0,            "R$ 0,00"),
        (1234567.891,  "R$ 1.234.567,89"),
        (0.005,        "R$ 0,01"),
        (594.43,       "R$ 594,43"),
    ],
)
def test_fmt_brl_parametrized(value: float, expected: str) -> None:
    assert _fmt_brl(value) == expected, (
        f"_fmt_brl({value!r}) = {_fmt_brl(value)!r}, expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# R-22(d): parse_brl_amounts — sinal negativo e milhão/milhões
# ---------------------------------------------------------------------------

class TestParseBrlAmountsR22d:
    def test_negative_prefix(self):
        assert abs(parse_brl_amounts("-R$ 1.234,56")[0] - (-1234.56)) < 0.001

    def test_negative_after_symbol(self):
        assert abs(parse_brl_amounts("R$ -1.234,56")[0] - (-1234.56)) < 0.001

    def test_milhao(self):
        result = parse_brl_amounts("R$ 1,2 milhão")
        assert len(result) == 1
        assert abs(result[0] - 1_200_000.0) < 0.001

    def test_milhoes(self):
        result = parse_brl_amounts("R$ 2 milhões")
        assert len(result) == 1
        assert abs(result[0] - 2_000_000.0) < 0.001

    def test_milh_tilde(self):
        result = parse_brl_amounts("R$ 1 milhão")
        assert len(result) == 1
        assert abs(result[0] - 1_000_000.0) < 0.001


# ---------------------------------------------------------------------------
# R-22(a): _SAVINGS_RE — negações ampliadas
# ---------------------------------------------------------------------------

from src.agents.explanation_guard import _is_savings_claim  # noqa: E402


class TestSavingsClaimR22a:
    def _facts_below(self):
        return _make_facts(budget_below=True, soma_dos_minimos=2345.67)

    # ---- Negações que NÃO devem ser issue ----
    @pytest.mark.parametrize("text", [
        "não haverá economia; a soma dos mínimos é R$ 2.345,67",
        "Não há economia possível com R$ 2.345,67.",
        "não terá economia com este plano de R$ 2.345,67.",
        "não vai economizar R$ 2.345,67 neste cenário.",
        "não gera economia de R$ 2.345,67.",
        "sem economia; o mínimo é R$ 2.345,67.",
        "nenhuma economia; pague R$ 2.345,67.",
        "Nao economiza R$ 2.345,67.",
        "Não economiza nada com R$ 2.345,67.",
    ])
    def test_negacao_nao_e_issue(self, text):
        facts_json = self._facts_below()
        issues = explanation_issues(text, facts_json, budget_below=True)
        assert "SAVINGS_CLAIM_BELOW_MINIMUMS" not in issues, (
            f"Frase negada não deveria disparar SAVINGS_CLAIM: {text!r}"
        )

    # ---- Afirmações positivas que SÃO issue ----
    @pytest.mark.parametrize("text", [
        "economiza R$ 5.000 em juros.",
        "você vai economizar R$ 3.000 com este plano.",
        "a economia é R$ 2.345,67.",
    ])
    def test_afirmacao_positiva_e_issue(self, text):
        facts_json = _make_facts(budget_below=True, soma_dos_minimos=500.0)
        issues = explanation_issues(text, facts_json, budget_below=True)
        assert "SAVINGS_CLAIM_BELOW_MINIMUMS" in issues, (
            f"Afirmação positiva deveria disparar SAVINGS_CLAIM: {text!r}"
        )


# ---------------------------------------------------------------------------
# R-22(b): tolerância adaptativa para valores arredondados
# ---------------------------------------------------------------------------

class TestToleranceR22b:
    def test_mil_suffix_rounds_accepted(self):
        """R$ 57 mil (57000) deve ser ACEITO quando o fato é 56.667,67.
        Ambos arredondam para o milhar 57.000, então a tolerância por arredondamento aceita."""
        facts_json = _make_facts(
            cenarios=[{
                "id": "consolidation", "nome": "C", "descricao": "d",
                "prazo_meses": 60, "juros_totais": 56667.67,
                "economia_vs_pagar_so_o_minimo_disponivel": False,
            }]
        )
        # round(57000/1000)*1000 = 57000; round(56667.67/1000)*1000 = 57000 → match → ACEITO
        text = "custa cerca de R$ 57 mil em juros"
        issues = explanation_issues(text, facts_json, budget_below=False)
        assert "UNGROUNDED_AMOUNT" not in issues

    def test_60_mil_ungrounded_for_56667(self):
        """R$ 60 mil (60000) deve ser UNGROUNDED quando o fato é 56.667,67.
        round(60000/1000)*1000=60000; round(56667.67/1000)*1000=57000 → não match → UNGROUNDED."""
        facts_json = _make_facts(
            cenarios=[{
                "id": "consolidation", "nome": "C", "descricao": "d",
                "prazo_meses": 60, "juros_totais": 56667.67,
                "economia_vs_pagar_so_o_minimo_disponivel": False,
            }]
        )
        text = "Juros de R$ 60 mil."
        issues = explanation_issues(text, facts_json, budget_below=False)
        assert "UNGROUNDED_AMOUNT" in issues

    def test_multiple_of_1000_wide_tolerance(self):
        """R$ 57.000 aceito quando fato é 56.700 (diferença 300 < 0.5% de 57000=285? não.
        Mas 57000 com fato 57100 → diff=100 < 285 → OK."""
        facts_json = _make_facts(
            cenarios=[{
                "id": "consolidation", "nome": "C", "descricao": "d",
                "prazo_meses": 60, "juros_totais": 57100.0,
                "economia_vs_pagar_so_o_minimo_disponivel": False,
            }]
        )
        # 57000 com fato 57100: diff=100, tolerância=285 → ACEITO
        text = "Juros de R$ 57.000."
        issues = explanation_issues(text, facts_json, budget_below=False)
        assert "UNGROUNDED_AMOUNT" not in issues

    def test_non_round_value_uses_1_tolerance(self):
        """Valor não-arredondado usa tolerância de R$ 1."""
        facts_json = _make_facts(
            cenarios=[{
                "id": "consolidation", "nome": "C", "descricao": "d",
                "prazo_meses": 60, "juros_totais": 56667.67,
                "economia_vs_pagar_so_o_minimo_disponivel": False,
            }]
        )
        # 56668.68 está a 1.01 de 56667.67 → UNGROUNDED (tolerância = 1.0)
        text = "Juros de R$ 56.668,68."
        issues = explanation_issues(text, facts_json, budget_below=False)
        assert "UNGROUNDED_AMOUNT" in issues


# ---------------------------------------------------------------------------
# R-22(c): grounded_amounts exclui prazo_meses
# ---------------------------------------------------------------------------

class TestGroundedAmountsR22c:
    def test_prazo_meses_not_in_grounded(self):
        """prazo_meses=13 NÃO deve estar em grounded_amounts."""
        facts = json.dumps({
            "cenarios": [{
                "id": "a", "nome": "A", "descricao": "d",
                "prazo_meses": 13,
                "juros_totais": 5000.0,
            }],
            "primeiros_passos": [],
        })
        grounded = grounded_amounts(facts)
        assert 13.0 not in grounded, "prazo_meses não deve estar no conjunto fundamentado"
        assert 5000.0 in grounded

    def test_prazo_as_rs_is_ungrounded(self):
        """R$ 13 é UNGROUNDED quando 13 só existe como prazo_meses (não como valor monetário)."""
        facts = json.dumps({
            "cenarios": [{
                "id": "a", "nome": "A", "descricao": "d",
                "prazo_meses": 13,
                "juros_totais": 5000.0,
            }],
            "primeiros_passos": [],
        })
        text = "O plano quita em R$ 13 de juros totais."
        issues = explanation_issues(text, facts, budget_below=False)
        assert "UNGROUNDED_AMOUNT" in issues

    def test_monetary_values_still_grounded(self):
        """juros_totais e economia_vs_pagar_so_o_minimo continuam fundamentados."""
        facts = json.dumps({
            "soma_dos_minimos": 1200.0,
            "cenarios": [{
                "id": "a", "nome": "A", "descricao": "d",
                "prazo_meses": 60,
                "juros_totais": 5000.0,
                "economia_vs_pagar_so_o_minimo": 2000.0,
            }],
            "primeiros_passos": [],
        })
        grounded = grounded_amounts(facts)
        assert 5000.0 in grounded
        assert 2000.0 in grounded
        assert 1200.0 in grounded
        assert 60.0 not in grounded


# ---------------------------------------------------------------------------
# R-22(e): _fmt_prazo e fallback com prazo correto
# ---------------------------------------------------------------------------

from src.agents.explanation_guard import _fmt_prazo  # noqa: E402


class TestFmtPrazoR22e:
    @pytest.mark.parametrize("meses, expected", [
        (7,  "7 meses"),
        (12, "12 meses (1 ano)"),
        (24, "24 meses (2 anos)"),
        (30, "30 meses (2 anos e 6 meses)"),
        (13, "13 meses (1 ano e 1 mês)"),
        (36, "36 meses (3 anos)"),
    ])
    def test_fmt_prazo(self, meses, expected):
        assert _fmt_prazo(meses) == expected, f"_fmt_prazo({meses}) = {_fmt_prazo(meses)!r}"


class TestFallbackPrazoR22e:
    def _make_facts_prazo(self, prazo_meses):
        return json.dumps({
            "cenario_recomendado": "consolidation",
            "orcamento_abaixo_dos_minimos": False,
            "cenarios": [{
                "id": "consolidation", "nome": "Consolidação", "descricao": "d",
                "prazo_meses": prazo_meses,
                "juros_totais": 5000.0,
                "economia_vs_pagar_so_o_minimo": 1000.0,
            }],
            "primeiros_passos": ["Mês 1: pagar R$ 594.43 em bb (empréstimo pessoal)"],
        })

    def test_fallback_30_meses(self):
        result = fallback_explanation(self._make_facts_prazo(30), LanguageLevel.SIMPLE)
        assert "2 anos e 6 meses" in result, f"Esperado '2 anos e 6 meses' em: {result}"

    def test_fallback_12_meses(self):
        result = fallback_explanation(self._make_facts_prazo(12), LanguageLevel.SIMPLE)
        assert "1 ano" in result
        assert "2 anos" not in result

    def test_fallback_24_meses(self):
        result = fallback_explanation(self._make_facts_prazo(24), LanguageLevel.SIMPLE)
        assert "2 anos" in result
        assert "meses" not in result.replace("24 meses", "")  # não deve ter "X meses" extra

    def test_fallback_7_meses(self):
        result = fallback_explanation(self._make_facts_prazo(7), LanguageLevel.SIMPLE)
        # Menos de 12 meses: apenas "7 meses" sem parênteses com anos
        assert "7 meses" in result
        import re
        assert not re.search(r"\bano(s)?\b", result), (
            f"'ano(s)' não deveria aparecer como palavra isolada para prazo < 12: {result}"
        )

    def test_fallback_normalizes_primeiros_passos_to_br(self):
        """Valores EN no primeiros_passos devem ser convertidos para BR no fallback."""
        result = fallback_explanation(self._make_facts_prazo(12), LanguageLevel.SIMPLE)
        # "R$ 594.43" (EN) deve virar "R$ 594,43" (BR)
        assert "R$ 594,43" in result, f"Esperado 'R$ 594,43' em: {result}"


# ---------------------------------------------------------------------------
# #5: _fmt_step não deve produzir "pagarR$" (espaço antes de R$ preservado)
# ---------------------------------------------------------------------------

from src.agents.explanation_guard import _fmt_step  # noqa: E402


class TestFmtStep:
    def test_no_missing_space_before_rs(self):
        """Garante que _fmt_step não consome o espaço antes de R$."""
        step = "Mês 1: pagar R$ 594.43 em bb (empréstimo pessoal)"
        result = _fmt_step(step)
        assert "pagarR$" not in result, f"Espaço antes de R$ sumiu: {result!r}"
        assert "pagar R$ 594,43" in result, f"Valor esperado ausente: {result!r}"

    def test_large_amount_no_missing_space(self):
        """Garante preservação do espaço com valor grande."""
        step = "Mês 1: pagar R$ 4275.85 em nova linha de consolidação BB"
        result = _fmt_step(step)
        assert "pagarR$" not in result, f"Espaço antes de R$ sumiu: {result!r}"
        assert "R$ 4.275,85" in result, f"Valor formatado incorretamente: {result!r}"




# ---------------------------------------------------------------------------
# Issue #6 (itens 7, 8, 10): nomes em português, códigos internos e formato de valor
# ---------------------------------------------------------------------------

class TestProductLabels:
    def test_every_product_type_has_portuguese_label(self):
        from src.api.models import PRODUCT_LABELS_PT, ProductType

        assert set(PRODUCT_LABELS_PT) == set(ProductType)
        for label in PRODUCT_LABELS_PT.values():
            assert label == label.lower() and "_" not in label

    def test_payroll_loan_is_consignado(self):
        from src.api.models import product_label_pt

        assert product_label_pt("PAYROLL_LOAN") == "crédito consignado"
        assert product_label_pt("NAO_EXISTE") == "dívida"

    def test_first_steps_use_label_not_enum_code(self):
        from src.api.models import ScenarioId, SimulationScenario
        from src.services.recommendation_service import _first_steps

        debts = [{"debt_id": "aaaaaaaa-0000-0000-0000-000000000009", "institution": "bb", "product_type": "PAYROLL_LOAN"}]
        scenario = SimulationScenario(
            scenario_id=ScenarioId.AGGRESSIVE, label="Agressivo", description="d",
            total_months=1, total_interest_paid=0.0, total_savings_vs_minimum=0.0,
            monthly_plan=[{"month": 1, "payments": [{"debt_id": "aaaaaaaa-0000-0000-0000-000000000009", "amount": 1350.6}]}],
        )
        steps = _first_steps([scenario], debts)(ScenarioId.AGGRESSIVE)
        assert steps == ["Mês 1: pagar R$ 1.350,60 em bb (crédito consignado)"]


class TestInternalCodeLeak:
    @pytest.mark.parametrize("text", [
        "Pagar no financiamento de carro (PAYROLL_LOAN).",
        "Quite o VEHICLE_LOAN primeiro.",
        "O plano minimum_viable é o mais barato.",
        "cartão credit_card em atraso",
    ])
    def test_detects_codes(self, text):
        assert "INTERNAL_CODE_LEAK" in explanation_issues(text, "{}", budget_below=False)

    @pytest.mark.parametrize("text", [
        "Pagar no crédito consignado. O plano de consolidação é o recomendado.",
        "O cenário mínimo viável quita em 12 meses.",
    ])
    def test_plain_text_is_clean(self, text):
        assert "INTERNAL_CODE_LEAK" not in explanation_issues(text, "{}", budget_below=False)

    def test_retry_has_corrective_instruction(self):
        from src.agents.recommendation_agent import _CORRECTIVE_INSTRUCTIONS

        assert "INTERNAL_CODE_LEAK" in _CORRECTIVE_INSTRUCTIONS


class TestBrlFormatting:
    @pytest.mark.parametrize("value,expected", [
        (3000, "R$ 3.000,00"), (1350.6, "R$ 1.350,60"), (0.005, "R$ 0,01"),
        (594.43, "R$ 594,43"), (1234567.891, "R$ 1.234.567,89"), (-2569.21, "-R$ 2.569,21"),
    ])
    def test_format_brl(self, value, expected):
        from src.simulation.engine import format_brl

        assert format_brl(value) == expected

    @pytest.mark.parametrize("text,expected", [
        ("orçamento de R$ 3000,00", "orçamento de R$ 3.000,00"),
        ("pagar R$ 1350.60 hoje", "pagar R$ 1.350,60 hoje"),
        ("já correto: R$ 1.234,56.", "já correto: R$ 1.234,56."),
        ("são R$ 50 mil no total", "são R$ 50 mil no total"),
        ("custa R$ 2 milhões", "custa R$ 2 milhões"),
        ("sem valores aqui", "sem valores aqui"),
    ])
    def test_normalize_brl(self, text, expected):
        from src.agents.explanation_guard import normalize_brl

        assert normalize_brl(text) == expected

    def test_normalized_text_keeps_amounts_grounded(self):
        from src.agents.explanation_guard import normalize_brl

        facts = json.dumps({"soma_dos_minimos": 3000.0, "cenarios": []})
        assert explanation_issues(normalize_brl("mínimos de R$ 3000,00"), facts, budget_below=False) == []
