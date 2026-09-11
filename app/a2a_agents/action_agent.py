from langchain_core.tools import tool

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentSkill

from app.a2a_agents.common import build_agent_card, emit_response, get_structured_input
from app.core_capabilities.action import perform_material_action, perform_production_order_action
from app.services.ai_core import run_agent
from app.services.s4_client import S4ClientError

_PRODUCTION_ORDER_ACTIONS = {"create_production_order", "update_production_order"}

SKILL = AgentSkill(
    id="perform_material_action",
    name="Perform Material Action",
    description=(
        "Updates material master fields in S/4HANA (T-code MM02) and creates new "
        "material masters (T-code MM01), via SAP's Product Master API (API_PRODUCT_SRV, "
        "entities A_Product / A_ProductDescription). Five actions supported: 'update_status' "
        "(payload.status - the new CrossPlantStatus code), 'update_weight' "
        "(payload.gross_weight, payload.weight_unit), 'update_description' "
        "(payload.description, optional payload.language, default 'EN'), "
        "'create_material' (requires material_number plus payload.product_type, "
        "payload.industry_sector, payload.base_unit, and payload.description - this system "
        "rejects a create with no description at all; optional payload.language, "
        "payload.material_group), and 'delete_description' "
        "(removes a material's description for a given payload.language, default "
        "'EN'). Accepts either structured JSON (material_number, action, payload) "
        "or a plain free-text instruction. Also creates and updates production orders "
        "(T-code CO01/CO02) via SAP's Production Order API (API_PRODUCTION_ORDER_2_SRV, "
        "entity A_ProductionOrder_2): 'create_production_order' (payload.material, "
        "payload.production_plant, payload.manufacturing_order_type, payload.total_quantity, "
        "payload.mfg_order_planned_end_date - ISO 8601, e.g. '2026-12-01T00:00:00', and "
        "payload.production_version - this system rejects a create with no production version "
        "at all; SAP assigns the order number, so no order_number is given for this action) "
        "and 'update_production_order' (requires order_number - the "
        "ManufacturingOrder number - plus at least one of payload.total_quantity or "
        "payload.mfg_order_planned_end_date)."
    ),
    tags=[
        "s4hana",
        "material",
        "action",
        "mm01",
        "mm02",
        "co01",
        "co02",
        "product-master-api",
        "api_product_srv",
        "production-order-api",
        "api_production_order_2_srv",
    ],
    examples=[
        "Change the status of material TG20 to Z1",
        "Update the gross weight of material TG20 to 12.5 KG",
        "Update the description of material TG20 to 'Trade Item 20'",
        "Create a new material TG99, type FERT, industry sector M, base unit EA, description 'New Trade Item'",
        "Delete the German description of material TG20",
        "Create a production order for material FG126, plant 1710, order type YBM1, quantity 5, planned end 2026-12-01",
        "Update production order 1002200 to quantity 10 and planned end date 2026-12-05",
    ],
    input_modes=["application/json", "text/plain"],
    output_modes=["application/json", "text/plain"],
)


@tool
def update_material_status(material_number: str, status: str) -> dict:
    """Update the cross-plant status (CrossPlantStatus) of a material master record (T-code MM02).
    Uses SAP's Product Master API (API_PRODUCT_SRV)."""
    return perform_material_action(material_number=material_number, action="update_status", payload={"status": status})


@tool
def update_material_weight(material_number: str, gross_weight: str, weight_unit: str) -> dict:
    """Update the gross weight (GrossWeight) and weight unit (WeightUnit) of a material master record (T-code MM02).
    Uses SAP's Product Master API (API_PRODUCT_SRV)."""
    return perform_material_action(
        material_number=material_number,
        action="update_weight",
        payload={"gross_weight": gross_weight, "weight_unit": weight_unit},
    )


@tool
def update_material_description(material_number: str, description: str, language: str = "EN") -> dict:
    """Update the description of a material master record (T-code MM02). language is a 2-letter SAP language code, defaults to EN.
    Uses SAP's Product Master API (API_PRODUCT_SRV)."""
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
    description: str,
    material_group: str | None = None,
    language: str = "EN",
) -> dict:
    """Create a new material master record (T-code MM01). material_number is the
    external material number to assign. product_type is the SAP material type code
    (e.g. 'FERT', 'HAWA', 'ROH'). industry_sector is the SAP industry sector code
    (e.g. 'M', 'C', 'P', 'R'). base_unit is the base unit of measure (e.g. 'EA', 'KG').
    description is required - this system rejects a create with no description at
    all. material_group is optional; language is a 2-letter SAP language code for
    the description, defaults to EN. Uses SAP's Product Master API (API_PRODUCT_SRV)."""
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
    language is a 2-letter SAP language code, defaults to EN.
    Uses SAP's Product Master API (API_PRODUCT_SRV)."""
    return perform_material_action(
        material_number=material_number, action="delete_description", payload={"language": language}
    )


