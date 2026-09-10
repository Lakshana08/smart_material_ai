"""Status Agent - the 4 material-status business rules (Technical Spec §6).
All logic lives in core_capabilities/material_status.py + engine/*.py, zero
LLM code; the LLM only picks a tool and narrates its result, never computes."""

from langchain_core.tools import tool

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentSkill

from app.a2a_agents.common import build_agent_card, emit_response, get_structured_input
from app.core_capabilities.material_status import (
    DEFAULT_RESULT_LIMIT,
    query_aging,
    query_machine_head_candidates,
    query_material_status,
)
from app.services.ai_core import run_agent

SKILL = AgentSkill(
    id="material_status_check",
    name="Material Status Check",
    description=(
        "Checks material control status, inventory aging, and machine-head "
        "material candidates from SAP source data (PP-134, 14C, COOIS, "
        "Z_AGEDINV). Accepts either structured JSON (check_type + "
        "work_order/material/storage_location) or a plain free-text "
        "question in English or Mandarin. check_type is one of "
        "'non_controlled', 'over_control', 'aging', 'machine_head_review'."
    ),
    tags=["material", "shortage", "aging", "over-control", "machine-head", "non-controlled"],
    examples=[
        "Is material SBC095160140 non-controlled?",
        "Any over-control materials in storage location S201?",
        "Show aged inventory for storage location A301",
        "Show machine-head material needing review",
    ],
    input_modes=["application/json", "text/plain"],
    output_modes=["application/json", "text/plain"],
)


# ---- Tools - business logic only. No LLM code inside any of these. ----


@tool
def check_non_controlled_material(material: str = "", storage_location: str = "") -> dict:
    """Check for Non-Controlled Material (无管制料) - WIP with no linked
    production order requirement, or where the requirement is already
    fulfilled. Optionally scoped by material and/or storage_location - but a
    broad call with NO arguments is valid and expected for questions like
    "show non-controlled material in the warehouse"; it returns up to
    1000 matching records plus the true total count (truncated=true if more exist)."""
    return query_material_status(
        material=material or None, storage_location=storage_location or None, status_filter="non_controlled"
    )


@tool
def check_over_control_material(work_order: str = "", material: str = "", storage_location: str = "") -> dict:
    """Check for Over-Control / Over-Issued Material (领超管制) - unrestricted
    stock exceeds the work order's controlled remaining issuance requirement.
    Optionally scoped by work_order, material, and/or storage_location - but a
    broad call with NO arguments is valid for questions like "any over-control
    materials today"; it returns up to 1000 matching records plus the true total
    count (truncated=true if more exist)."""
    return query_material_status(
        work_order=work_order or None,
        material=material or None,
        storage_location=storage_location or None,
        status_filter="over_control",
    )


@tool
def check_aging(material: str = "", storage_location: str = "") -> dict:
    """Check Aging - inventory that has not moved for more than 14 days
    (fixed threshold). Optionally scoped by material and/or storage_location -
    but a broad call with NO arguments is valid for questions like "show aged
    inventory"; it returns up to 1000 matching records plus the true total count
    (truncated=true if more exist). Rows with no Aging Days value in the source
    data (aging_days_unknown=true) are conservatively included as aged rather
    than silently dropped, since their true age can't be confirmed."""
    return query_aging(material=material or None, storage_location=storage_location or None)


@tool
def check_machine_head_review(order_type: str = "ZNPC") -> dict:
    """Surface Machine-Head Material (机头料) CANDIDATES for human review only
    - this is a heuristic flag, never a resolved/confirmed status."""
    return query_machine_head_candidates(order_type=order_type)


_TOOLS = [check_non_controlled_material, check_over_control_material, check_aging, check_machine_head_review]

