"""
Testes unitários para src/privacy/pii_guard.py — Fase 4 do M4.

Todos os testes rodam sem rede.
"""

from __future__ import annotations

import pytest

from src.privacy.pii_guard import PIIViolation, assert_no_pii

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_CPF = "123.456.789-09"          # 12345678909 — dígitos verificadores OK
_VALID_CPF_DIGITS = "12345678909"


def _raises_pii(doc, *, path_contains: str | None = None) -> PIIViolation:
    """Garante que assert_no_pii levanta PIIViolation e retorna a exceção."""
    with pytest.raises(PIIViolation) as exc_info:
        assert_no_pii(doc)
    exc = exc_info.value
    if path_contains is not None:
        assert path_contains in exc.path, (
            f"path esperado conter {path_contains!r}, mas foi {exc.path!r}"
        )
    return exc


# ---------------------------------------------------------------------------
# 1. Snapshot real do M2 passa sem PIIViolation
# ---------------------------------------------------------------------------

def test_real_m2_snapshot_passes(snapshot_from_m2):
    """Um snapshot gerado pelos fakes do M2 deve passar pela guarda de PII."""
    assert_no_pii(snapshot_from_m2)   # não deve levantar


def test_pseudonym_ids_in_snapshot_pass(snapshot_from_m2):
    """pseudonym_id, debt_id e contract_ref são hexadecimais — não são CPF."""
    # Garante que o snapshot tem esses campos antes de testar
    assert "pseudonym_id" in snapshot_from_m2
    assert len(snapshot_from_m2["debts"]) > 0
    first_debt = snapshot_from_m2["debts"][0]
    assert "debt_id" in first_debt
    assert "contract_ref" in first_debt
    # Se passou acima, esses campos não disparam a guarda
    assert_no_pii(snapshot_from_m2)


# ---------------------------------------------------------------------------
# 2. 200 pseudonym_ids aleatórios não disparam a guarda
# ---------------------------------------------------------------------------

def test_random_pseudonym_ids_do_not_trigger(random_cpfs):
    """pseudonymize_cpf de CPFs aleatórios produz hashes hex que não são CPF."""
    from src.privacy.pseudonymization import pseudonymize_cpf

    for cpf in random_cpfs:
        pid = pseudonymize_cpf(cpf)
        assert_no_pii({"pseudonym_id": pid})  # não deve levantar


# ---------------------------------------------------------------------------
# 3. CPF como valor (string) dispara
# ---------------------------------------------------------------------------

def test_cpf_value_with_mask_raises():
    exc = _raises_pii({"doc": _VALID_CPF}, path_contains="doc")
    assert _VALID_CPF_DIGITS not in str(exc)  # mensagem não contém o CPF


def test_cpf_value_without_mask_raises():
    exc = _raises_pii({"doc": _VALID_CPF_DIGITS}, path_contains="doc")
    assert _VALID_CPF_DIGITS not in str(exc)


def test_cpf_with_partial_mask_raises():
    # Só pontos (sem traço): 123.456.789-09 sem traço = 123.456.78909
    # A regex exata exige XXX.XXX.XXX-XX com traço para "embutido com máscara".
    # Para o formato sem traço: bate em _CPF_FULL (^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$)
    exc = _raises_pii({"cpf_field": "123.456.78909"})
    assert _VALID_CPF_DIGITS not in str(exc)


# ---------------------------------------------------------------------------
# 4. CPF embutido em texto dispara
# ---------------------------------------------------------------------------

def test_cpf_masked_embedded_in_text_raises():
    text = f"Titular: {_VALID_CPF} solicitou renegociação."
    exc = _raises_pii({"notes": text}, path_contains="notes")
    assert _VALID_CPF_DIGITS not in str(exc)
    assert _VALID_CPF not in str(exc)


def test_cpf_digits_embedded_in_text_raises():
    # Use a CPF with valid check digits (different from _VALID_CPF_DIGITS which may not be valid)
    valid_cpf_digits = "52998224725"  # CPF with valid check digits
    text = f"id={valid_cpf_digits} aprovado"
    exc = _raises_pii({"notes": text}, path_contains="notes")
    assert valid_cpf_digits not in str(exc)


