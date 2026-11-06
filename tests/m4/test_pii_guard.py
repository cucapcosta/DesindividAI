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
    """Inteiro com DV válido (CPF) levanta; int com DV inválido e ints comuns não levantam."""
    # 12345678909 (SANDBOX_CPF como int) tem DVs válidos → deve levantar
    with pytest.raises(PIIViolation):
        assert_no_pii({"balance": 12345678909})

    # Ints comuns (não são CPF) → não levantam
    assert_no_pii({"count": 1500})
    assert_no_pii({"year": 2026})
    assert_no_pii({"ref": 63914})
    assert_no_pii({"small": 0})
    assert_no_pii({"negative": -1})  # negativos nunca são CPF (str começa com '-')


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


# ---------------------------------------------------------------------------
# 14. R-05 — CPF como chave de dict dispara; path não contém o CPF
# ---------------------------------------------------------------------------

_CPF_KEY = "529.982.247-25"   # CPF mascarado válido
_CPF_KEY_DIGITS = "52998224725"


def test_cpf_as_dict_key_raises():
    """CPF válido como chave de dict deve levantar PIIViolation."""
    doc = {_CPF_KEY: 1}
    exc = _raises_pii(doc)
    # A violação deve ter sido detectada
    assert exc is not None


def test_cpf_as_dict_key_path_does_not_contain_cpf():
    """path da violação não pode conter o CPF (mascarado ou em dígitos)."""
    doc = {_CPF_KEY: 1}
    exc = _raises_pii(doc)
    assert _CPF_KEY not in exc.path, f"path contém CPF mascarado: {exc.path!r}"
    assert _CPF_KEY_DIGITS not in exc.path, f"path contém CPF em dígitos: {exc.path!r}"
    assert "<cpf>" in exc.path, f"path deveria conter '<cpf>': {exc.path!r}"


def test_cpf_as_dict_key_path_does_not_contain_cpf_in_message():
    """str(exc) não pode conter o CPF."""
    doc = {_CPF_KEY: 1}
    exc = _raises_pii(doc)
    assert _CPF_KEY not in str(exc)
    assert _CPF_KEY_DIGITS not in str(exc)


def test_cpf_digits_as_dict_key_raises():
    """CPF em dígitos puro como chave de dict deve levantar PIIViolation."""
    doc = {_CPF_KEY_DIGITS: "valor qualquer"}
    exc = _raises_pii(doc)
    assert _CPF_KEY_DIGITS not in exc.path
    assert "<cpf>" in exc.path


def test_value_with_cpf_under_cpf_key_path_clean():
    """
    Valor com CPF sob chave com CPF → deve levantar (pela chave primeiro);
    path e mensagem não podem conter o CPF.
    """
    doc = {_CPF_KEY: _CPF_KEY}
    exc = _raises_pii(doc)
    assert _CPF_KEY not in exc.path
    assert _CPF_KEY_DIGITS not in exc.path
    assert _CPF_KEY not in str(exc)
    assert _CPF_KEY_DIGITS not in str(exc)


def test_cpf_as_key_nested_raises_and_path_masked():
    """CPF como chave em dict aninhado → path mascarado."""
    doc = {"outer": {_CPF_KEY_DIGITS: "val"}}
    exc = _raises_pii(doc)
    assert _CPF_KEY_DIGITS not in exc.path
    assert "<cpf>" in exc.path
    assert "outer" in exc.path


# ---------------------------------------------------------------------------
# R-11 — Varredura determinística em massa
# ---------------------------------------------------------------------------

import time
import uuid as _uuid_mod


def _gen_valid_cpf_list(rng, count: int) -> list[str]:
    """Gera `count` CPFs válidos com semente fixa (determinístico)."""
    result: list[str] = []
    while len(result) < count:
        base9 = "".join(str(rng.randint(0, 9)) for _ in range(9))
        if len(set(base9)) == 1:
            continue
        cpf = _ref_cpf(base9)
        if len(set(cpf)) == 1:
            continue
        result.append(cpf)
    return result


def _fmt_masked(digits: str) -> str:
    """000.000.000-00"""
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def _fmt_spaced(digits: str) -> str:
    """000 000 000 00"""
    return f"{digits[:3]} {digits[3:6]} {digits[6:9]} {digits[9:]}"


def _fmt_nbsp(digits: str) -> str:
    """000\u00a0000\u00a0000\u00a000 (non-breaking spaces)"""
    return f"{digits[:3]}\u00a0{digits[3:6]}\u00a0{digits[6:9]}\u00a0{digits[9:]}"