_AGENT_SYSTEM_PROMPT = f"""You answer questions about material control status, inventory aging, and \
machine-head material review using the tools available. Always call the appropriate tool to get the \
computed result before answering - the numbers are already calculated for you by the tool, never \
estimate or invent them yourself.

Broad questions with no specific material/storage location/work order (e.g. "show non-controlled \
material in the warehouse", "any over-control materials today", "show aged inventory") are valid on \
their own - call the tool with no arguments, do not ask the user to narrow it down first. Every tool \
returns a true total count plus a capped list (up to {DEFAULT_RESULT_LIMIT} rows, with truncated=true \
if more exist). When the question asks for a list ("show", "list", "any ... today", etc.) and the \
result contains rows, list EVERY row the tool returned as a compact table (material, storage \
location/plant, and the status-specific fields such as aging_days or available_to_issue_qty) - never \
just state a bare count instead of the rows, and never say the list is "too long to fit" when you are \
holding fewer than {DEFAULT_RESULT_LIMIT} rows. Only when truncated is true, say so after the table and \
suggest narrowing by material or storage location to see the remaining rows. For questions that ask a \
yes/no or single-fact question (e.g. "is material X non-controlled"), a short 1-3 sentence answer is \
fine - no table needed. Only ask the user for missing information when the question itself refers to a \
specific thing you can't identify (e.g. "is it over-control" with no material named at all).

For machine-head review results, always phrase the answer as flagged candidates needing human review \
- never state it as a confirmed or resolved status."""


def build_status_agent_card(base_url: str):
    return build_agent_card(
        name="Status Agent",
        description=(
            "Checks material control status (non-controlled, over-control), inventory aging, "
            "and machine-head material review candidates."
        ),
        skill=SKILL,
        base_url=base_url,
    )


def _run_structured_check(args: dict) -> tuple[dict, str]:
    """Deterministic dispatch for structured JSON callers - no LLM involved."""
    check_type = args.get("check_type", "over_control")

    if check_type == "non_controlled":
        result = query_material_status(
            material=args.get("material"),
            storage_location=args.get("storage_location"),
            status_filter="non_controlled",
        )
    elif check_type == "aging":
        result = query_aging(material=args.get("material"), storage_location=args.get("storage_location"))
    elif check_type == "machine_head_review":
        result = query_machine_head_candidates(order_type=args.get("order_type", "ZNPC"))
    else:  # default: over_control
        result = query_material_status(
            work_order=args.get("work_order"),
            material=args.get("material"),
            storage_location=args.get("storage_location"),
            status_filter="over_control",
        )

    summary = f"Found {result['count']} record(s) for check_type '{check_type}'."
    return result, summary


def _has_review_candidates(tool_outputs: list) -> bool:
    return any(
        isinstance(output, dict) and any(row.get("needs_review") for row in output.get("results", []))
        for output in tool_outputs
    )


def _has_unknown_aging(tool_outputs: list) -> bool:
    return any(
        isinstance(output, dict) and any(row.get("aging_days_unknown") for row in output.get("results", []))
        for output in tool_outputs
    )


class StatusAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        args = get_structured_input(context)

        if args is not None:
            result, summary = _run_structured_check(args)
            await emit_response(event_queue, context, summary, result)
            return

        # Free text - the LLM decides which tool(s) to call and narrates the
        # result. No LLM call happens anywhere above this line.
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
        if _has_review_candidates(tool_outputs):
            # Forced server-side, not left to the model's phrasing - Technical
            # Spec §6.4: machine-head results must never be presented as resolved.
            answer += (
                "\n\nNote: this includes machine-head candidates flagged for human review, "
                "not a resolved status."
            )
        if _has_unknown_aging(tool_outputs):
            # Forced server-side for the same reason - a row with no Aging Days
            # value in the source data must not read as a confirmed 14+ day age.
            answer += (
                "\n\nNote: some results have no Aging Days value in the source data - they're "
                "conservatively included as aged pending verification, not a confirmed age."
            )

        data = {"tool_results": tool_outputs} if tool_outputs else None
        await emit_response(event_queue, context, answer, data)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Single-shot synchronous lookups - nothing runs long enough to cancel mid-flight.
        return None
