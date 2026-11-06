"""
Guarda de PII — M4, Fase 4.

assert_no_pii(doc) varre recursivamente dict/list/tuple/str e levanta
PIIViolation se encontrar:
  - uma chave proibida (comparação case-insensitive, nome exato)
  - uma chave string que seja ou contenha um CPF válido (com ou sem máscara)
  - um valor string que seja ou contenha um CPF válido (com ou sem máscara)

Falsos positivos são evitados:
  - 11 dígitos contínuos só contam se delimitados por não-alfanumérico
    E tiverem dígitos verificadores de CPF válidos (rejeita sequências repetidas).
  - Inteiros e floats não são verificados.
  - "name" NÃO está na lista proibida (agent.name legítimo no snapshot do M2).

Segurança no path:
  - Segmentos do path que correspondam a um CPF são substituídos por "<cpf>"
    para que PIIViolation.path nunca exponha um CPF.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Chaves proibidas (case-insensitive, comparação exata)
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "cpf",
    "cpf_number",
    "nome",
    "nome_completo",
    "full_name",
    "customer_name",
    "email",
    "e_mail",
    "telefone",
    "phone",
    "celular",
    "endereco",
    "address",
    "contractid",
    "contract_id",
    "contract_number",
    "numero_contrato",
    "rg",
})

# ---------------------------------------------------------------------------
# Padrões de CPF em strings
# ---------------------------------------------------------------------------

# 1. Valor inteiro no formato CPF: 11 dígitos com ou sem máscara
_CPF_FULL = re.compile(r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$")

# 2. CPF com máscara embutido em texto (delimitado por não-alfanumérico).
# Separadores aceitos entre os grupos 3-3-3-2: ponto, hífen, barra, tab, espaço comum ou NBSP.
# Lookbehind duplo evita "0 529 982 247 25" (dígito colado via espaço).
# Lookahead duplo evita "529 982 247 25 1" (dígito colado via espaço após).
_CPF_SEP = r"[.\-/\t \u00a0]"
_CPF_MASKED_EMBEDDED = re.compile(
    r"(?<!\d )(?<!\d\u00a0)(?<!\d\t)(?<![0-9A-Za-z])"
    r"(\d{3})" + _CPF_SEP + r"(\d{3})" + _CPF_SEP + r"(\d{3})" + _CPF_SEP + r"(\d{2})"
    r"(?! \d)(?!\u00a0\d)(?!\t\d)(?![0-9A-Za-z])"
)

# 3. 11 dígitos contínuos delimitados por não-dígito (bordas por não-dígito).
# Permite CPF grudado em letras (ex.: "CPF52998224725", "id52998224725x").
_CPF_DIGITS_EMBEDDED = re.compile(
    r"(?<!\d)(\d{11})(?!\d)"
)


# ---------------------------------------------------------------------------
# Validação de dígitos verificadores de CPF
# ---------------------------------------------------------------------------

def _is_valid_cpf(digits: str) -> bool:
    """
    Retorna True se os 11 dígitos formam um CPF com dígitos verificadores válidos.
    Rejeita sequências de dígito único (111.111.111-11 etc.).
    """
    if len(digits) != 11:
        return False
    # Rejeita sequências repetidas (00000000000, 11111111111 … 99999999999)
    if len(set(digits)) == 1:
        return False

    def _check(cpf: str, n: int) -> bool:
        total = sum(int(d) * w for d, w in zip(cpf, range(n, 1, -1)))
        remainder = (total * 10) % 11
        digit = 0 if remainder == 10 else remainder
        return digit == int(cpf[n - 1])

    return _check(digits, 10) and _check(digits, 11)


# ---------------------------------------------------------------------------
# Exceção
# ---------------------------------------------------------------------------

class PIIViolation(ValueError):
    """
    Levantada quando assert_no_pii detecta dado pessoal no documento.

    Atributo `path` indica a localização (ex.: "debts[2].notes").
    A mensagem NUNCA inclui o valor encontrado.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        super().__init__(f"PII detectado em {path!r}: {reason}")


# ---------------------------------------------------------------------------
# Verificação de valor string
# ---------------------------------------------------------------------------