def test_mass_scan_cpf_detection():
    """
    R-11: varredura determinística em massa.

    - 20 000 CPFs válidos distribuídos em 4 formatos (5 000 por formato) embutidos em frases
      → assert_no_pii levanta PIIViolation em 100% dos casos.
    - 5 000 textos financeiros sem CPF válido
      → 0 falsos positivos.

    Deve rodar em < 5 s.
    """
    import random
    from src.privacy.pii_guard import PIIViolation, assert_no_pii

    rng = random.Random(42)

    # ---- Parte 1: 20 000 CPFs válidos, 4 formatos (5 000 por formato) ----
    N_CPF = 20_000
    cpfs = _gen_valid_cpf_list(rng, N_CPF)
    phrases = [
        "Titular: {cpf} solicitou renegociação.",
        "id={cpf} aprovado pelo sistema.",
        "contrato referente ao cpf {cpf} emitido.",
        "cliente {cpf} ligou ontem.",
    ]

    t0 = time.monotonic()

    false_negatives = 0
    for i, cpf in enumerate(cpfs):
        fmt_idx = i % 4
        if fmt_idx == 0:
            token = cpf            # 11 dígitos contínuos
        elif fmt_idx == 1:
            token = _fmt_masked(cpf)   # 000.000.000-00
        elif fmt_idx == 2:
            token = _fmt_spaced(cpf)   # 000 000 000 00
        else:
            token = _fmt_nbsp(cpf)     # NBSP

        text = phrases[fmt_idx].format(cpf=token)
        doc = {"notes": text}
        try:
            assert_no_pii(doc)
            false_negatives += 1  # should have raised
        except PIIViolation:
            pass  # expected

    # ---- Parte 2: 5 000 textos sem CPF ----
    N_CLEAN = 5_000
    false_positives = 0

    def _gen_invalid_dv_11digits(rng) -> str:
        """Gera uma string de 11 dígitos com DVs inválidos (garantido não ser CPF)."""
        while True:
            base9 = "".join(str(rng.randint(0, 9)) for _ in range(9))
            if len(set(base9)) == 1:
                continue
            # Calcular DVs corretos
            digits = [int(d) for d in base9]
            total = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
            rem = (total * 10) % 11
            d1_correct = 0 if rem >= 10 else rem
            # Inverter o último dígito para garantir DV inválido
            d1_wrong = (d1_correct + 1) % 10
            candidate = base9 + str(d1_wrong) + str(rng.randint(0, 9))
            if len(candidate) == 11 and len(set(candidate)) > 1:
                return candidate

    financial_templates = [
        "Saldo devedor: R$ {val1},{val2:02d} em {month}/{year}.",
        "Taxa de juros: {pct:.2f}% ao mês; prazo {prazo} meses.",
        "Parcela {n} de {total}: R$ {val1},{val2:02d}.",
        "UUID da operação: {uid}.",
        "Código de contrato: {code12}.",
        "Número de protocolo: {proto}.",
        "Valor total: R$ {big}.{val2:02d}; id={short}.",
        "Data de vencimento: {day:02d}/{month:02d}/{year}.",
        # R-18: 11 dígitos com DV inválido (não deve acionar)
        "Número de controle: {inv11} confirmado.",
        # R-18: número de telefone (não deve acionar)
        "Contato: (11) 91234-5678 para dúvidas.",
    ]

    for j in range(N_CLEAN):
        tmpl = financial_templates[j % len(financial_templates)]

        # Generate safe values (never a valid 11-digit CPF sequence)
        val1 = rng.randint(1, 999_999)
        val2 = rng.randint(0, 99)
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        year = rng.randint(2020, 2030)
        pct = rng.uniform(0.5, 5.0)
        prazo = rng.randint(1, 120)
        n = rng.randint(1, 60)
        total = rng.randint(n, 120)
        # 12+ digit number that is NOT a valid CPF (use 12 digits)
        code12 = "".join(str(rng.randint(0, 9)) for _ in range(12))
        proto = "".join(str(rng.randint(0, 9)) for _ in range(13))
        # 1–10 digit short number
        short = rng.randint(1, 9_999_999_999)
        # large amount
        big = rng.randint(10_000, 9_999_999)
        uid = str(_uuid_mod.UUID(int=rng.getrandbits(128)))
        # 11 dígitos com DV inválido
        inv11 = _gen_invalid_dv_11digits(rng)

        text = tmpl.format(
            val1=val1, val2=val2, month=month, day=day, year=year,
            pct=pct, prazo=prazo, n=n, total=total,
            code12=code12, proto=proto, short=short, big=big, uid=uid,
            inv11=inv11,
        )
        doc = {"info": text}
        try:
            assert_no_pii(doc)
        except PIIViolation:
            false_positives += 1

    elapsed = time.monotonic() - t0

    assert false_negatives == 0, (
        f"assert_no_pii falhou em detectar {false_negatives}/{N_CPF} CPFs válidos."
    )
    assert false_positives == 0, (
        f"assert_no_pii gerou {false_positives}/{N_CLEAN} falsos positivos."
    )
    assert elapsed < 5.0, (
        f"Teste demorou {elapsed:.2f}s (limite: 5s). "
        f"Tamanhos usados: CPFs={N_CPF} (5 000/formato), textos limpos={N_CLEAN}."
    )


