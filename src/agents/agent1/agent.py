"""
Orquestração do Agente 1 — LangChain AgentExecutor + Granite (watsonx.ai).

O Granite conduz as 4 tools via tool calling. Não há fallback: se o modelo
não completar a sequência (ou o watsonx.ai falhar), a execução é abortada com
AgentExecutionError — um snapshot só existe se o agente o gerou.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.core.config import WATSONX_VARS, env, require_env, watsonx_scope

from . import tools
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .repository import Repository, get_repository
from .sources import (
    OpenFinanceClient,
    SerasaClient,
    build_open_finance_client,
    build_serasa_client,
)
from .tools import RunContext

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "ibm/granite-4-h-small"
MAX_ITERATIONS = 10  # 4 tools + 1 correção por tool + resposta final


class AgentExecutionError(RuntimeError):
    """O Granite não completou a sequência de tools."""


def build_llm():
    from langchain_ibm import ChatWatsonx

    require_env(*WATSONX_VARS)
    return ChatWatsonx(
        model_id=env("WATSONX_MODEL_ID", DEFAULT_MODEL_ID),
        url=env("WATSONX_URL"),
        apikey=env("IBM_CLOUD_API_KEY"),
        **watsonx_scope(),
        # Temperatura 0: o agente só orquestra tools; queremos a mesma
        # sequência de chamadas para a mesma entrada (auditabilidade).
        params={"temperature": 0, "max_tokens": 512},
    )


def _execute(ctx: RunContext, llm) -> None:
    """Roda o AgentExecutor; falha se o Granite não chegar ao persist_snapshot."""
    from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", USER_PROMPT_TEMPLATE),
        ("placeholder", "{agent_scratchpad}"),
    ])
    agent_tools = tools.build_langchain_tools(ctx)
    executor = AgentExecutor(
        agent=create_tool_calling_agent(llm, agent_tools, prompt),
        tools=agent_tools, max_iterations=MAX_ITERATIONS,
        handle_parsing_errors=True, verbose=False,
    )
    result = executor.invoke({"consent_id": ctx.consent_id, "pseudonym_id": ctx.pseudonym_id})
    logger.info("agent1 concluiu: %s", result.get("output"))
    if ctx.snapshot is None:
        raise AgentExecutionError(
            "O Granite terminou sem persistir o snapshot. "
            f"Tools executadas: {[c['tool'] for c in ctx.tool_calls]}"
        )


def run_agent1(
    consent: dict,
    *,
    repository: Optional[Repository] = None,
    open_finance: Optional[OpenFinanceClient] = None,
    serasa: Optional[SerasaClient] = None,
    llm=None,
    audit=None,
) -> dict:
    """Executa o Agente 1 e devolve o snapshot persistido (fonte da resposta da API)."""
    # M4: se audit não for injetado, tenta get_audit_logger(); falha → None + warning.
    if audit is None:
        try:
            from src.privacy.audit import get_audit_logger
            audit = get_audit_logger()
        except Exception as exc:
            logger.warning(
                "run_agent1: auditoria indisponível, continuando sem ela. exc=%s",
                type(exc).__name__,
            )
            audit = None

    llm = llm or build_llm()
    ctx = RunContext(
        consent=consent,
        repository=repository or get_repository(),
        open_finance=open_finance or build_open_finance_client(),
        serasa=serasa or build_serasa_client(),
        # Registra o modelo que de fato rodou (o injetado, se houver).
        model_id=getattr(llm, "model_id", None),
        audit=audit,
    )
    _execute(ctx, llm)
    return ctx.snapshot
