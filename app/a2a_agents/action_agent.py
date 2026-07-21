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
        "Updates material master fields in S/4HANA (T-code MM02), creates new "
        "material masters (T-code MM01), and updates MRP area / supply planning data. Five "
        "actions supported: 'update_status' (payload.status - the new CrossPlantStatus "
        "code), 'update_description' (payload.description, optional payload.language, "
        "default 'EN'), 'create_material' (requires material_number plus "
        "payload.product_type, payload.industry_sector, payload.base_unit; optional "
        "payload.description, payload.language, payload.material_group), "
        "'update_mrp_area' (requires payload.plant, payload.mrp_area, and at least one "
        "of the planning fields below), and 'update_supply_planning' (plant-level, no "
        "MRP area - requires payload.plant and at least one of the planning fields "
        "below). Planning fields for both: payload.reorder_point, payload.safety_stock, "
        "payload.mrp_type, payload.mrp_controller, payload.mrp_group, "
        "payload.minimum_lot_size, payload.maximum_lot_size, payload.maximum_stock, "
        "payload.lot_sizing_procedure, payload.planning_time_fence. Accepts either "
        "structured JSON (material_number, action, payload) or a plain free-text "
        "instruction."
    ),
    tags=["s4hana", "material", "action", "mm01", "mm02", "mrp"],
    examples=[
        "Change the status of material TG20 to Z1",
        "Update the description of material TG20 to 'Trade Item 20'",
        "Create a new material TG99, type FERT, industry sector M, base unit EA, description 'New Trade Item'",
        "Set the reorder point for MAT-1000 at plant 1010 MRP area 1010 to 50",
        "Set the safety stock for MAT-1000 at plant 1010 to 20 (plant-level, no MRP area)",
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
def update_material_mrp_area(
    material_number: str,
    plant: str,
    mrp_area: str,
    reorder_point: float | None = None,
    safety_stock: float | None = None,
    mrp_type: str | None = None,
    mrp_controller: str | None = None,
    mrp_group: str | None = None,
    minimum_lot_size: float | None = None,
    maximum_lot_size: float | None = None,
    maximum_stock: float | None = None,
    lot_sizing_procedure: str | None = None,
    planning_time_fence: str | None = None,
) -> dict:
    """Update MRP area planning data for a material at a plant/MRP area (composite key:
    material_number + plant + mrp_area). Pass only the field(s) that should change - at
    least one of reorder_point, safety_stock, mrp_type, mrp_controller, mrp_group,
    minimum_lot_size, maximum_lot_size, maximum_stock, lot_sizing_procedure, or
    planning_time_fence is required."""
    payload = {
        "plant": plant,
        "mrp_area": mrp_area,
        "reorder_point": reorder_point,
        "safety_stock": safety_stock,
        "mrp_type": mrp_type,
        "mrp_controller": mrp_controller,
        "mrp_group": mrp_group,
        "minimum_lot_size": minimum_lot_size,
        "maximum_lot_size": maximum_lot_size,
        "maximum_stock": maximum_stock,
        "lot_sizing_procedure": lot_sizing_procedure,
        "planning_time_fence": planning_time_fence,
    }
    return perform_material_action(material_number=material_number, action="update_mrp_area", payload=payload)


@tool
def update_material_supply_planning(
    material_number: str,
    plant: str,
    reorder_point: float | None = None,
    safety_stock: float | None = None,
    mrp_type: str | None = None,
    mrp_controller: str | None = None,
    mrp_group: str | None = None,
    minimum_lot_size: float | None = None,
    maximum_lot_size: float | None = None,
    maximum_stock: float | None = None,
    lot_sizing_procedure: str | None = None,
    planning_time_fence: str | None = None,
) -> dict:
    """Update plant-level supply planning data for a material at a plant (composite key:
    material_number + plant, no MRP area - use update_material_mrp_area instead if the
    user specifies an MRP area). Pass only the field(s) that should change - at least one
    of reorder_point, safety_stock, mrp_type, mrp_controller, mrp_group, minimum_lot_size,
    maximum_lot_size, maximum_stock, lot_sizing_procedure, or planning_time_fence is
    required."""
    payload = {
        "plant": plant,
        "reorder_point": reorder_point,
        "safety_stock": safety_stock,
        "mrp_type": mrp_type,
        "mrp_controller": mrp_controller,
        "mrp_group": mrp_group,
        "minimum_lot_size": minimum_lot_size,
        "maximum_lot_size": maximum_lot_size,
        "maximum_stock": maximum_stock,
        "lot_sizing_procedure": lot_sizing_procedure,
        "planning_time_fence": planning_time_fence,
    }
    return perform_material_action(material_number=material_number, action="update_supply_planning", payload=payload)


_TOOLS = [
    update_material_status,
    update_material_description,
    create_material,
    update_material_mrp_area,
    update_material_supply_planning,
]

_AGENT_SYSTEM_PROMPT = """You update material master fields, create new materials, and update MRP \
area / plant-level supply planning data (reorder point, safety stock, MRP type/controller, lot \
sizing) in S/4HANA using the tools available. Use update_material_mrp_area only when the user gives \
an MRP area; use update_material_supply_planning for plant-level changes with no MRP area. Only \
call a tool if the user's instruction clearly states all the required values - never guess or \
invent values (especially product_type, industry_sector, and base_unit for create_material, or \
plant/mrp_area for the planning tools). If anything required is missing or ambiguous, ask the user \
to clarify instead of calling a tool. Keep answers short and factual (1-2 sentences)."""


def build_action_agent_card(base_url: str):
    return build_agent_card(
        name="Material Action Agent",
        description=(
            "Updates material master fields (status, description, MRP area / supply planning data) in S/4HANA."
        ),
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
            except ValueError as exc:
                await event_queue.enqueue_event(
                    build_response_message(context, str(exc), {"error": "invalid_action"})
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
