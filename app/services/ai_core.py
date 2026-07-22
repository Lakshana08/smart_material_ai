"""SAP AI Core-backed LangChain tool-calling agent, used by the 3 A2A agents
to handle free-text task input: the model is given the relevant
core_capabilities functions as tools, decides which (if any) to call based
on the user's text, executes them, and produces a final natural-language
answer - all in one call via run_agent().

Structured JSON input (e.g. a Joule Studio skill invocation with
pre-extracted fields) never goes through this module - agents dispatch
those directly to core_capabilities, no LLM involved.

Uses gen_ai_hub.proxy.langchain.init_llm() to get a LangChain-compatible
chat model pointed at the configured AI Core deployment, then
langchain.agents.create_agent() (the current, non-deprecated API - not
langgraph.prebuilt.create_react_agent, which now warns "moved to
langchain.agents", and not langchain_classic.agents.AgentExecutor, the
older pattern) to build the tool-calling agent.

Credentials: the gen_ai_hub SDK resolves AICORE_CLIENT_ID / AICORE_CLIENT_SECRET
/ AICORE_AUTH_URL / AICORE_BASE_URL / AICORE_RESOURCE_GROUP from the process
environment, or automatically from VCAP_SERVICES once an AI Core service
instance is bound on Cloud Foundry. app/main.py calls load_dotenv() so
values placed in .env reach os.environ for the SDK to pick up locally.
"""

import json
import logging
from functools import lru_cache
from typing import Any, Callable

from app.core.config import get_settings
from app.services.s4_client import S4ClientError

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _resolve_model_name(deployment_id: str) -> str:
    """gen_ai_hub's init_llm requires a model_name that matches the
    deployment even when deployment_id is also given (a quirk of its
    catalog lookup - deployment_id alone isn't sufficient), so look up the
    deployment's real registered model_name from AI Core rather than
    relying on a possibly-mismatched config default.

    Cached per deployment_id: this was previously a live network call to AI
    Core's deployment catalog on EVERY free-text request across all agents,
    which is both slow and a real source of intermittent "AI Core
    unavailable" failures on nothing more than a transient catalog-lookup
    hiccup. A deployment's model_name doesn't change during a process's
    lifetime, so resolve it once. lru_cache only caches successful returns -
    a failure here isn't cached and will simply retry live on the next call.
    """
    from gen_ai_hub.proxy.core.proxy_clients import get_proxy_client

    proxy_client = get_proxy_client()
    proxy_client.update_deployments()
    for deployment in proxy_client.deployments:
        if deployment.deployment_id == deployment_id:
            return deployment.model_name
    raise ValueError(f"No AI Core deployment found with deployment_id '{deployment_id}'")


def _build_llm():
    from gen_ai_hub.proxy.langchain import init_llm

    settings = get_settings()
    if settings.llm_deployment_id:
        model_name = _resolve_model_name(settings.llm_deployment_id)
        return init_llm(model_name=model_name, deployment_id=settings.llm_deployment_id)
    return init_llm(model_name=settings.ai_core_model_name)


async def run_agent(
    system_prompt: str, tools: list[Callable[..., Any]], user_text: str
) -> tuple[str, list[Any]] | None:
    """Runs a LangChain tool-calling agent over user_text with the given
    tools bound. Returns (final_answer_text, raw_tool_outputs) - the second
    element mirrors what the structured-JSON path returns as its data part,
    so free-text and structured callers get comparably rich responses.
    Returns None if AI Core is disabled or the call fails - callers should
    fall back to requiring structured JSON input in that case, not crash.
    """
    settings = get_settings()
    if not settings.ai_core_enabled:
        return None

    from langchain.agents import create_agent
    from langchain_core.messages import ToolMessage

    try:
        llm = _build_llm()
        agent = create_agent(llm, tools=tools, system_prompt=system_prompt)
        result = await agent.ainvoke({"messages": [{"role": "user", "content": user_text}]})
        messages = result["messages"]
        final_text = messages[-1].content

        tool_outputs = []
        for message in messages:
            if isinstance(message, ToolMessage):
                try:
                    tool_outputs.append(json.loads(message.content))
                except (json.JSONDecodeError, TypeError):
                    tool_outputs.append(message.content)

        return final_text, tool_outputs
    except S4ClientError:
        # Raised by a tool the model called (e.g. generate_report,
        # perform_material_action), not by AI Core/the LLM itself - the
        # agent picked the right tool and the tool ran, S/4HANA just
        # rejected or failed to serve the request. Logged distinctly so
        # this doesn't get mistaken for an AI Core outage server-side; the
        # caller still sees the same "unavailable" fallback message today
        # since narrowing that requires updating each agent's executor.
        logger.exception("AI Core agent's tool call failed against S/4HANA")
        return None
    except Exception:
        logger.exception("AI Core agent execution failed (LLM call, tool-calling loop, or unexpected error)")
        return None