# ---------------------------------------------------------------------------
# R-18(a) — CPF como int com DV válido → PIIViolation
# ---------------------------------------------------------------------------

_VALID_INT_CPF = 12345678909   # "12345678909" tem DVs válidos (SANDBOX_CPF)
_INVALID_INT_CPF = 12345678900  # DVs inválidos para estes dígitos


def test_int_cpf_valid_dv_raises():
    """int com DV válido (CPF) levanta PIIViolation."""
    from src.privacy.pii_guard import PIIViolation, assert_no_pii
    with pytest.raises(PIIViolation):
        assert_no_pii({"doc": _VALID_INT_CPF})


def test_int_cpf_invalid_dv_does_not_raise():
    """int de 11 dígitos com DV inválido NÃO levanta PIIViolation."""
    assert_no_pii({"doc": _INVALID_INT_CPF})


def test_int_cpf_bool_never_raises():
    """bool é subclasse de int mas NÃO deve ser verificado como CPF."""
    assert_no_pii({"flag": True})
    assert_no_pii({"flag": False})


def test_int_cpf_short_numbers_do_not_raise():
    """Ints comuns não são CPF."""
    for v in [0, 1, 100, 1500, 2026, 63914, 999999999]:
        assert_no_pii({"n": v})


def test_int_cpf_too_long_does_not_raise():
    """Int com 12+ dígitos não é CPF."""
    assert_no_pii({"n": 123456789009})  # 12 dígitos


def test_int_cpf_leading_zero_detected():
    """int 1234567890 → zfill(11) = '01234567890'; se DVs válidos → levanta."""
    from src.privacy.pii_guard import _is_valid_cpf
    # Verificar se 01234567890 tem DV válido
    if _is_valid_cpf("01234567890"):
        from src.privacy.pii_guard import PIIViolation, assert_no_pii
        with pytest.raises(PIIViolation):
            assert_no_pii({"n": 1234567890})
    else:
        assert_no_pii({"n": 1234567890})  # inválido → não levanta


# ---------------------------------------------------------------------------
# R-18(b) — Separadores "/" e "\t" aceitos entre grupos de CPF
# ---------------------------------------------------------------------------

_SLASH_CPF = "529/982/247/25"   # separador barra
_TAB_CPF = "529\t982\t247\t25"  # separador tab
_CPF_DIGITS_529 = "52998224725"


def test_cpf_slash_separator_raises():
    """CPF com separador '/' deve disparar PIIViolation."""
    text = f"cpf: {_SLASH_CPF} informado"
    with pytest.raises(PIIViolation):
        assert_no_pii({"obs": text})


def test_cpf_tab_separator_raises():
    """CPF com separador tab deve disparar PIIViolation."""
    text = f"cpf:\t{_TAB_CPF}\tconfirmado"
    with pytest.raises(PIIViolation):
        assert_no_pii({"obs": text})


def test_cpf_slash_separator_message_no_cpf():
    """Mensagem da exceção NÃO deve conter o CPF (separador /)."""
    from src.privacy.pii_guard import PIIViolation, assert_no_pii
    text = f"documento {_SLASH_CPF} registrado"
    with pytest.raises(PIIViolation) as exc_info:
        assert_no_pii({"obs": text})
    assert _CPF_DIGITS_529 not in str(exc_info.value)


# ---------------------------------------------------------------------------
# R-18(c) — CPF grudado em letra usa bordas por não-dígito
# ---------------------------------------------------------------------------

_CPF_DIGITS_STUCK = "52998224725"


def test_cpf_stuck_to_letters_prefix_raises():
    """'CPF52998224725' — CPF grudado em letras à esquerda → PIIViolation."""
    with pytest.raises(PIIViolation):
        assert_no_pii({"ref": f"CPF{_CPF_DIGITS_STUCK}"})


def test_cpf_stuck_to_letters_suffix_raises():
    """'id52998224725x' — CPF grudado em letras à direita → PIIViolation."""
    with pytest.raises(PIIViolation):
        assert_no_pii({"ref": f"id{_CPF_DIGITS_STUCK}x"})


