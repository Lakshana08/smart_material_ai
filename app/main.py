# Run: .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
"""FastAPI entrypoint that hosts four independent A2A agents - querying,
acting on, and reporting on live S/4HANA material data, plus evaluating
business-rule status against sample_data/ - plus a plain REST route for
downloading generated report files.

Each agent is a separate A2A server (its own AgentCard/discovery endpoint,
its own JSON-RPC endpoint) mounted at its own path prefix within this one
deployable app:

  /a2a/query   -> Material Query Agent   -> /.well-known/agent-card.json, /
  /a2a/action  -> Material Action Agent  -> /.well-known/agent-card.json, /
  /a2a/report  -> Material Report Agent  -> /.well-known/agent-card.json, /
  /a2a/status  -> Status Agent           -> /.well-known/agent-card.json, /

so a caller (e.g. a Joule Studio A2A code-based agent registration) sees
four distinct agents at
http://<host>/a2a/query/.well-known/agent-card.json, etc.
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
"""

from dotenv import load_dotenv
from fastapi import FastAPI

# Must run before any gen_ai_hub import: the AI Core SDK reads
# AICORE_CLIENT_ID/CLIENT_SECRET/AUTH_URL/BASE_URL/RESOURCE_GROUP directly
# from os.environ (see app/services/ai_core.py) rather than through
# app.core.config.Settings, so .env values need to land in the real process
# environment for local development.
load_dotenv()

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.utils.constants import DEFAULT_RPC_URL

from app.a2a_agents.action_agent import ActionAgentExecutor, build_action_agent_card
from app.a2a_agents.query_agent import QueryAgentExecutor, build_query_agent_card
from app.a2a_agents.report_agent import ReportAgentExecutor, build_report_agent_card
from app.a2a_agents.status_agent import StatusAgentExecutor, build_status_agent_card
from app.core.config import get_settings
from app.routers.reports import router as reports_router

_AGENTS = {
    "query": (build_query_agent_card, QueryAgentExecutor),
    "action": (build_action_agent_card, ActionAgentExecutor),
    "report": (build_report_agent_card, ReportAgentExecutor),
    "status": (build_status_agent_card, StatusAgentExecutor),
}


def _build_agent_app(path_segment: str, build_card, executor_cls) -> FastAPI:
    settings = get_settings()
    # Trailing slash matters: create_jsonrpc_routes registers the RPC endpoint
    # at DEFAULT_RPC_URL ("/") relative to this mounted sub-app, so the real
    # external path is ".../a2a/<segment>/" - advertising it without the
    # trailing slash makes strict A2A clients choke on the 307 redirect.
    base_url = f"{settings.app_public_url}/a2a/{path_segment}/"
    card = build_card(base_url=base_url)
    handler = DefaultRequestHandler(
        agent_executor=executor_cls(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    agent_app = FastAPI(title=card.name)
    add_a2a_routes_to_fastapi(
        agent_app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )
    return agent_app


def create_app() -> FastAPI:
    app = FastAPI(title="Smart Material AI - A2A Agents")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(reports_router)

    for path_segment, (build_card, executor_cls) in _AGENTS.items():
        app.mount(f"/a2a/{path_segment}", _build_agent_app(path_segment, build_card, executor_cls))

    return app


app = create_app()