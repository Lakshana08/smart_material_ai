from langchain_core.tools import tool

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentSkill

from app.a2a_agents.common import build_agent_card, build_response_message, get_structured_input
from app.core_capabilities.query import (
    query_material_master,
    query_material_mrp_area,
    query_material_serial_numbers,
    query_material_stock,
    query_production_order,
)
from app.services.ai_core import run_agent
from app.services.s4_client import S4ClientError

SKILL = AgentSkill(
    id="query_material",
    name="Query Material",
    description=(
        "Looks up data in S/4HANA. Accepts either structured JSON "
        "(query_type + product/material/plant/production_order) or a plain "
        "free-text question. query_type selects the source: "
        "'material_master' (MM03 - needs product), "
        "'material_stock' (MMBE - needs material, optional plant), "
        "'production_order' (COOIS - production_order and/or material and/or plant), "
        "'material_serial_number' (MMBE serialized stock - needs material, "
        "optional plant and serial_number), "
        "'material_mrp_area' (MRP area planning data - needs product, optional "
        "plant and mrp_area). "
        "Defaults to 'material_stock' if query_type is omitted."
    ),
    tags=["s4hana", "material", "production-order", "query"],
    examples=[
        "What's the stock for material MAT-1000?",
        "Look up material master data for product MAT-2000",
        "Show me production order 60001234",
        "What's the reorder point and safety stock for MAT-1000 at plant 1010?",
    ],
    input_modes=["application/json", "text/plain"],
    output_modes=["application/json", "text/plain"],
)


@tool
def lookup_material_stock(material: str, plant: str = "") -> dict:
    """Look up stock overview (MMBE) for a material, optionally scoped to a plant. Use this for stock/inventory questions."""
    return query_material_stock(material=material, plant=plant or None)


@tool
def lookup_material_master(product: str) -> dict:
    """Look up material/product master data (MM03) for a material or product number."""
    return query_material_master(product=product)


@tool
def lookup_production_order(production_order: str = "", material: str = "", plant: str = "") -> dict:
    """Look up production order info (COOIS), filterable by production_order, material, and/or plant."""
    return query_production_order(
        production_order=production_order or None,
        material=material or None,
        plant=plant or None,
    )


@tool
def lookup_material_serial_numbers(material: str, plant: str = "", serial_number: str = "") -> dict:
    """Look up serialized stock (MMBE, broken down by serial number/equipment) for a material,
    optionally scoped to a plant and/or specific serial_number."""
    return query_material_serial_numbers(
        material=material,
        plant=plant or None,
        serial_number=serial_number or None,
    )


@tool
def lookup_material_mrp_area(product: str, plant: str = "", mrp_area: str = "") -> dict:
    """Look up MRP area planning data (MRP type/controller, reorder point, safety stock, lot
    sizing) for a product, optionally scoped to a plant and/or specific mrp_area."""
    return query_material_mrp_area(
        product=product,
        plant=plant or None,
        mrp_area=mrp_area or None,
    )


_TOOLS = [
    lookup_material_stock,
    lookup_material_master,
    lookup_production_order,
    lookup_material_serial_numbers,
    lookup_material_mrp_area,
]

_AGENT_SYSTEM_PROMPT = """You answer questions about S/4HANA material master data, stock levels, \
production orders, serialized stock, and MRP planning data (reorder point, safety stock, lot \
sizing) using the tools available. Always call the appropriate tool to get real data before \
answering - never invent numbers or data. Keep answers short and factual (1-3 sentences). If \
the question doesn't give you enough information to call a tool (e.g. no material number), ask the \
user for what's missing instead of guessing."""


def build_query_agent_card(base_url: str):
    return build_agent_card(
        name="Material Query Agent",
        description=(
            "Answers questions about material master data, stock levels, production orders, "
            "serialized stock, and MRP planning data in S/4HANA."
        ),
        skill=SKILL,
        base_url=base_url,
    )


# Required identifier field per query_type, used to validate structured input
# before dispatch. None means no single field is strictly required (COOIS
# accepts any combination of production_order/material/plant).
_REQUIRED_FIELD = {
    "material_master": "product",
    "material_stock": "material",
    "production_order": None,
    "material_serial_number": "material",
    "material_mrp_area": "product",
}


def _run_structured_query(args: dict) -> tuple[dict, str]:
    """Deterministic dispatch for structured JSON callers - no LLM involved."""
    query_type = args.get("query_type", "material_stock")

    if query_type == "material_master":
        product = args.get("product") or args.get("material_number")
        result = query_material_master(product=product)
        summary = (
            f"Found {result['count']} material master record(s) for product {product}."
            if result["results"]
            else f"No material master data found for product {product}."
        )
        return result, summary

    if query_type == "production_order":
        result = query_production_order(
            production_order=args.get("production_order"),
            material=args.get("material") or args.get("material_number"),
            plant=args.get("plant"),
        )
        summary = f"Found {result['count']} production order record(s)."
        return result, summary

    if query_type == "material_serial_number":
        material = args.get("material") or args.get("material_number")
        result = query_material_serial_numbers(
            material=material, plant=args.get("plant"), serial_number=args.get("serial_number")
        )
        summary = f"Found {result['count']} serialized stock record(s) for material {material}."
        return result, summary

    if query_type == "material_mrp_area":
        product = args.get("product") or args.get("material_number")
        result = query_material_mrp_area(
            product=product, plant=args.get("plant"), mrp_area=args.get("mrp_area")
        )
        summary = (
            f"Found {result['count']} MRP area record(s) for product {product}."
            if result["results"]
            else f"No MRP area data found for product {product}."
        )
        return result, summary

    # material_stock (also the default when query_type is omitted)
    material = args.get("material") or args.get("material_number")
    result = query_material_stock(material=material, plant=args.get("plant"))
    summary = (
        f"Found {result['count']} stock record(s) for material {material}."
        if result["results"]
        else f"No stock records found for material {material}."
    )
    return result, summary


class QueryAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        args = get_structured_input(context)

        if args is not None:
            query_type = args.get("query_type", "material_stock")

            if query_type not in _REQUIRED_FIELD:
                await event_queue.enqueue_event(
                    build_response_message(
                        context,
                        f"Unknown query_type '{query_type}', expected one of {sorted(_REQUIRED_FIELD)}.",
                        {"error": "unknown_query_type"},
                    )
                )
                return

            required_field = _REQUIRED_FIELD[query_type]
            identifier_present = any(
                args.get(key) for key in ("product", "material", "material_number", "production_order")
            )
            if required_field and not identifier_present:
                await event_queue.enqueue_event(
                    build_response_message(
                        context,
                        f"I need a '{required_field}' to run a {query_type} lookup.",
                        {"error": "missing_required_field", "query_type": query_type},
                    )
                )
                return

            try:
                result, summary = _run_structured_query(args)
            except S4ClientError as exc:
                await event_queue.enqueue_event(
                    build_response_message(context, f"Couldn't reach S/4HANA: {exc}", {"error": "s4_error"})
                )
                return

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
        # Single-shot synchronous lookups - nothing runs long enough to cancel mid-flight.
        return None
