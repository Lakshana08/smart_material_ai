"""Shared helpers for the three A2A agent executors.

Input contract: if the caller sends a JSON DataPart matching the
capability's parameter shape (e.g. a Joule Studio skill invocation with
pre-extracted fields), dispatch directly to core_capabilities - no LLM
involved. If the caller sends plain free text instead, route through
services/ai_core.run_agent() with the capability's functions bound as
LangChain tools, and let the model decide what to call.
"""

from typing import Any

from a2a.helpers import get_data_parts, new_data_part, new_message, new_text_part
from a2a.server.agent_execution import RequestContext
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol


def get_structured_input(context: RequestContext) -> dict[str, Any] | None:
    """Returns the JSON DataPart if the caller sent one, else None - None
    means the caller sent free text and should be routed through the AI
    Core tool-calling agent instead.
    """
    data_parts = get_data_parts(context.message.parts)
    if data_parts and isinstance(data_parts[0], dict):
        return data_parts[0]
    return None


def build_response_message(context: RequestContext, summary_text: str, data: dict[str, Any] | None = None):
    parts = [new_text_part(summary_text)]
    if data is not None:
        parts.append(new_data_part(data))
    return new_message(parts, context_id=context.context_id, task_id=context.task_id)


def build_agent_card(*, name: str, description: str, skill: AgentSkill, base_url: str) -> AgentCard:
    return AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False, extended_agent_card=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url=base_url,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        skills=[skill],
    )