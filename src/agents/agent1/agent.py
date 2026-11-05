"""
Orquestração do Agente 1 — LangChain AgentExecutor + Granite (watsonx.ai).

O Granite conduz as 4 tools via tool calling. Não há fallback: se o modelo
não completar a sequência (ou o watsonx.ai falhar), a execução é abortada com
AgentExecutionError — um snapshot só existe se o agente o gerou.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from . import tools
from .config import WATSONX_VARS, require_env
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
        model_id=os.getenv("WATSONX_MODEL_ID", DEFAULT_MODEL_ID),
        url=os.environ["WATSONX_URL"],
        project_id=os.environ["WATSONX_PROJECT_ID"],
        apikey=os.environ["IBM_CLOUD_API_KEY"],
        # Temperatura 0: o agente só orquestra tools; queremos a mesma
        # sequência de chamadas para a mesma entrada (auditabilidade).
        params={"temperature": 0, "max_tokens": 512},
    )


def build_agent_executor(ctx: RunContext, llm=None):
    from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", USER_PROMPT_TEMPLATE),
        ("placeholder", "{agent_scratchpad}"),
    ])
    agent_tools = tools.build_langchain_tools(ctx)
    agent = create_tool_calling_agent(llm or build_llm(), agent_tools, prompt)
    return AgentExecutor(
        agent=agent, tools=agent_tools, max_iterations=MAX_ITERATIONS,
        handle_parsing_errors=True, verbose=False,
    )


def run_with_llm(ctx: RunContext, llm=None) -> None:
    executor = build_agent_executor(ctx, llm)
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
) -> dict:
    """Executa o Agente 1 e devolve o snapshot persistido (fonte da resposta da API)."""
    ctx = RunContext(
        consent=consent,
        repository=repository or get_repository(),
        open_finance=open_finance or build_open_finance_client(),
        serasa=serasa or build_serasa_client(),
        model_id=os.getenv("WATSONX_MODEL_ID", DEFAULT_MODEL_ID),
    )
    run_with_llm(ctx, llm)
    return ctx.snapshot
