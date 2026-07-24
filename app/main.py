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
"""

import logging

from dotenv import load_dotenv
from fastapi import FastAPI, Request

logger = logging.getLogger("a2a_wire_debug")

# Must run before app.services.ai_core is imported: it reads
# AICORE_CLIENT_ID/CLIENT_SECRET/AUTH_URL/BASE_URL/RESOURCE_GROUP directly
# from os.environ rather than through app.core.config.Settings, so .env
# values need to land in the real process environment for local development.
load_dotenv()

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.utils.constants import DEFAULT_RPC_URL
from starlette.routing import Route

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


def _build_agent_app(path_segment: str, build_card, executor_cls) -> tuple[FastAPI, DefaultRequestHandler]:
    settings = get_settings()
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
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL, enable_v0_3_compat=True),
    )
    return agent_app, handler


def create_app() -> FastAPI:
    app = FastAPI(title="Smart Material AI - A2A Agents")

    @app.middleware("http")
    async def log_a2a_wire_traffic(request: Request, call_next):
        # TEMPORARY: logs raw request/response bodies for /a2a/* calls so we can
        # see exactly what a real caller (e.g. Joule) sends on the wire - remove
        # once the calling convention is confirmed.
        if request.url.path.startswith("/a2a/"):
            body = await request.body()
            logger.warning(
                "A2A REQUEST %s %s headers=%s body=%s",
                request.method,
                request.url.path,
                dict(request.headers),
                body.decode("utf-8", errors="replace"),
            )
            response = await call_next(request)
            chunks = [chunk async for chunk in response.body_iterator]
            resp_body = b"".join(chunks)
            logger.warning("A2A RESPONSE %s status=%s body=%s", request.url.path, response.status_code, resp_body.decode("utf-8", errors="replace"))
            from starlette.responses import Response as _Response

            return _Response(
                content=resp_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        return await call_next(request)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(reports_router)

    for path_segment, (build_card, executor_cls) in _AGENTS.items():
        agent_app, handler = _build_agent_app(path_segment, build_card, executor_cls)
        app.mount(f"/a2a/{path_segment}", agent_app)
        # Mounting means a bare POST to /a2a/<segment> (no trailing slash) 307-
        # redirects to /a2a/<segment>/ - some A2A clients (e.g. Joule) don't
        # follow redirects on POST, so the request just fails. Register the
        # same JSON-RPC endpoint directly on the bare path too, bypassing the
        # Mount's redirect entirely for that case.
        bare_route = create_jsonrpc_routes(handler, rpc_url=f"/a2a/{path_segment}", enable_v0_3_compat=True)[0]
        app.routes.append(Route(bare_route.path, endpoint=bare_route.endpoint, methods=["POST"]))

    return app


app = create_app()