# ---------------------------------------------------------------------------
# 5. Hex e outros números não disparam
# ---------------------------------------------------------------------------

def test_hex_string_does_not_trigger():
    """Hashes hexadecimais com 64 chars nunca contêm um CPF delimitado."""
    import secrets
    for _ in range(50):
        hex_id = secrets.token_hex(32)  # 64 chars hex
        assert_no_pii({"pseudonym_id": hex_id})


def test_invalid_cpf_digits_do_not_trigger():
    """11 dígitos seguidos com verificadores inválidos não disparam."""
    # 00000000000 é repetido → rejeitado pelo _is_valid_cpf
    assert_no_pii({"value": "x00000000000x"})


def test_repeated_digit_cpf_embedded_does_not_trigger():
    """Sequências repetidas embutidas em texto (Regra 3) não ativam a guarda.
    Nota: como valor isolado (Regra 1), 11 dígitos iguais SÃO tratados como CPF suspeito.
    """
    for d in "0123456789":
        # Embutido em texto — apenas a Regra 3 se aplica; check digits devem ser válidos.
        text = f"ref-{d * 11}-ok"
        assert_no_pii({"v": text})


def test_integer_value_does_not_trigger():
    """Inteiros não são verificados."""
    assert_no_pii({"balance": 12345678909})


def test_float_value_does_not_trigger():
    """Floats não são verificados."""
    assert_no_pii({"rate": 123.45})


# ---------------------------------------------------------------------------
# 6. Chave proibida dispara
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "cpf", "CPF", "Cpf",
    "cpf_number",
    "nome", "Nome",
    "nome_completo",
    "full_name",
    "customer_name",
    "email", "EMAIL",
    "e_mail",
    "telefone",
    "phone",
    "celular",
    "endereco",
    "address",
    "contractid", "contractId", "ContractID",
    "contract_id",
    "contract_number",
    "numero_contrato",
    "rg",
])
def test_forbidden_key_raises(key):
    # path stores the actual key name (preserving case); check case-insensitively
    exc = _raises_pii({key: "qualquer valor"})
    assert exc.path != ""
    assert exc.path.lower() == key.lower(), (
        f"path {exc.path!r} should match key {key!r} (case-insensitive)"
    )


def test_name_key_does_not_trigger():
    """'name' NÃO está na lista proibida (agent.name legítimo no snapshot do M2)."""
    assert_no_pii({"agent": {"name": "agent1-analysis-prioritization"}})


# ---------------------------------------------------------------------------
# 7. Aninhamento: lista e dict profundos disparam com path correto
# ---------------------------------------------------------------------------

def test_forbidden_key_nested_in_list_raises():
    doc = {"debts": [{"institution": "itau"}, {"cpf": "anything"}]}
    exc = _raises_pii(doc)
    assert "debts" in exc.path
    assert "[1]" in exc.path
    assert "cpf" in exc.path


def test_forbidden_key_deeply_nested_raises():
    doc = {"a": {"b": {"c": {"email": "x@x.com"}}}}
    exc = _raises_pii(doc, path_contains="email")
    assert "a" in exc.path
    assert "b" in exc.path
    assert "c" in exc.path


def test_cpf_value_in_list_raises():
    doc = {"ids": ["ok1", "ok2", _VALID_CPF]}
    exc = _raises_pii(doc, path_contains="[2]")
    assert _VALID_CPF_DIGITS not in str(exc)


def test_cpf_value_nested_in_dict_raises():
    doc = {"debts": [{"notes": f"cpf: {_VALID_CPF}"}]}
    exc = _raises_pii(doc)
    assert "debts" in exc.path
    assert "[0]" in exc.path
    assert "notes" in exc.path
    assert _VALID_CPF not in str(exc)


# ---------------------------------------------------------------------------
# 8. Mensagem da exceção não contém o CPF
# ---------------------------------------------------------------------------

def test_exception_message_never_contains_cpf_masked():
    exc = _raises_pii({"campo": _VALID_CPF})
    assert _VALID_CPF not in str(exc)
    assert _VALID_CPF_DIGITS not in str(exc)