def test_cpf_stuck_to_letters_both_sides_raises():
    """'cpf52998224725ok' — CPF grudado em letras dos dois lados → PIIViolation."""
    with pytest.raises(PIIViolation):
        assert_no_pii({"ref": f"cpf{_CPF_DIGITS_STUCK}ok"})


def test_cpf_stuck_message_no_cpf():
    """Mensagem da exceção não deve conter o CPF grudado em letras."""
    from src.privacy.pii_guard import PIIViolation, assert_no_pii
    with pytest.raises(PIIViolation) as exc_info:
        assert_no_pii({"ref": f"CPF{_CPF_DIGITS_STUCK}"})
    assert _CPF_DIGITS_STUCK not in str(exc_info.value)


def test_phone_number_does_not_trigger():
    """Número de telefone '(11) 91234-5678' NÃO é CPF e não deve disparar."""
    assert_no_pii({"phone_info": "(11) 91234-5678"})
    assert_no_pii({"notes": "ligue para (11) 91234-5678 agora"})


# ---------------------------------------------------------------------------
# R-18(d) — Regra 1: 11 dígitos com DV inválido NÃO é violação
# ---------------------------------------------------------------------------

def test_rule1_11digits_invalid_dv_no_violation():
    """11 dígitos contínuos com DV inválido (isolado como valor) → sem violação."""
    # Construir um CPF-like com DV errado
    base = "12345678900"  # DV inválido para 123456789
    assert_no_pii({"code": base})


def test_rule1_11digits_valid_dv_raises():
    """11 dígitos contínuos com DV válido (isolado) → PIIViolation (regra 1)."""
    with pytest.raises(PIIViolation):
        assert_no_pii({"code": "12345678909"})


def test_rule3_embedded_11digits_invalid_dv_no_violation():
    """11 dígitos embutidos em texto com DV inválido → sem violação (regra 3)."""
    assert_no_pii({"notes": "referência 12345678900 confirmada"})


def test_rule3_embedded_11digits_valid_dv_raises():
    """11 dígitos embutidos em texto com DV válido → PIIViolation (regra 3)."""
    with pytest.raises(PIIViolation):
        assert_no_pii({"notes": "referência 12345678909 confirmada"})



# ---------------------------------------------------------------------------
# R-25(2): CPFs com 1 DV trocado NÃO devem disparar (DV inválido)
# ---------------------------------------------------------------------------

# CPFs reais com 1 dígito verificador trocado — devem passar (DV inválido)
# 529.982.247-25 (válido) → trocar um DV: 529.982.247-35 ou 529.982.247-24
_CPF_VALID_529 = "52998224725"   # válido
_CPF_DV1_TROCADO = "52998224735"  # DV1 trocado: 7→3 (inválido)
_CPF_DV2_TROCADO = "52998224724"  # DV2 trocado: 5→4 (inválido)

# 123.456.789-09 (válido) → trocar DVs
_CPF_VALID_123 = "12345678909"   # válido
_CPF_123_DV1_TROCADO = "12345678919"   # DV1 trocado (inválido)
_CPF_123_DV2_TROCADO = "12345678908"   # DV2 trocado (inválido)


@pytest.mark.parametrize("cpf_with_wrong_dv", [
    _CPF_DV1_TROCADO,
    _CPF_DV2_TROCADO,
    _CPF_123_DV1_TROCADO,
    _CPF_123_DV2_TROCADO,
])
def test_cpf_with_one_dv_changed_does_not_raise(cpf_with_wrong_dv):
    """
    CPFs reais com exatamente 1 DV trocado NÃO devem acionar a guarda.
    (Diferença de sequência repetida: estes têm dígitos variados, só o DV está errado.)
    """
    from src.privacy.pii_guard import _is_valid_cpf
    # Confirmar que o CPF modificado é realmente inválido
    assert not _is_valid_cpf(cpf_with_wrong_dv), (
        f"{cpf_with_wrong_dv} deveria ser inválido (1 DV trocado)"
    )
    # Como valor isolado
    assert_no_pii({"code": cpf_with_wrong_dv})
    # Embutido em texto
    assert_no_pii({"notes": f"referência {cpf_with_wrong_dv} confirmada"})
    # Com máscara
    masked = f"{cpf_with_wrong_dv[:3]}.{cpf_with_wrong_dv[3:6]}.{cpf_with_wrong_dv[6:9]}-{cpf_with_wrong_dv[9:]}"
    assert_no_pii({"notes": f"documento {masked} registrado"})

