"""
Testa que o cache por event loop de get_llm/build_llm funciona:
- await get_llm() duas vezes no mesmo loop → mesmo objeto retornado.
- _build_llm_instance() é chamado exatamente uma vez.

Sem rede: _build_llm_instance é monkeypatchado para um fake contador.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from langchain_core.language_models.fake_chat_models import FakeListChatModel

import src.agents.recommendation_agent as _mod
from src.agents.recommendation_agent import get_llm


def test_get_llm_cache_same_loop(monkeypatch):
    """
    Dentro de um único asyncio.run(), get_llm() deve retornar o mesmo objeto
    nas duas chamadas e _build_llm_instance deve ter sido chamado apenas 1 vez.
    """
    fake_instance = FakeListChatModel(responses=["ok"])
    call_count = 0

    def fake_build() -> FakeListChatModel:
        nonlocal call_count
        call_count += 1
        return fake_instance

    # Limpa cache antes para garantir estado limpo entre testes.
    _mod.clear_llm_cache()

    with patch.object(_mod, "_build_llm_instance", side_effect=fake_build):

        async def _run() -> tuple:
            first = await get_llm()
            second = await get_llm()
            return first, second

        first, second = asyncio.run(_run())

    assert first is second, "get_llm deve retornar o mesmo objeto no mesmo event loop"
    assert call_count == 1, f"_build_llm_instance deve ser chamado 1 vez, foi chamado {call_count}"


def test_get_llm_different_loops(monkeypatch):
    """
    (a) Dois asyncio.run distintos chamando await get_llm() devem retornar
    objetos diferentes e o construtor deve ter sido chamado exatamente 2 vezes
    (um por loop).
    """
    call_count = 0
    instances = []

    def fake_build():
        nonlocal call_count
        inst = FakeListChatModel(responses=[f"resp-{call_count}"])
        call_count += 1
        instances.append(inst)
        return inst

    _mod.clear_llm_cache()

    with patch.object(_mod, "_build_llm_instance", side_effect=fake_build):
        first = asyncio.run(get_llm())
        second = asyncio.run(get_llm())

    _mod.clear_llm_cache()

    assert first is not second, "Loops distintos devem gerar objetos distintos"
    assert call_count == 2, f"_build_llm_instance deve ser chamado 2 vezes, foi chamado {call_count}"


def test_get_llm_error_not_cached(monkeypatch):
    """
    (b) Construtor que levanta ServiceError na 1ª chamada e devolve fake na 2ª,
    no MESMO loop:
    - 1ª await get_llm() deve levantar ServiceError com status 503.
    - 2ª await get_llm() deve devolver o fake (falha não ficou em cache).
    """
    from src.core.errors import ServiceError

    fake_instance = FakeListChatModel(responses=["ok"])
    call_count = 0

    def fake_build():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ServiceError(503, "SERVICE_UNAVAILABLE", "x")
        return fake_instance

    _mod.clear_llm_cache()

    with patch.object(_mod, "_build_llm_instance", side_effect=fake_build):

        async def _run():
            with __import__("pytest").raises(ServiceError) as exc_info:
                await get_llm()
            assert exc_info.value.status_code == 503
            second = await get_llm()
            return second

        result = asyncio.run(_run())

    _mod.clear_llm_cache()

    assert result is fake_instance, "2ª chamada deve retornar o fake após falha"
    assert call_count == 2, f"Construtor deve ter sido chamado 2 vezes, foi {call_count}"


def test_build_llm_no_loop_no_cache(monkeypatch):
    """
    (c) build_llm() chamado fora de qualquer loop (sem asyncio.run):
    - Constrói sem cache: duas chamadas ao build_llm() → construtor chamado 2 vezes.
    """
    from src.agents.recommendation_agent import build_llm

    call_count = 0

    def fake_build():
        nonlocal call_count
        call_count += 1
        return FakeListChatModel(responses=[f"resp-{call_count}"])

    _mod.clear_llm_cache()

    with patch.object(_mod, "_build_llm_instance", side_effect=fake_build):
        r1 = build_llm()
        r2 = build_llm()

    _mod.clear_llm_cache()

    assert call_count == 2, f"Sem loop, build_llm não deve usar cache; esperado 2, obtido {call_count}"
    assert r1 is not r2, "Sem loop, cada chamada deve retornar instância nova"
