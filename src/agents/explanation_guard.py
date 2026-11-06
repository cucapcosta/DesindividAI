"""
Guarda determinística de números na explicação do Agente 2 (B4).

Detecta valores R$ inventados pelo LLM (não fundamentados nos FATOS) e
afirmações de economia quando o orçamento está abaixo dos mínimos.
Sem rede, sem I/O — funções puras.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from src.api.models import LanguageLevel

# ---------------------------------------------------------------------------
# parse_brl_amounts
# ---------------------------------------------------------------------------

# Padrão R$ seguido de número — captura o token numérico inteiro (dígitos, pontos, vírgulas)
# mas não consome ponto/vírgula final de frase (ex: "R$ 1.500." ou "custa R$ 1.500, e").
# Suporta sinal negativo: "-R$ 1.234,56" ou "R$ -1.234,56".
# Suporta sufixo "mil", "milhão", "milhões".
_BRL_RE = re.compile(
    r"(-?)\s*R\$\s*(-?)\s*"
    r"(\d[\d.,]*\d|\d)"
    r"(?:\s+(mil|milh[ãa]o|milh[õo]es)\b)?",
    re.IGNORECASE,
)


def parse_brl_amounts(text: str) -> list[float]:
    """
    Extrai valores precedidos de "R$" do texto e converte para float.

    Formatos suportados:
      "R$ 49.335,04"    → 49335.04   (milhar ponto, decimal vírgula)
      "R$ 1.500"        → 1500.0     (milhar ponto, sem decimal)
      "R$ 594,43"       → 594.43     (decimal vírgula, sem milhar)
      "R$ 594.43"       → 594.43     (decimal ponto, sem vírgula)
      "R$1.234,56"      → 1234.56
      "R$ 50 mil"       → 50000.0
      "-R$ 1.234,56"    → -1234.56   (negativo prefixo)
      "R$ -1.234,56"    → -1234.56   (negativo pós-símbolo)
      "R$ 1,2 milhão"   → 1200000.0
      "R$ 2 milhões"    → 2000000.0
    """
    results: list[float] = []
    for m in _BRL_RE.finditer(text):
        neg_prefix = m.group(1)   # "-" antes do R$ ou ""
        neg_after = m.group(2)    # "-" entre R$ e número ou ""
        num_str = m.group(3)
        suffix = (m.group(4) or "").lower()

        is_negative = bool(neg_prefix or neg_after)

        value = _parse_number(num_str)
        if re.match(r"milh[ãa]o|milh[õo]es", suffix, re.IGNORECASE):
            value *= 1_000_000.0
        elif suffix == "mil":
            value *= 1000.0

        if is_negative:
            value = -value
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

# Chaves de cenário que contêm valores monetários fundamentados (R$).
# Excluídas: prazo_meses, ids, contagens — evita que um prazo (ex: 13 meses)
# seja confundido com um valor monetário (ex: R$ 13).
_MONETARY_SCENARIO_KEYS = frozenset({
    "juros_totais",
    "juros_se_pagar_so_o_minimo",
    "economia_vs_pagar_so_o_minimo",
    "soma_dos_minimos",
})

# Chaves raiz que contêm valores monetários
_MONETARY_ROOT_KEYS = frozenset({
    "soma_dos_minimos",
})

# Chaves de string que podem conter valores R$ inline (descricao, primeiros_passos)
_MONETARY_STRING_KEYS = frozenset({
    "descricao",
    "primeiros_passos",
})

# Chaves de cenário a EXCLUIR (nunca são R$)
_NON_MONETARY_SCENARIO_KEYS = frozenset({
    "prazo_meses",
    "id",
    "nome",
    "economia_vs_pagar_so_o_minimo_disponivel",
})


def grounded_amounts(facts_json: str) -> set[float]:
    """
    Retorna o conjunto de valores R$ fundamentados nos FATOS.

    Inclui:
    - Valores numéricos dos campos monetários de cada cenário (juros_totais,
      juros_se_pagar_so_o_minimo, economia_vs_pagar_so_o_minimo, soma_dos_minimos).
    - Valores de qualquer cenário (o texto pode comparar cenários).
    - Valores R$ encontrados em strings de descricao e primeiros_passos.

    Exclui:
    - prazo_meses, ids, contagens — evita falsos negativos para alucinações de prazo.
    - Valores bool.
    """
    try:
        data = json.loads(facts_json)
    except (json.JSONDecodeError, TypeError):
        return set()

    nums: set[float] = set()

    # soma_dos_minimos raiz
    soma = data.get("soma_dos_minimos")
    if isinstance(soma, (int, float)) and not isinstance(soma, bool):
        nums.add(float(soma))

    # campos monetários de cada cenário (qualquer cenário)
    for cenario in data.get("cenarios", []):
        if not isinstance(cenario, dict):
            continue
        for key in _MONETARY_SCENARIO_KEYS:
            val = cenario.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                nums.add(float(val))
        # strings de descricao e primeiros_passos → extrair R$
        for str_key in _MONETARY_STRING_KEYS:
            val = cenario.get(str_key)
            if isinstance(val, str):
                for v in parse_brl_amounts(val):
                    nums.add(v)

    # primeiros_passos raiz (lista de strings ou string)
    primeiros_passos = data.get("primeiros_passos", [])
    if isinstance(primeiros_passos, list):
        for item in primeiros_passos:
            if isinstance(item, str):
                for v in parse_brl_amounts(item):
                    nums.add(v)
    elif isinstance(primeiros_passos, str):
        for v in parse_brl_amounts(primeiros_passos):
            nums.add(v)

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

# Negações aceitas antes de "economiz*" / "economia" (até 2 palavras entre a negação e o termo).
# Cobre: "não economiza", "nao economiza", "sem economia", "nenhuma economia",
#        "não haverá economia", "não há economia", "não terá economia",
#        "não vai economizar", "não gera economia".
_SAVINGS_NEG_PREFIXES = r"(?:não\s+haverá|não\s+há|não\s+terá|não\s+vai|não\s+gera|não|nao|sem|nenhuma)"
_SAVINGS_RE = re.compile(
    r"(?:"
    + _SAVINGS_NEG_PREFIXES
    + r"\s+(?:\w+\s+){0,2})"         # negação + até 2 palavras intermediárias
    r"|\b"                           # OU início de palavra sem negação
    r"(economiz\w*|economia)\b[^.\n]{0,40}R\$\s?\d",
    re.IGNORECASE,
)

# Regex que detecta afirmação POSITIVA de economia (sem negação à esquerda).
# Para evitar falsos positivos com frases negadas, fazemos a verificação em dois passos:
# primeiro testamos se existe "economiz*/economia" + "R$ <dígito>", depois excluímos
# os que têm uma negação imediatamente antes (até 4 tokens).
_SAVINGS_POS_RE = re.compile(
    r"\b(economiz\w*|economia)\b[^.\n]{0,40}R\$\s?\d",
    re.IGNORECASE,
)
_SAVINGS_NEG_BEFORE_RE = re.compile(
    _SAVINGS_NEG_PREFIXES + r"\s+(?:\w+\s+){0,2}(economiz\w*|economia)\b",
    re.IGNORECASE,
)


def _is_savings_claim(text: str) -> bool:
    """
    Retorna True se o texto contiver uma afirmação POSITIVA de economia com valor R$.
    Frases negadas ("não haverá economia", "sem economia", etc.) retornam False.
    """
    # Verificar se existe algum match positivo
    if not _SAVINGS_POS_RE.search(text):
        return False
    # Remover as ocorrências negadas e verificar se ainda restam positivas
    # Substituir os trechos negados por espaços e re-testar
    cleaned = _SAVINGS_NEG_BEFORE_RE.sub(" ", text)
    return bool(_SAVINGS_POS_RE.search(cleaned))


def _tolerance_for(val: float) -> float:
    """
    Retorna a tolerância para comparação de um valor R$ com o conjunto fundamentado.

    Regra:
    - Se o valor for inteiro múltiplo de 1.000 (arredondamento legítimo):
      tolerância = max(1.0, 0.5% do valor).
    - Caso contrário: R$ 1,00.
    """
    is_rounded = (val != 0 and val % 1000 == 0)
    if is_rounded:
        return max(1.0, abs(val) * 0.005)
    return 1.0


def _round_to_milhar(val: float) -> int:
    """Arredonda para o milhar mais próximo (ex: 56667.67 → 57000)."""
    return int(round(val / 1000.0)) * 1000


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
        tol = max(tolerance, _tolerance_for(val))
        # Critério 1: distância absoluta dentro da tolerância
        if any(abs(val - g) <= tol for g in grounded):
            continue
        # Critério 2 (apenas para múltiplos de 1000): valor arredondado ao milhar mais
        # próximo coincide com algum grounded arredondado ao milhar mais próximo.
        # Aceita "R$ 57 mil" quando o fato é 56.667,67 (ambos arredondam para 57.000).
        if val != 0 and val % 1000 == 0:
            val_milhar = _round_to_milhar(val)
            if any(_round_to_milhar(g) == val_milhar for g in grounded):
                continue
        issues.append("UNGROUNDED_AMOUNT")
        break  # um código por problema, não um por ocorrência

    if budget_below and _is_savings_claim(text):
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
    """Formata float para R$ 1.234,56 (ou -R$ 1.234,56 para negativos).

    Usa Decimal com ROUND_HALF_UP para arredondamento correto (ex: 0,005 → 0,01).
    """
    quantized = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = quantized < 0
    abs_val = abs(quantized)
    # Separar parte inteira e centavos
    int_part = int(abs_val)
    cents = int(abs_val * 100) % 100
    # Formatar parte inteira com separador de milhar (ponto)
    int_str = f"{int_part:,}".replace(",", ".")
    brl = f"R$ {int_str},{cents:02d}"
    return f"-{brl}" if negative else brl


def _fmt_prazo(prazo: int) -> str:
    """
    Formata prazo em meses para string legível.

    Exemplos:
      7  → "7 meses"
      12 → "12 meses (1 ano)"
      24 → "24 meses (2 anos)"
      30 → "30 meses (2 anos e 6 meses)"
    """
    anos = prazo // 12
    meses_resto = prazo % 12

    if anos == 0:
        return f"{prazo} meses"
    if meses_resto == 0:
        label_anos = f"{anos} ano{'s' if anos != 1 else ''}"
        return f"{prazo} meses ({label_anos})"
    label_anos = f"{anos} ano{'s' if anos != 1 else ''}"
    label_meses = f"{meses_resto} {'meses' if meses_resto != 1 else 'mês'}"
    return f"{prazo} meses ({label_anos} e {label_meses})"


def _fmt_step(step: str) -> str:
    """
    Normaliza um passo de primeiros_passos para formato BR.

    Converte "R$ 594.43" (ponto decimal EN) para "R$ 594,43" (BR) quando aplicável.
    Deixa o restante do texto inalterado.
    """
    def _replace_amount(m: "re.Match[str]") -> str:
        raw_num = m.group(3)
        # Converter EN → BR se necessário (ponto decimal sem vírgula)
        try:
            value = _parse_number(raw_num)
            formatted = _fmt_brl(value)
        except (ValueError, Exception):
            return m.group(0)
        # _BRL_RE inclui \s* antes de R$, que pode consumir o espaço antes do símbolo.
        # Preservar esse espaço para evitar "pagarR$" em vez de "pagar R$".
        matched = m.group(0)
        leading_ws = matched[: len(matched) - len(matched.lstrip())]
        # Preservar sinal negativo, se houver
        is_neg = bool(m.group(1) or m.group(2))
        replaced = formatted if not is_neg else f"-{formatted}"
        return leading_ws + replaced

    return _BRL_RE.sub(_replace_amount, step)


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
        prazo_str = f"{prazo} meses" if isinstance(prazo, int) else str(prazo)
        intro = (
            f"Cenário recomendado: **{nome}** — prazo de {prazo_str}, "
            f"juros totais {juros_str}."
        )
    else:
        prazo_str = _fmt_prazo(prazo) if isinstance(prazo, int) else f"{prazo} meses"
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
            # Normalizar cada passo para formato BR e juntar os 3 primeiros
            normalized = [_fmt_step(s) for s in primeiros_passos[:3] if isinstance(s, str)]
            steps_text = "; ".join(normalized)
        else:
            steps_text = _fmt_step(str(primeiros_passos))
        parts.append(f"Primeiros passos: {steps_text}.")

    parts.append("Os valores são estimativas; resultado final sujeito a aprovação.")

    return " ".join(parts)
