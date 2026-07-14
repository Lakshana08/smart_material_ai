"""SAP AI Core-backed LangChain tool-calling agent, used by the 3 A2A agents
to handle free-text task input: the model is given the relevant
core_capabilities functions as tools, decides which (if any) to call based
on the user's text, executes them, and produces a final natural-language
answer - all in one call via run_agent().

Structured JSON input (e.g. a Joule Studio skill invocation with
pre-extracted fields) never goes through this module - agents dispatch
those directly to core_capabilities, no LLM involved.

Talks to AI Core directly over its OpenAI-compatible deployment endpoint
(OAuth2 client-credentials token + the AI Core Lifecycle API to resolve the
deployment's URL and underlying model name), wrapped in a plain
langchain_openai.ChatOpenAI. Deliberately NOT using generative-ai-hub-sdk's
LangChain integration: that package mixes a pydantic-v1-era base class into
langchain-openai's provider classes, which is a hard metaclass conflict
under any langchain-openai version new enough to also satisfy a2a-sdk's
pydantic>=2.11.3 requirement - there is no version of generative-ai-hub-sdk
that satisfies both at once.

Credentials: AICORE_CLIENT_ID / AICORE_CLIENT_SECRET / AICORE_AUTH_URL /
AICORE_BASE_URL / AICORE_RESOURCE_GROUP, read directly from the process
environment - app/main.py calls load_dotenv() so .env values reach
os.environ locally; on Cloud Foundry these are set via `cf set-env`.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Callable

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_CACHE_TTL_SECONDS = 1800

_token_lock = threading.Lock()
_token_cache: tuple[str, float] | None = None


def _fetch_aicore_token() -> str:
    global _token_cache

    with _token_lock:
        if _token_cache and _token_cache[1] > time.time():
            return _token_cache[0]

    resp = httpx.post(
        os.environ["AICORE_AUTH_URL"],
        data={"grant_type": "client_credentials"},
        auth=(os.environ["AICORE_CLIENT_ID"], os.environ["AICORE_CLIENT_SECRET"]),
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]

    with _token_lock:
        _token_cache = (token, time.time() + _TOKEN_CACHE_TTL_SECONDS)
    return token


def _resolve_deployment(token: str, deployment_id: str) -> tuple[str, str]:
    """Returns (deployment_url, model_name) for the given AI Core deployment id."""
    resource_group = os.environ.get("AICORE_RESOURCE_GROUP", "default")
    resp = httpx.get(
        f"{os.environ['AICORE_BASE_URL']}/lm/deployments/{deployment_id}",
        headers={"Authorization": f"Bearer {token}", "AI-Resource-Group": resource_group},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    model_name = data["details"]["resources"]["backendDetails"]["model"]["name"]
    return data["deploymentUrl"], model_name


def _find_deployment_by_model(token: str, model_name: str) -> tuple[str, str]:
    """Fallback used when LLM_DEPLOYMENT_ID isn't set: finds the first RUNNING
    deployment serving the given model name."""
    resource_group = os.environ.get("AICORE_RESOURCE_GROUP", "default")
    resp = httpx.get(
        f"{os.environ['AICORE_BASE_URL']}/lm/deployments",
        params={"status": "RUNNING"},
        headers={"Authorization": f"Bearer {token}", "AI-Resource-Group": resource_group},
        timeout=10,
    )
    resp.raise_for_status()
    for deployment in resp.json().get("resources", []):
        backend_model = deployment.get("details", {}).get("resources", {}).get("backendDetails", {}).get("model", {})
        if backend_model.get("name") == model_name:
            return deployment["deploymentUrl"], model_name
    raise ValueError(f"No running AI Core deployment found serving model '{model_name}'")


def _build_llm():
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    token = _fetch_aicore_token()

    if settings.llm_deployment_id:
        deployment_url, model_name = _resolve_deployment(token, settings.llm_deployment_id)
    else:
        deployment_url, model_name = _find_deployment_by_model(token, settings.ai_core_model_name)

    resource_group = os.environ.get("AICORE_RESOURCE_GROUP", "default")
    return ChatOpenAI(
        model=model_name,
        base_url=deployment_url,
        api_key=token,
        default_headers={"AI-Resource-Group": resource_group},
        default_query={"api-version": "2023-05-15"},
    )


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
    except Exception:
        logger.exception("AI Core agent execution failed")
        return None
