"""
Guarda determinística de números na explicação do Agente 2 (B4).

Detecta valores R$ inventados pelo LLM (não fundamentados nos FATOS) e
afirmações de economia quando o orçamento está abaixo dos mínimos.
Sem rede, sem I/O — funções puras.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.api.models import LanguageLevel

# ---------------------------------------------------------------------------
# parse_brl_amounts
# ---------------------------------------------------------------------------

# Padrão R$ seguido de número — captura o token numérico inteiro (dígitos, pontos, vírgulas)
# mas não consome ponto/vírgula final de frase (ex: "R$ 1.500." ou "custa R$ 1.500, e").
_BRL_RE = re.compile(
    r"R\$\s*"
    r"(\d[\d.,]*\d|\d)"
    r"(?:\s+mil\b)?",
    re.IGNORECASE,
)


def parse_brl_amounts(text: str) -> list[float]:
    """
    Extrai valores precedidos de "R$" do texto e converte para float.

    Formatos suportados:
      "R$ 49.335,04"   → 49335.04   (milhar ponto, decimal vírgula)
      "R$ 1.500"       → 1500.0     (milhar ponto, sem decimal)
      "R$ 594,43"      → 594.43     (decimal vírgula, sem milhar)
      "R$ 594.43"      → 594.43     (decimal ponto, sem vírgula)
      "R$1.234,56"     → 1234.56
      "R$ 50 mil"      → 50000.0
    """
    results: list[float] = []
    for m in _BRL_RE.finditer(text):
        raw = m.group(0)
        num_str = m.group(1)
        is_mil = bool(re.search(r"\s+mil\b", raw, re.IGNORECASE))

        value = _parse_number(num_str)
        if is_mil:
            value *= 1000.0
        results.append(value)
    return results


def _parse_number(s: str) -> float:
    """
    Converte string numérica com formatação BR ou EN para float.

    Regra:
      - Se termina em ",dd" (exatamente 1 ou 2 dígitos após vírgula): pontos são
        separadores de milhar, vírgula é decimal.
      - Se termina em ".dd" e não contém vírgula: ponto é decimal.
      - Senão: pontos são milhar (número inteiro ou ambíguo).
    """
    # termina em vírgula + 1 ou 2 dígitos
    if re.search(r",\d{1,2}$", s):
        # formato BR: pontos = milhar, vírgula = decimal
        s = s.replace(".", "").replace(",", ".")
    elif re.search(r"\.\d{1,2}$", s) and "," not in s:
        # formato EN: ponto = decimal
        pass
    else:
        # pontos são milhar; sem decimal (ou formato ambíguo de 3 dígitos após ponto)
        s = s.replace(".", "").replace(",", "")
    return float(s)


# ---------------------------------------------------------------------------
# grounded_amounts
# ---------------------------------------------------------------------------

def grounded_amounts(facts_json: str) -> set[float]:
    """
    Retorna o conjunto de todos os números fundamentados nos FATOS:
    - Todos os int/float encontrados recursivamente no JSON.
    - Todos os valores R$ encontrados em strings do JSON (descricao, primeiros_passos, etc.).
    """
    try:
        data = json.loads(facts_json)
    except (json.JSONDecodeError, TypeError):
        return set()

    nums: set[float] = set()
    _collect_numbers(data, nums)
    return nums


def _collect_numbers(obj: Any, out: set[float]) -> None:
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.add(float(obj))
    elif isinstance(obj, str):
        for v in parse_brl_amounts(obj):
            out.add(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_numbers(v, out)


# ---------------------------------------------------------------------------
# explanation_issues
# ---------------------------------------------------------------------------

# Afirmação positiva de economia seguida de valor R$
_SAVINGS_RE = re.compile(
    r"(?<!não )(?<!nao )(?<!sem )\b(economiz\w*|economia)\b[^.\n]{0,40}R\$\s?\d",
    re.IGNORECASE,
)


def explanation_issues(
    text: str,
    facts_json: str,
    *,
    budget_below: bool,
    tolerance: float = 1.0,
) -> list[str]:
    """
    Retorna lista de códigos de problema (sem texto do usuário).

    Códigos possíveis:
      "UNGROUNDED_AMOUNT"            — valor R$ no texto não está nos FATOS (±tolerance).
      "SAVINGS_CLAIM_BELOW_MINIMUMS" — afirmação positiva de economia quando budget_below=True.
    """
    issues: list[str] = []
    grounded = grounded_amounts(facts_json)

    for val in parse_brl_amounts(text):
        if not any(abs(val - g) <= tolerance for g in grounded):
            issues.append("UNGROUNDED_AMOUNT")
            break  # um código por problema, não um por ocorrência

    if budget_below and _SAVINGS_RE.search(text):
        issues.append("SAVINGS_CLAIM_BELOW_MINIMUMS")

    return issues


# ---------------------------------------------------------------------------
# fallback_explanation
# ---------------------------------------------------------------------------

_MONTH_NAMES = [
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]

_CONSOLIDATION_IDS = {"consolidation"}


def _fmt_brl(value: float) -> str:
    """Formata float para R$ 1.234,56."""
    int_part = int(value)
    dec_part = round((value - int_part) * 100)
    int_str = f"{int_part:,}".replace(",", ".")
    return f"R$ {int_str},{dec_part:02d}"


def fallback_explanation(facts_json: str, language_level: LanguageLevel) -> str:
    """
    Explicação determinística em PT-BR, montada exclusivamente com os FATOS.
    ≤ 180 palavras. Passa em explanation_issues por construção.
    """
    try:
        facts = json.loads(facts_json)
    except (json.JSONDecodeError, TypeError):
        return "Não foi possível gerar a explicação. Consulte seu gerente."

    rec_id = facts.get("cenario_recomendado", "")
    cenarios = {c["id"]: c for c in facts.get("cenarios", [])}
    rec = cenarios.get(rec_id, {})
    abaixo = facts.get("orcamento_abaixo_dos_minimos", False)
    soma_minimos = facts.get("soma_dos_minimos")
    primeiros_passos = facts.get("primeiros_passos", [])

    nome = rec.get("nome", rec_id)
    prazo = rec.get("prazo_meses", "?")
    juros = rec.get("juros_totais")
    juros_str = _fmt_brl(juros) if juros is not None else "valor não disponível"

    if language_level == LanguageLevel.TECHNICAL:
        intro = (
            f"Cenário recomendado: **{nome}** — prazo de {prazo} meses, "
            f"juros totais {juros_str}."
        )
    else:
        anos = int(prazo) // 12 if isinstance(prazo, int) else None
        prazo_str = (
            f"{prazo} meses ({anos} ano{'s' if anos != 1 else ''})"
            if anos and anos >= 1
            else f"{prazo} meses"
        )
        intro = (
            f"O plano recomendado é **{nome}**, com duração de {prazo_str} "
            f"e juros totais de {juros_str}."
        )

    parts = [intro]

    if abaixo:
        min_str = _fmt_brl(soma_minimos) if soma_minimos is not None else "o valor mínimo"
        parts.append(
            f"Atenção: seu orçamento mensal está abaixo dos pagamentos mínimos "
            f"({min_str}). Haverá atraso e encargos adicionais. "
            f"Recomendamos renegociar diretamente com as instituições credoras."
        )
    else:
        economia = rec.get("economia_vs_pagar_so_o_minimo")
        if economia is not None and economia >= 0:
            parts.append(f"Comparado a pagar só o mínimo, este plano economiza {_fmt_brl(economia)} em juros.")

    if rec_id in _CONSOLIDATION_IDS:
        parts.append(
            "Importante: a taxa de consolidação é uma estimativa e depende de aprovação de crédito."
        )

    if primeiros_passos:
        if isinstance(primeiros_passos, list):
            steps_text = "; ".join(primeiros_passos[:3])
        else:
            steps_text = str(primeiros_passos)
        parts.append(f"Primeiros passos: {steps_text}.")

    parts.append("Os valores são estimativas; resultado final sujeito a aprovação.")

    return " ".join(parts)
