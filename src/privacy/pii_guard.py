"""
Guarda de PII — M4, Fase 4.

assert_no_pii(doc) varre recursivamente dict/list/tuple/str e levanta
PIIViolation se encontrar:
  - uma chave proibida (comparação case-insensitive, nome exato)
  - um valor string que seja ou contenha um CPF válido (com ou sem máscara)

Falsos positivos são evitados:
  - 11 dígitos contínuos só contam se delimitados por não-alfanumérico
    E tiverem dígitos verificadores de CPF válidos (rejeita sequências repetidas).
  - Inteiros e floats não são verificados.
  - "name" NÃO está na lista proibida (agent.name legítimo no snapshot do M2).
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

# 2. CPF com máscara embutido em texto (delimitado por não-alfanumérico)
_CPF_MASKED_EMBEDDED = re.compile(
    r"(?<![0-9A-Za-z])\d{3}\.\d{3}\.\d{3}-\d{2}(?![0-9A-Za-z])"
)

# 3. 11 dígitos contínuos delimitados por não-alfanumérico
_CPF_DIGITS_EMBEDDED = re.compile(
    r"(?<![0-9A-Za-z])(\d{11})(?![0-9A-Za-z])"
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
    # A regex garante o formato; não exige dígitos verificadores válidos.
    if _CPF_FULL.match(stripped):
        raise PIIViolation(path, "valor é um CPF (com ou sem máscara)")

    # Regra 2: CPF com máscara embutido em texto (delimitado por não-alfanumérico).
    if _CPF_MASKED_EMBEDDED.search(value):
        raise PIIViolation(path, "CPF mascarado embutido no texto")

    # Regra 3: 11 dígitos contínuos embutidos, delimitados e com check digits válidos.
    # Inteiros de 11 dígitos em hexadecimais/IDs não ativam esta regra.
    for m in _CPF_DIGITS_EMBEDDED.finditer(value):
        digits = m.group(1)
        if _is_valid_cpf(digits):
            raise PIIViolation(path, "CPF (11 dígitos) embutido no texto")


# ---------------------------------------------------------------------------
# Varredura recursiva
# ---------------------------------------------------------------------------

def _scan(node: object, path: str) -> None:
    if isinstance(node, dict):
        for key, val in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            # Verificar chave proibida
            if isinstance(key, str) and key.lower() in _FORBIDDEN_KEYS:
                raise PIIViolation(child_path, f"chave proibida: {key!r}")
            _scan(val, child_path)
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            _scan(item, f"{path}[{i}]")
    elif isinstance(node, str):
        _check_string(node, path)
    # int, float, bool, None → não verificados


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
