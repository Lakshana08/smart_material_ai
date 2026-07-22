from langchain_core.tools import tool

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentSkill

from app.a2a_agents.common import build_agent_card, build_response_message, get_structured_input
from app.core_capabilities._s4_apis import BY_NAME
from app.core_capabilities.report import generate_material_report
from app.services.ai_core import run_agent
from app.services.report_builder import SUPPORTED_FORMATS
from app.services.s4_client import S4ClientError

SKILL = AgentSkill(
    id="generate_material_report",
    name="Generate Material Report",
    description=(
        "Builds a downloadable report (PDF/Excel/CSV) from S/4HANA data. "
        f"report_type selects the source: one of {sorted(BY_NAME)}. "
        "identifiers optionally scopes rows by key (product/material/production order "
        "depending on report_type); plant further scopes where applicable. Accepts either "
        "structured JSON or a plain free-text request. Returns a download_url rather than "
        "the file itself."
    ),
    tags=["s4hana", "material", "production-order", "report"],
    examples=[
        "Generate a PDF stock report for plant 1010",
        "Export material master data for MAT-1000 and MAT-2000 to Excel",
        "Give me a CSV of production orders for plant 1010",
    ],
    input_modes=["application/json", "text/plain"],
    output_modes=["application/json", "text/plain"],
)


@tool
def generate_report(report_type: str = "material_stock", identifiers: str = "", plant: str = "", report_format: str = "pdf") -> dict:
    """Generate a downloadable report of S/4HANA data. report_type is one of
    'material_master', 'material_serial_number', 'material_stock', 'production_order'.
    identifiers is a comma-separated list of material/product/production-order numbers
    to scope the report to (leave empty for all). report_format is 'pdf', 'xlsx', or 'csv'."""
    id_list = [i.strip() for i in identifiers.split(",") if i.strip()] or None
    return generate_material_report(
        report_type=report_type, identifiers=id_list, plant=plant or None, report_format=report_format
    )


_TOOLS = [generate_report]

# Deliberately tells the model NOT to state the download link itself - the
# literal URL is always appended by the executor after the tool runs, so a
# model paraphrasing/garbling it in its answer can't break the actual link.
_AGENT_SYSTEM_PROMPT = f"""You generate S/4HANA reports using the tool available. report_type must \
be one of {sorted(BY_NAME)}; report_format must be one of {sorted(SUPPORTED_FORMATS)}. Infer both \
from the user's wording (e.g. "stock" -> material_stock, "Excel" -> xlsx), defaulting to \
material_stock/pdf if unclear. After calling the tool, briefly describe what was generated (e.g. \
report type and row count) in one short sentence - do NOT include any URL or link in your answer, \
that will be added separately."""


def build_report_agent_card(base_url: str):
    return build_agent_card(
        name="Material Report Agent",
        description="Generates downloadable reports (PDF/Excel/CSV) of material and production order data from S/4HANA.",
        skill=SKILL,
        base_url=base_url,
    )


class ReportAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        args = get_structured_input(context)

        if args is not None:
            report_type = args.get("report_type", "material_stock")
            identifiers = args.get("identifiers")
            plant = args.get("plant")
            report_format = args.get("report_format", "pdf")

            if report_type not in BY_NAME:
                await event_queue.enqueue_event(
                    build_response_message(
                        context,
                        f"Unknown report_type '{report_type}', expected one of {sorted(BY_NAME)}.",
                        {"error": "unknown_report_type"},
                    )
                )
                return

            if report_format not in SUPPORTED_FORMATS:
                await event_queue.enqueue_event(
                    build_response_message(
                        context,
                        f"Unsupported report_format '{report_format}', expected one of {sorted(SUPPORTED_FORMATS)}.",
                        {"error": "unsupported_format"},
                    )
                )
                return

            try:
                result = generate_material_report(
                    report_type=report_type, identifiers=identifiers, plant=plant, report_format=report_format
                )
            except S4ClientError as exc:
                await event_queue.enqueue_event(
                    build_response_message(context, f"Couldn't build report from S/4HANA data: {exc}", {"error": "s4_error"})
                )
                return

            summary = f"Report ready ({result['row_count']} rows): {result['download_url']}"
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
        download_url = next((t.get("download_url") for t in tool_outputs if isinstance(t, dict) and t.get("download_url")), None)
        final_text = f"{answer} {download_url}" if download_url else answer
        data = {"tool_results": tool_outputs} if tool_outputs else None
        await event_queue.enqueue_event(build_response_message(context, final_text, data))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None