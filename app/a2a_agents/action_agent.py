from langchain_core.tools import tool

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentSkill

from app.a2a_agents.common import build_agent_card, emit_response, get_structured_input
from app.core_capabilities.action import perform_material_action
from app.services.ai_core import run_agent
from app.services.s4_client import S4ClientError

SKILL = AgentSkill(
    id="perform_material_action",
    name="Perform Material Action",
    description=(
        "Updates material master fields in S/4HANA (T-code MM02) and creates new "
        "material masters (T-code MM01). Four actions supported: 'update_status' "
        "(payload.status - the new CrossPlantStatus code), 'update_description' "
        "(payload.description, optional payload.language, default 'EN'), "
        "'create_material' (requires material_number plus payload.product_type, "
        "payload.industry_sector, payload.base_unit; optional payload.description, "
        "payload.language, payload.material_group), and 'delete_description' "
        "(removes a material's description for a given payload.language, default "
        "'EN'). Accepts either structured JSON (material_number, action, payload) "
        "or a plain free-text instruction."
    ),
    tags=["s4hana", "material", "action", "mm01", "mm02"],
    examples=[
        "Change the status of material TG20 to Z1",
        "Update the description of material TG20 to 'Trade Item 20'",
        "Create a new material TG99, type FERT, industry sector M, base unit EA, description 'New Trade Item'",
        "Delete the German description of material TG20",
    ],
    input_modes=["application/json", "text/plain"],
    output_modes=["application/json", "text/plain"],
)


@tool
def update_material_status(material_number: str, status: str) -> dict:
    """Update the cross-plant status (CrossPlantStatus) of a material master record (T-code MM02)."""
    return perform_material_action(material_number=material_number, action="update_status", payload={"status": status})


@tool
def update_material_description(material_number: str, description: str, language: str = "EN") -> dict:
    """Update the description of a material master record (T-code MM02). language is a 2-letter SAP language code, defaults to EN."""
    return perform_material_action(
        material_number=material_number,
        action="update_description",
        payload={"description": description, "language": language},
    )


@tool
def create_material(
    material_number: str,
    product_type: str,
    industry_sector: str,
    base_unit: str,
    description: str | None = None,
    material_group: str | None = None,
    language: str = "EN",
) -> dict:
    """Create a new material master record (T-code MM01). material_number is the
    external material number to assign. product_type is the SAP material type code
    (e.g. 'FERT', 'HAWA', 'ROH'). industry_sector is the SAP industry sector code
    (e.g. 'M', 'C', 'P', 'R'). base_unit is the base unit of measure (e.g. 'EA', 'KG').
    description and material_group are optional; language is a 2-letter SAP language
    code for the description, defaults to EN."""
    payload = {
        "product_type": product_type,
        "industry_sector": industry_sector,
        "base_unit": base_unit,
        "description": description,
        "material_group": material_group,
        "language": language,
    }
    return perform_material_action(material_number=material_number, action="create_material", payload=payload)


@tool
def delete_material_description(material_number: str, language: str = "EN") -> dict:
    """Delete a material master's description for a given language (T-code MM02).
    language is a 2-letter SAP language code, defaults to EN."""
    return perform_material_action(
        material_number=material_number, action="delete_description", payload={"language": language}
    )


_TOOLS = [update_material_status, update_material_description, create_material, delete_material_description]

_AGENT_SYSTEM_PROMPT = """You update material master fields, create new materials, and delete \
material descriptions in S/4HANA using the tools available. Only call a tool if the user's \
instruction clearly states all the required values - never guess or invent values (especially \
product_type, industry_sector, and base_unit for create_material). If anything required is \
missing or ambiguous, ask the user to clarify instead of calling a tool. Keep answers short and \
factual (1-2 sentences)."""


def build_action_agent_card(base_url: str):
    return build_agent_card(
        name="Material Action Agent",
        description="Creates, updates, and deletes material master fields (status, description) in S/4HANA.",
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
                await emit_response(
                    event_queue,
                    context,
                    "I need both a material_number and an action to perform.",
                    {"error": "missing_required_fields"},
                )
                return

            try:
                result = perform_material_action(material_number=material_number, action=action, payload=payload)
            except S4ClientError as exc:
                await emit_response(
                    event_queue, context, f"Action failed against S/4HANA: {exc}", {"error": "s4_error"}
                )
                return
            except ValueError as exc:
                await emit_response(event_queue, context, str(exc), {"error": "invalid_action"})
                return

            summary = f"Action '{action}' completed for material {material_number}."
            await emit_response(event_queue, context, summary, result)
            return

        # Free text - let the AI Core tool-calling agent decide what to call.
        text = context.get_user_input()
        agent_result = await run_agent(_AGENT_SYSTEM_PROMPT, _TOOLS, text)
        if agent_result is None:
            await emit_response(
                event_queue,
                context,
                "I couldn't process that request right now (AI Core unavailable).",
                {"error": "ai_core_unavailable"},
            )
            return

        answer, tool_outputs = agent_result
        data = {"tool_results": tool_outputs} if tool_outputs else None
        await emit_response(event_queue, context, answer, data)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None