@tool
def create_production_order(
    material: str,
    production_plant: str,
    manufacturing_order_type: str,
    total_quantity: str,
    mfg_order_planned_end_date: str,
    production_version: str,
) -> dict:
    """Create a new production order in S/4HANA (T-code CO01) via the Production Order API
    (API_PRODUCTION_ORDER_2_SRV). material is the material to produce. production_plant is
    the SAP plant code. manufacturing_order_type is the SAP order type code (e.g. 'YBM1').
    total_quantity is the order quantity. mfg_order_planned_end_date is an ISO 8601 datetime
    string (e.g. '2026-12-01T00:00:00'). production_version is required - this system rejects
    a create with no production version at all. SAP assigns the resulting production order
    number - it's returned in the result."""
    payload = {
        "material": material,
        "production_plant": production_plant,
        "manufacturing_order_type": manufacturing_order_type,
        "total_quantity": total_quantity,
        "mfg_order_planned_end_date": mfg_order_planned_end_date,
        "production_version": production_version,
    }
    return perform_production_order_action(order_number=None, action="create_production_order", payload=payload)


@tool
def update_production_order(
    order_number: str,
    total_quantity: str | None = None,
    mfg_order_planned_end_date: str | None = None,
) -> dict:
    """Update the total quantity and/or planned end date of an existing production order in
    S/4HANA (T-code CO02) via the Production Order API (API_PRODUCTION_ORDER_2_SRV). order_number
    is the ManufacturingOrder number to update. mfg_order_planned_end_date is an ISO 8601
    datetime string (e.g. '2026-12-05T00:00:00'). At least one of total_quantity or
    mfg_order_planned_end_date must be given."""
    payload = {"total_quantity": total_quantity, "mfg_order_planned_end_date": mfg_order_planned_end_date}
    return perform_production_order_action(
        order_number=order_number, action="update_production_order", payload=payload
    )


_TOOLS = [
    update_material_status,
    update_material_weight,
    update_material_description,
    create_material,
    delete_material_description,
    create_production_order,
    update_production_order,
]

_AGENT_SYSTEM_PROMPT = """You update material master fields, create new materials, delete \
material descriptions, and create/update production orders in S/4HANA using the tools \
available. Only call a tool if the user's instruction clearly states all the required values \
- never guess or invent values (especially product_type, industry_sector, and base_unit for \
create_material; material, production_plant, manufacturing_order_type, total_quantity, and \
mfg_order_planned_end_date for create_production_order). If anything required is missing or \
ambiguous, ask the user to clarify instead of calling a tool. Keep answers short and factual \
(1-2 sentences)."""


def build_action_agent_card(base_url: str):
    return build_agent_card(
        name="Material Action Agent",
        description=(
            "Creates and updates material master records in S/4HANA via the Product Master API "
            "(API_PRODUCT_SRV): create a product master (T-code MM01), update a product master's "
            "status or weight, and update or delete a product master's description (T-code MM02). "
            "Also creates and updates production orders (T-code CO01/CO02) via the Production "
            "Order API (API_PRODUCTION_ORDER_2_SRV)."
        ),
        skill=SKILL,
        base_url=base_url,
    )


class ActionAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        args = get_structured_input(context)

        if args is not None:
            action = args.get("action")
            payload = args.get("payload")

            if not action:
                await emit_response(
                    event_queue, context, "I need an action to perform.", {"error": "missing_required_fields"}
                )
                return

            if action in _PRODUCTION_ORDER_ACTIONS:
                order_number = args.get("order_number")
                if action == "update_production_order" and not order_number:
                    await emit_response(
                        event_queue,
                        context,
                        "I need an order_number to update a production order.",
                        {"error": "missing_required_fields"},
                    )
                    return
                identifier = order_number
                try:
                    result = perform_production_order_action(order_number=order_number, action=action, payload=payload)
                except S4ClientError as exc:
                    await emit_response(
                        event_queue, context, f"Action failed against S/4HANA: {exc}", {"error": "s4_error"}
                    )
                    return
                except ValueError as exc:
                    await emit_response(event_queue, context, str(exc), {"error": "invalid_action"})
                    return
                identifier = identifier or result.get("manufacturing_order")
            else:
                material_number = args.get("material_number")
                if not material_number:
                    await emit_response(
                        event_queue,
                        context,
                        "I need both a material_number and an action to perform.",
                        {"error": "missing_required_fields"},
                    )
                    return
                identifier = material_number
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

            summary = f"Action '{action}' completed" + (f" for {identifier}." if identifier else ".")
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