def _check_string(value: str, path: str) -> None:
    """Levanta PIIViolation se a string contiver um CPF."""
    stripped = value.strip()

    # Regra 1: valor INTEIRO no formato CPF (com ou sem máscara).
    # Exige dígitos verificadores válidos para evitar falsos positivos
    # com números que acidentalmente têm 11 dígitos mas não são CPF.
    if _CPF_FULL.match(stripped):
        digits = re.sub(r"\D", "", stripped)
        if _is_valid_cpf(digits):
            raise PIIViolation(path, "valor é um CPF (com ou sem máscara)")

    # Regra 2: CPF com máscara embutido em texto (delimitado por não-alfanumérico).
    # Extrai os 4 grupos capturados e valida os dígitos verificadores.
    for m in _CPF_MASKED_EMBEDDED.finditer(value):
        digits = m.group(1) + m.group(2) + m.group(3) + m.group(4)
        if _is_valid_cpf(digits):
            raise PIIViolation(path, "CPF mascarado embutido no texto")

    # Regra 3: 11 dígitos contínuos embutidos, delimitados e com check digits válidos.
    # Inteiros de 11 dígitos em hexadecimais/IDs não ativam esta regra.
    for m in _CPF_DIGITS_EMBEDDED.finditer(value):
        digits = m.group(1)
        if _is_valid_cpf(digits):
            raise PIIViolation(path, "CPF (11 dígitos) embutido no texto")


# ---------------------------------------------------------------------------
# Mascaramento de segmentos de path que contenham CPF
# ---------------------------------------------------------------------------

def _mask_path_segment(segment: str) -> str:
    """
    Retorna "<cpf>" se o segmento for ou contiver um CPF válido;
    caso contrário retorna o segmento original.
    """
    stripped = segment.strip()
    # Regra 1: segmento inteiro no formato CPF
    if _CPF_FULL.match(stripped):
        return "<cpf>"
    # Regra 2: CPF com máscara embutido
    for m in _CPF_MASKED_EMBEDDED.finditer(segment):
        digits = m.group(1) + m.group(2) + m.group(3) + m.group(4)
        if _is_valid_cpf(digits):
            return "<cpf>"
    # Regra 3: 11 dígitos contínuos com check digits válidos
    for m in _CPF_DIGITS_EMBEDDED.finditer(segment):
        if _is_valid_cpf(m.group(1)):
            return "<cpf>"
    return segment


def _safe_path(path: str, key: str) -> str:
    """
    Constrói o child_path mascarando o segmento `key` se for um CPF.
    """
    safe_key = _mask_path_segment(key)
    return f"{path}.{safe_key}" if path else safe_key


# ---------------------------------------------------------------------------
# Varredura recursiva
# ---------------------------------------------------------------------------

def _check_int(value: int, path: str) -> None:
    """
    Levanta PIIViolation se o inteiro representar um CPF válido.

    Converte para string com zero-fill(11) para cobrir CPFs com dígitos iniciais
    zero (ex.: int 1234567890 → "01234567890", 9–11 dígitos significativos).
    Inteiros negativos e com mais de 11 dígitos nunca são CPF.
    bool é subclasse de int mas NÃO deve ser verificado.
    """
    if isinstance(value, bool):
        return
    if value < 0:
        return
    s = str(value)
    if len(s) > 11:
        return
    digits = s.zfill(11)
    if _is_valid_cpf(digits):
        raise PIIViolation(path, "valor inteiro é um CPF")


def _scan(node: object, path: str) -> None:
    if isinstance(node, dict):
        for key, val in node.items():
            child_path = _safe_path(path, str(key)) if isinstance(key, str) else (
                f"{path}.{key}" if path else str(key)
            )
            # Verificar chave proibida (nome exato, case-insensitive)
            if isinstance(key, str) and key.lower() in _FORBIDDEN_KEYS:
                raise PIIViolation(child_path, f"chave proibida: {key!r}")
            # Verificar chave como valor (CPF como chave do dict)
            if isinstance(key, str):
                try:
                    _check_string(key, child_path)
                except PIIViolation:
                    # Re-raise com o child_path já mascarado
                    raise PIIViolation(child_path, "chave contém CPF")
            _scan(val, child_path)
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            _scan(item, f"{path}[{i}]")
    elif isinstance(node, str):
        _check_string(node, path)
    elif isinstance(node, int) and not isinstance(node, bool):
        _check_int(node, path)
    # float, bool, None → não verificados


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def assert_no_pii(doc: object) -> None:
    """
    Varre `doc` recursivamente (dict/list/tuple/str) e levanta PIIViolation
    na primeira ocorrência de PII encontrada.

    Retorna None se o documento está limpo.
    """
    _scan(doc, "")
