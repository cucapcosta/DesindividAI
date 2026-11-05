"""Fakes compartilhados pelos testes — nenhum teste chama serviços reais."""

from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ToolCallingFakeModel(BaseChatModel):
    """Fake do Granite: devolve as mensagens roteirizadas e aceita bind_tools."""

    messages: Any
    model_id: Optional[str] = None

    @property
    def _llm_type(self) -> str:
        return "fake-granite"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=next(self.messages))])

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def happy_path_granite(consent: dict) -> ToolCallingFakeModel:
    """Granite que chama as 4 tools na ordem esperada pelo system prompt."""
    return ToolCallingFakeModel(messages=iter([
        tool_call("get_open_finance_debts", {"consent_id": consent["consent_id"]}, "1"),
        tool_call("get_serasa_data", {"pseudonym_id": consent["pseudonym_id"]}, "2"),
        tool_call("calculate_cet", {}, "3"),
        tool_call("persist_snapshot", {}, "4"),
        AIMessage(content="Snapshot gravado."),
    ]))
