from langchain_core.tools import tool

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentSkill

from app.a2a_agents.common import build_agent_card, emit_response, get_structured_input
from app.core_capabilities.query import (
    QUERY_ROW_LIMIT,
    count_all_records,
    query_material_master,
    query_material_serial_numbers,
    query_material_stock,
    query_production_order,
)
from app.services.ai_core import run_agent
from app.services.odata_utils import humanize_odata_dates
from app.services.s4_client import S4ClientError

VALID_QUERY_TYPES = {"material_master", "material_stock", "production_order", "material_serial_number"}

SKILL = AgentSkill(
    id="query_material",
    name="Query Material",
    description=(
        "Looks up data in S/4HANA. Accepts either structured JSON "
        "(query_type + product/material/plant/production_order/serial_number, or "
        "count_only: true for a total count) or a plain free-text question. query_type "
        "selects the source: "
        "'material_master' (MM03 - needs product), "
        "'material_stock' (MMBE - needs material, optional plant), "
        "'production_order' (COOIS - production_order and/or material and/or plant), "
        "'material_serial_number' (MMBE serialized stock - needs material, optional plant/serial_number). "
        "Defaults to 'material_stock' if query_type is omitted. Also answers 'how many X are "
        "there in total' for any of the 4 sources, with no identifier needed - a real, unfiltered "
        "total count from S/4, not just the count of rows returned in one page."
    ),
    tags=["s4hana", "material", "production-order", "query"],
    examples=[
        "What's the stock for material MAT-1000?",
        "Look up material master data for product MAT-2000",
        "Show me production order 60001234",
        "List serial numbers for material MAT-1000 at plant 1010",
        "How many product masters are there in total?",
        "How many production orders are there?",
    ],
    input_modes=["application/json", "text/plain"],
    output_modes=["application/json", "text/plain"],
)


# query.py's QUERY_ROW_LIMIT (200) protects the live S/4 fetch and structured/
# direct callers, but 200 full-width rows fed straight into the LLM's
# tool-calling loop can still blow its context window - confirmed live:
# 200 raw production order rows alone hit 141k tokens, over the model's
# 128k limit. Free-text tools cap what actually reaches the LLM much lower;
# count/truncated stay accurate to the real S/4 total regardless.
_LLM_ROW_LIMIT = 20


def _for_llm(result: dict) -> dict:
    rows = result.get("results")
    if isinstance(rows, list) and len(rows) > _LLM_ROW_LIMIT:
        result = {**result, "results": rows[:_LLM_ROW_LIMIT], "truncated": True}
    return humanize_odata_dates(result)


@tool
def lookup_material_stock(material: str, plant: str = "") -> dict:
    """Look up stock overview (MMBE) for a material, optionally scoped to a plant. Use this for stock/inventory questions."""
    return _for_llm(query_material_stock(material=material, plant=plant or None))


@tool
def lookup_material_master(product: str) -> dict:
    """Look up material/product master data (MM03) for a material or product number."""
    return _for_llm(query_material_master(product=product))


@tool
def lookup_production_order(production_order: str = "", material: str = "", plant: str = "") -> dict:
    """Look up production order info (COOIS), filterable by production_order, material, and/or plant."""
    return _for_llm(
        query_production_order(
            production_order=production_order or None,
            material=material or None,
            plant=plant or None,
        )
    )


@tool
def lookup_material_serial_number(material: str, plant: str = "", serial_number: str = "") -> dict:
    """Look up serialized stock (MMBE, serial-number breakdown) for a material, optionally scoped to a plant and/or serial number."""
    return _for_llm(
        query_material_serial_numbers(material=material, plant=plant or None, serial_number=serial_number or None)
    )


@tool
def count_records(query_type: str) -> dict:
    """Get the total, unfiltered count of records for a query source - use this for "how many X
    are there in total" style questions, where the user isn't asking about one specific
    material/product/order. query_type is one of 'material_master', 'material_stock',
    'production_order', 'material_serial_number'. Do not use the lookup_* tools for this - those
    require a specific identifier and only return up to the row cap, not a true total."""
    return count_all_records(query_type=query_type)


_TOOLS = [
    lookup_material_stock,
    lookup_material_master,
    lookup_production_order,
    lookup_material_serial_number,
    count_records,
]