def test_exception_message_never_contains_cpf_digits():
    exc = _raises_pii({"campo": _VALID_CPF_DIGITS})   # triggers via rule 1 (format match)
    assert _VALID_CPF_DIGITS not in str(exc)


def test_exception_message_never_contains_cpf_embedded():
    text = f"O cpf é {_VALID_CPF} do cliente"
    exc = _raises_pii({"notes": text})
    assert _VALID_CPF not in str(exc)
    assert _VALID_CPF_DIGITS not in str(exc)


# ---------------------------------------------------------------------------
# 9. Tuplas são varridas como listas
# ---------------------------------------------------------------------------

def test_tuple_is_scanned():
    doc = {"items": ("ok", _VALID_CPF)}
    exc = _raises_pii(doc, path_contains="[1]")
    assert _VALID_CPF_DIGITS not in str(exc)


# ---------------------------------------------------------------------------
# 10. Documento limpo retorna None
# ---------------------------------------------------------------------------

def test_clean_document_returns_none():
    doc = {
        "type": "financial_snapshot",
        "pseudonym_id": "a" * 64,
        "debts": [
            {"institution": "itau", "outstanding_balance": 1000.0, "contract_ref": "b" * 32}
        ],
    }
    result = assert_no_pii(doc)
    assert result is None

# ---------------------------------------------------------------------------
# 11. CPFs válidos cujo cálculo de algum DV resulta em resto 10 (→ dígito 0)
# ---------------------------------------------------------------------------

def _ref_cpf(base9: str) -> str:
    """
    Função de referência para geração de CPF.
    Recebe os 9 primeiros dígitos (string) e retorna o CPF completo de 11 dígitos.
    Regra Receita Federal: se (soma*10) % 11 == 10, o dígito verificador é 0.
    """
    digits = [int(d) for d in base9]

    total = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
    rem = (total * 10) % 11
    d1 = 0 if rem >= 10 else rem
    digits.append(d1)

    total2 = sum(d * w for d, w in zip(digits, range(11, 1, -1)))
    rem2 = (total2 * 10) % 11
    d2 = 0 if rem2 >= 10 else rem2
    digits.append(d2)

    return "".join(str(d) for d in digits)


def _find_cpf_with_rem10(which_digit: int, count: int = 5) -> list[str]:
    """
    Encontra CPFs válidos onde o dígito `which_digit` (1 ou 2) tem resto 10.
    Itera bases fixas sequencialmente a partir de 100000000 para determinismo.
    """
    results: list[str] = []
    n = 100_000_000
    while len(results) < count:
        base9 = str(n).zfill(9)[:9]
        # Skip all-same-digit sequences
        if len(set(base9)) == 1:
            n += 1
            continue
        digits = [int(d) for d in base9]
        if which_digit == 1:
            total = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
            rem = (total * 10) % 11
            if rem == 10:
                cpf = _ref_cpf(base9)
                if len(set(cpf)) > 1:
                    results.append(cpf)
        else:
            total = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
            rem = (total * 10) % 11
            d1 = 0 if rem >= 10 else rem
            digits_10 = digits + [d1]
            total2 = sum(d * w for d, w in zip(digits_10, range(11, 1, -1)))
            rem2 = (total2 * 10) % 11
            if rem2 == 10:
                cpf = _ref_cpf(base9)
                if len(set(cpf)) > 1:
                    results.append(cpf)
        n += 1
    return results


# Build parametrize list at module load time (deterministic, no I/O)
_CPF_REM10_D1 = _find_cpf_with_rem10(which_digit=1, count=5)
_CPF_REM10_D2 = _find_cpf_with_rem10(which_digit=2, count=5)
_CPF_REM10_ALL = [(c, "d1_rem10") for c in _CPF_REM10_D1] + [
    (c, "d2_rem10") for c in _CPF_REM10_D2
]


