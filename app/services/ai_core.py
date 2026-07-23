"""LangChain tool-calling agent backed by SAP AI Core - free-text input only.
The model picks a tool and narrates its result; it never computes anything.
Structured JSON input skips this module entirely, no LLM involved."""

import json
import logging
from functools import lru_cache
from typing import Any, Callable

from app.core.config import get_settings
from app.services.s4_client import S4ClientError

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _resolve_model_name(deployment_id: str) -> str:
    """Looks up the deployment's registered model_name (init_llm requires it).
    Cached per deployment_id - avoids a live catalog call on every request;
    a failed lookup isn't cached, so it retries live next time."""
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
    """Runs one tool-calling turn; returns (answer_text, tool_outputs).
    Returns None only if AI Core is disabled; on failure, returns the real
    error as the answer text instead of a generic message."""
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
    except S4ClientError as exc:
        # A tool's S/4 call failed, not AI Core itself - logged distinctly.
        logger.exception("AI Core agent's tool call failed against S/4HANA")
        return f"S/4HANA error: {exc}", []
    except Exception as exc:
        logger.exception("AI Core agent execution failed (LLM call, tool-calling loop, or unexpected error)")
        return f"AI Core error: {type(exc).__name__}: {exc}", []