_AGENT_SYSTEM_PROMPT = f"""You answer questions about S/4HANA material master data, stock levels, \
production orders, and serialized stock (serial numbers) using the tools available - nothing else. \
Always call the appropriate tool to get real data before answering - never invent numbers or data. \
Keep answers short and factual (1-3 sentences). Only ask the user to clarify when a tool's one truly \
required identifier is missing: material for stock/serial-number lookups, product for material master. \
Plant and serial_number are optional filters on every tool that accepts them - if the user doesn't \
mention one, call the tool without it and return the unfiltered results; never ask for a plant code \
or serial number before running a lookup. production_order lookups need at least one of \
production_order/material/plant, not all three.

If the user asks "how many X are there" / "total number of X" for material masters, stock records, \
production orders, or serial numbers - with no specific material/product/order given - call \
count_records with the matching query_type instead of a lookup_* tool or refusing. This works with \
no identifier at all; never ask the user for one first.

Every lookup_* tool caps results at {QUERY_ROW_LIMIT} rows and returns a true total count plus \
truncated=true if more rows exist on the S/4HANA side. If truncated is true, say so and suggest \
narrowing the request (e.g. by production order, material, or plant) instead of trying to list \
every row.

Do NOT treat questions about over-control/over-issued status, non-controlled material, aging, \
machine-head material review, report generation, or ZPL pull-list actions as needing more parameters \
- those are not S/4HANA lookups this agent performs at all. If asked about any of them, do not call \
a tool or ask for material numbers - just say plainly that this isn't available from the Query Agent. \
Do not name any other agent or say who handles it.

Accuracy rules - confirmed live failure modes, follow these exactly:
1. Every date, number, code, or status value you state must be copied verbatim from a field that \
is literally present in the tool's JSON result - never invent or guess a value, even a plausible \
one. Field names describe their own content plainly (e.g. LastChangeDate is literally when the \
record was last changed, CreationDate is when it was created, LastChangedByUser is who last \
changed it) - when a question clearly maps to a field like this, use its value directly and \
confidently; don't refuse just because the mapping wasn't spelled out for you. Only say you don't \
have the information when no field in the result plausibly answers the question at all.
2. Never invent or assume a business meaning for a coded/technical field value (e.g. a special \
stock indicator, a status code) unless that meaning is explicitly present in the data itself (e.g. \
a description field). If asked what a code means and no description is provided, report the raw \
code and say its business meaning isn't available from this data - do not guess a plausible-sounding \
interpretation, even a common textbook one, since the actual configured meaning is client-specific.
3. The word "entries" (or "records") in a question always means the number of items in the tool's \
results array - it does NOT mean the number of distinct values within one field of those items, \
even when the question also says "different" (e.g. "how many different storage location entries \
exist" asks for the array length, not the count of distinct storage location codes). Confirmed \
live failure: 8 stock rows for one material, split across 2 storage location codes, was wrongly \
answered as "2" - the correct answer is 8. Only answer with a count of distinct values if the \
question asks for that specifically (e.g. "how many different storage locations are there", with \
no word like "entries"/"records" attached to it)."""


def build_query_agent_card(base_url: str):
    return build_agent_card(
        name="Material Query Agent",
        description=(
            "Answers questions about material master data, stock levels, production orders, "
            "and serialized stock in S/4HANA."
        ),
        skill=SKILL,
        base_url=base_url,
    )


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
        summary = (
            f"Found {result['count']} serial number record(s) for material {material}."
            if result["results"]
            else f"No serial number records found for material {material}."
        )
        return result, summary

    # default: material_stock
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
            if query_type not in VALID_QUERY_TYPES:
                await emit_response(
                    event_queue,
                    context,
                    f"Unknown query_type '{query_type}', expected one of {sorted(VALID_QUERY_TYPES)}.",
                    {"error": "unknown_query_type"},
                )
                return

            if args.get("count_only"):
                try:
                    result = count_all_records(query_type=query_type)
                except S4ClientError as exc:
                    await emit_response(
                        event_queue, context, f"Couldn't reach S/4HANA: {exc}", {"error": "s4_error"}
                    )
                    return
                summary = f"Total {query_type} count: {result['count']}."
                await emit_response(event_queue, context, summary, result)
                return

            required_field = {"material_master": "product", "production_order": None}.get(query_type, "material")
            identifier_present = bool(
                required_field and (args.get(required_field) or args.get("material_number"))
            )
            if required_field and not identifier_present:
                await emit_response(
                    event_queue,
                    context,
                    f"I need a '{required_field}' to run a {query_type} lookup.",
                    {"error": "missing_required_field", "query_type": query_type},
                )
                return

            try:
                result, summary = _run_structured_query(args)
            except S4ClientError as exc:
                await emit_response(event_queue, context, f"Couldn't reach S/4HANA: {exc}", {"error": "s4_error"})
                return

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
        # Single-shot synchronous lookups - nothing runs long enough to cancel mid-flight.
        return None