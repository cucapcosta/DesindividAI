"""
Fixtures compartilhadas pelos testes de M4.
"""

from __future__ import annotations

import pytest

from src.agents.agent1.repository import InMemoryRepository
from src.agents.agent1.sandbox_data import SANDBOX_CPF, sandbox_consents
from src.agents.agent1.sources import SandboxOpenFinanceClient, SandboxSerasaClient
from src.privacy.pseudonymization import pseudonymize_cpf
from tests.fakes import happy_path_granite


@pytest.fixture
def snapshot_from_m2():
    """
    Gera um snapshot real usando os fakes do M2 (mesma lógica de test_agent1).
    Garante que o snapshot é idêntico ao que o Agente 1 produz em produção.
    """
    from src.agents.agent1 import run_agent1

    pseudonym_id = pseudonymize_cpf(SANDBOX_CPF)
    consent = sandbox_consents(pseudonym_id)[0]
    repo = InMemoryRepository([consent])
    llm = happy_path_granite(consent)
    snapshot = run_agent1(
        consent,
        repository=repo,
        llm=llm,
        open_finance=SandboxOpenFinanceClient(),
        serasa=SandboxSerasaClient(),
    )
    return snapshot


@pytest.fixture
def random_cpfs():
    """
    Retorna 200 CPFs aleatórios válidos para testar pseudonimização.
    Gerados de forma determinística para reprodutibilidade.
    """
    import random

    rng = random.Random(42)

    def _gen_cpf() -> str:
        """Gera um CPF aleatório com dígitos verificadores corretos."""
        while True:
            digits = [rng.randint(0, 9) for _ in range(9)]
            # Rejeitar sequências repetidas
            if len(set(digits)) == 1:
                continue

            # Primeiro dígito verificador
            total = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
            rem = (total * 10) % 11
            d1 = rem if rem < 10 else 0
            digits.append(d1)

            # Segundo dígito verificador
            total2 = sum(d * w for d, w in zip(digits, range(11, 1, -1)))
            rem2 = (total2 * 10) % 11
            d2 = rem2 if rem2 < 10 else 0
            digits.append(d2)

            cpf = "".join(str(d) for d in digits)
            if len(set(cpf)) > 1:  # última verificação
                return cpf

    return [_gen_cpf() for _ in range(200)]


@pytest.fixture
def audit_log_lines():
    """Captura as linhas do logger desendividai.audit (propagate=False)."""
    import logging

    from src.privacy.audit import AUDIT_EVENT_LOGGER_NAME

    lines: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = _Collector(level=logging.INFO)
    event_logger = logging.getLogger(AUDIT_EVENT_LOGGER_NAME)
    event_logger.addHandler(handler)
    try:
        yield lines
    finally:
        event_logger.removeHandler(handler)


@pytest.fixture
def pseudonym_v2(monkeypatch):
    """Adiciona a versão v2 da chave de pseudonimização (v1 continua ativa).

    Para rotacionar no meio do teste: monkeypatch.setenv(
    "PSEUDONYM_KEY_ACTIVE_VERSION", "v2") seguido de pm.reset_key_cache().
    """
    from src.privacy import pseudonymization as pm

    monkeypatch.setenv("PSEUDONYM_HMAC_KEY_V2", "ef" * 32)
    monkeypatch.setenv("PSEUDONYM_KEY_ACTIVE_VERSION", "v1")
    pm.reset_key_cache()
    yield pm
    pm.reset_key_cache()
