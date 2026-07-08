from langchain_core.tools import tool

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentSkill

from app.a2a_agents.common import build_agent_card, build_response_message, get_structured_input
from app.core_capabilities.action import perform_material_action
from app.services.ai_core import run_agent
from app.services.s4_client import S4ClientError

SKILL = AgentSkill(
    id="perform_material_action",
    name="Perform Material Action",
    description=(
        "Performs a business action on a material in S/4HANA (e.g. create, "
        "update, block/unblock). Accepts either structured JSON "
        "(material_number, action, optional payload) or a plain free-text "
        "instruction."
    ),
    tags=["s4hana", "material", "action"],
    examples=["Block material MAT-1000", "Update the description of material MAT-2000"],
    input_modes=["application/json", "text/plain"],
    output_modes=["application/json", "text/plain"],
)


@tool
def perform_action(material_number: str, action: str, reason: str = "") -> dict:
    """Perform a business action (e.g. block, unblock, update) on a material in S/4HANA."""
    payload = {"reason": reason} if reason else None
    return perform_material_action(material_number=material_number, action=action, payload=payload)


_TOOLS = [perform_action]

_AGENT_SYSTEM_PROMPT = """You perform business actions on materials in S/4HANA using the tool \
available. Only call the tool if the user's instruction clearly states a material number and an \
action - never guess or invent values. If either is missing or ambiguous, ask the user to clarify \
instead of calling the tool. Keep answers short and factual (1-2 sentences)."""


def build_action_agent_card(base_url: str):
    return build_agent_card(
        name="Material Action Agent",
        description="Performs business actions on materials in S/4HANA (create/update/trigger processes).",
        skill=SKILL,
        base_url=base_url,
    )


class ActionAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        args = get_structured_input(context)

        if args is not None:
            material_number = args.get("material_number")
            action = args.get("action")
            payload = args.get("payload")

            if not material_number or not action:
                await event_queue.enqueue_event(
                    build_response_message(
                        context,
                        "I need both a material_number and an action to perform.",
                        {"error": "missing_required_fields"},
                    )
                )
                return

            try:
                result = perform_material_action(material_number=material_number, action=action, payload=payload)
            except S4ClientError as exc:
                await event_queue.enqueue_event(
                    build_response_message(context, f"Action failed against S/4HANA: {exc}", {"error": "s4_error"})
                )
                return

            summary = f"Action '{action}' completed for material {material_number}."
            await event_queue.enqueue_event(build_response_message(context, summary, result))
            return

        # Free text - let the AI Core tool-calling agent decide what to call.
        text = context.get_user_input()
        agent_result = await run_agent(_AGENT_SYSTEM_PROMPT, _TOOLS, text)
        if agent_result is None:
            await event_queue.enqueue_event(
                build_response_message(
                    context,
                    "I couldn't process that request right now (AI Core unavailable).",
                    {"error": "ai_core_unavailable"},
                )
            )
            return

        answer, tool_outputs = agent_result
        data = {"tool_results": tool_outputs} if tool_outputs else None
        await event_queue.enqueue_event(build_response_message(context, answer, data))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None