@pytest.mark.parametrize("cpf,label", _CPF_REM10_ALL)
def test_valid_cpf_remainder10_is_accepted(cpf, label):
    """CPFs onde algum DV tem resto 10 (→ dígito 0) devem ser aceitos por _is_valid_cpf."""
    from src.privacy.pii_guard import _is_valid_cpf

    assert _is_valid_cpf(cpf) is True, (
        f"[{label}] CPF {cpf} deveria ser válido mas foi rejeitado"
    )


@pytest.mark.parametrize("cpf,label", _CPF_REM10_ALL)
def test_cpf_remainder10_embedded_raises(cpf, label):
    """CPFs com DV de resto 10, embutidos em texto, devem levantar PIIViolation."""
    exc = _raises_pii({"obs": f"cliente {cpf} ligou"}, path_contains="obs")
    assert cpf not in str(exc), "mensagem não deve conter o CPF"


# ---------------------------------------------------------------------------
# 12. Teste de propriedade: 2000 CPFs gerados com semente fixa → todos válidos
# ---------------------------------------------------------------------------

def test_property_2000_ref_cpfs_all_valid():
    """2000 CPFs gerados com _ref_cpf (seed 0) são aceitos por _is_valid_cpf."""
    import random
    from src.privacy.pii_guard import _is_valid_cpf

    rng = random.Random(0)
    invalid: list[str] = []
    generated = 0
    while generated < 2000:
        base9 = "".join(str(rng.randint(0, 9)) for _ in range(9))
        if len(set(base9)) == 1:
            continue
        cpf = _ref_cpf(base9)
        if len(set(cpf)) == 1:
            continue
        generated += 1
        if not _is_valid_cpf(cpf):
            invalid.append(cpf)

    assert invalid == [], (
        f"{len(invalid)} CPFs gerados com _ref_cpf foram rejeitados por _is_valid_cpf: "
        f"{invalid[:5]}"
    )


# ---------------------------------------------------------------------------
# 13. CPF separado por espaço (Fase B2)
# ---------------------------------------------------------------------------

# CPF 529.982.247-25 (52998224725) é válido — verificado pelos DVs.
_CPF_SPACED = "529 982 247 25"      # separadores: espaço
_CPF_SPACED_DIGITS = "52998224725"

def test_cpf_space_separated_in_text_raises():
    """'CPF: 529 982 247 25' deve disparar PIIViolation."""
    text = f"CPF: {_CPF_SPACED} do cliente"
    exc = _raises_pii({"notes": text}, path_contains="notes")
    assert _CPF_SPACED_DIGITS not in str(exc)


def test_cpf_mixed_sep_dot_space_raises():
    """'529.982.247 25' (terceiro sep = espaço) deve disparar."""
    text = f"documento 529.982.247 25 registrado"
    exc = _raises_pii({"obs": text}, path_contains="obs")
    assert _CPF_SPACED_DIGITS not in str(exc)


def test_cpf_mixed_sep_space_hyphen_raises():
    """'529 982 247-25' (primeiro sep = espaço, terceiro = hífen) deve disparar."""
    text = f"ref 529 982 247-25 ok"
    exc = _raises_pii({"obs": text}, path_contains="obs")
    assert _CPF_SPACED_DIGITS not in str(exc)


def test_monetary_value_does_not_trigger():
    """'R$ 529 982 247,25' não deve disparar — último sep é vírgula, não dígito."""
    assert_no_pii({"valor": "R$ 529 982 247,25"})


def test_space_separated_invalid_check_digits_does_not_trigger():
    """'parcelas 123 456 789 00' — DVs inválidos, não deve disparar."""
    assert_no_pii({"info": "parcelas 123 456 789 00"})


def test_digit_before_space_cpf_does_not_trigger():
    """'0 529 982 247 25 1' — dígito colado via espaço antes e depois, não deve disparar."""
    assert_no_pii({"val": "0 529 982 247 25 1"})


def test_monetary_common_does_not_trigger():
    """Valores monetários comuns não disparam."""
    assert_no_pii({"saldo": "R$ 56.667,67"})


def test_uuid_does_not_trigger():
    """UUIDs não disparam a guarda."""
    import uuid
    for _ in range(20):
        assert_no_pii({"id": str(uuid.uuid4())})
