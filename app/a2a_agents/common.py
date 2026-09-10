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
from a2a.server.events import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Task, TaskState, TaskStatus
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


async def emit_response(
    event_queue: EventQueue,
    context: RequestContext,
    summary_text: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Completes the task with a final agent message, in a single event.

    Joule's agent-request action doesn't treat a standalone A2A Message as a
    finished turn - it needs a Task. But enqueueing a submitted Task and then
    a *separate* TaskStatusUpdateEvent(completed) right after (e.g. via
    TaskUpdater) lets Joule capture the first (submitted) event as the
    message/send result before the completed update is ever applied -
    confirmed via Joule's own debug trace, which showed
    agentResult.body.status == {"state": "submitted"} with no message at
    all. Since this app is always single-shot (the full answer is already
    known by the time this runs, nothing genuinely continues in the
    background), enqueue one Task that's already in a terminal state -
    no submitted-then-completed transition to race against.
    """
    parts = [new_text_part(summary_text)]
    if data is not None:
        parts.append(new_data_part(data))
    agent_message = new_message(parts, context_id=context.context_id, task_id=context.task_id)
    task = Task(
        id=context.task_id,
        context_id=context.context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, message=agent_message),
    )
    await event_queue.enqueue_event(task)


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
