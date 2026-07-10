# smart_material_ai

A FastAPI application on **SAP BTP Cloud Foundry** that exposes three [A2A](https://a2a-protocol.org/) (Agent-to-Agent) agents for querying, acting on, and reporting against S/4HANA material data — designed to be registered as skills in SAP Joule Studio, or called by any other A2A-compatible client.

## Overview

The app hosts three independent A2A agents, each mounted as its own sub-application:

| Agent | Path | Purpose |
|---|---|---|
| Material Query Agent | `/a2a/query` | Looks up material master data (MM03), stock overview (MMBE), and production orders (COOIS) |
| Material Action Agent | `/a2a/action` | Creates/updates/deletes material master; creates/updates production orders; posts goods movements to change stock (scoped to what each underlying SAP API actually supports - see `core_capabilities/action.py`) |
| Material Report Agent | `/a2a/report` | Generates downloadable reports (PDF/Excel/CSV) from S/4 data |

Each agent advertises its own `AgentCard` at `/a2a/<agent>/.well-known/agent-card.json` and accepts JSON-RPC task requests at `/a2a/<agent>/`.

### Dual input path

Every agent supports two ways of being called:

- **Structured JSON** (e.g. a Joule Studio skill invocation with pre-extracted fields) — dispatched directly to the relevant `core_capabilities` function. No LLM involved.
- **Plain free text** — routed through a LangChain tool-calling agent backed by **SAP AI Core** (`services/ai_core.py`), with the same capability functions exposed to the model as tools. Controlled by `AI_CORE_ENABLED` (off by default); when off, agents only accept structured JSON.

### Data flow

```
A2A client (e.g. Joule Studio)
        │  JSON-RPC
        ▼
app/main.py  ──mounts──►  app/a2a_agents/{query,action,report}_agent.py
                                  │
                    (structured JSON)        (free text)
                                  │                │
                                  ▼                ▼
                     app/core_capabilities/*   services/ai_core.py
                                  │             (gen_ai_hub + LangChain
                                  │              tool-calling agent)
                                  ▼                │
                       app/services/s4_client.py ◄─┘  (via bound tools)
                                  │
                                  ▼
                    app/services/destination.py
                          │                │
                 (BTP Destination)   (BTP Connectivity,
                          │            if OnPremise)
                          ▼                ▼
                    S/4HANA OData services (Cloud or on-prem via Cloud Connector)
```

Report generation additionally goes through `services/report_builder.py`, which renders the file in memory, stores it under a short-lived token, and returns a `download_url` served by `routers/reports.py` (`GET /reports/download/{token}`) — A2A responses carry text/data parts, not binary attachments.

See [ARCHITECTURE_FLOW.md](ARCHITECTURE_FLOW.md) for the full target BTP architecture (Teams, Joule UI, MCP Server, identity federation, etc.) this app is one piece of.

## File structure

```
smart_material_ai/
├── app/
│   ├── main.py                    # FastAPI entrypoint - builds & mounts the 3 A2A sub-apps + /health + reports router
│   │
│   ├── a2a_agents/                # One AgentExecutor per A2A agent
│   │   ├── common.py              #   shared AgentCard builder, structured-input detection, response-message helper
│   │   ├── query_agent.py         #   Material Query Agent - MM03/MMBE/COOIS lookups
│   │   ├── action_agent.py        #   Material Action Agent - CRUD scoped to what each underlying API supports
│   │   └── report_agent.py        #   Material Report Agent - PDF/Excel/CSV report generation
│   │
│   ├── core_capabilities/         # Business logic - what each agent can actually do, independent of A2A/LLM plumbing
│   │   ├── _s4_apis.py            #   registry of S/4 OData service paths, key fields, plant-filter support
│   │   ├── query.py               #   read-only lookups (material master / stock / production order / serial numbers)
│   │   ├── action.py              #   create/update/delete, scoped per-resource to SAP's actual capabilities:
│   │   │                          #     material_master full CRUD, production_order create+update only,
│   │   │                          #     material_stock_movement create-only (A_MaterialStock has no write verb -
│   │   │                          #     stock changes via posting a goods movement instead)
│   │   └── report.py              #   fetches rows for a report_type and hands them to report_builder
│   │
│   ├── services/                  # Infrastructure / integration concerns
│   │   ├── s4_client.py           #   generic OData v2/v4 HTTP client (CSRF handling, JSON format, error wrapping)
│   │   ├── destination.py         #   resolves SAP BTP Destination (+ Connectivity proxy for on-prem systems)
│   │   ├── odata_utils.py         #   normalizes OData v2/v4 response shapes, strips __metadata/__deferred noise
│   │   ├── ai_core.py             #   LangChain tool-calling agent backed by SAP AI Core (gen_ai_hub.proxy.langchain)
│   │   └── report_builder.py      #   renders CSV/XLSX/PDF in memory, token-keyed short-lived storage
│   │
│   ├── core/
│   │   └── config.py              # pydantic-settings Settings (.env-backed) - destinations, AI Core toggle, public URL
│   │
│   └── routers/
│       └── reports.py             # GET /reports/download/{token} - streams a generated report file
│
├── ARCHITECTURE_FLOW.md           # Full target BTP architecture (Teams/Joule/MCP/identity) this app implements a slice of
├── ARCHITECTURE_FLOW.pdf          # Same, as a diagram export
├── manifest.yml                   # Cloud Foundry deployment manifest (buildpack, start command, bound services)
├── .env.example                   # Local dev config template (copy to .env, gitignored)
├── requirements.txt               # Python dependencies
└── README.md
```

## How it was built

1. **Transport layer first**: `app/main.py` sets up a FastAPI app that mounts three independently-addressable A2A sub-apps (one per agent), each with its own `AgentCard` and JSON-RPC endpoint, using the `a2a-sdk` package's `add_a2a_routes_to_fastapi` / `create_agent_card_routes` / `create_jsonrpc_routes` helpers.
2. **Business logic decoupled from transport**: each agent's `AgentExecutor` (in `a2a_agents/`) is a thin adapter — it extracts input, and either dispatches straight to a `core_capabilities` function (structured JSON path) or hands the same functions to `services/ai_core.run_agent()` as LangChain tools (free-text path). The capability functions themselves have no knowledge of A2A or LLMs.
3. **S/4 connectivity as its own layer**: `services/s4_client.py` + `services/destination.py` isolate all SAP BTP Destination/Connectivity resolution and OData mechanics (CSRF tokens, on-premise proxying via Cloud Connector) behind a small client interface, so `core_capabilities/*.py` only deals with OData paths and filters, not HTTP/auth plumbing. A `S4_BASE_URL` escape hatch in config lets you develop against a directly reachable S/4 system before BTP Destination/Connectivity services are provisioned.
4. **AI Core wired in as an optional layer**: `AI_CORE_ENABLED` defaults to `false`, so the app is fully usable with structured-JSON-only callers (e.g. Joule Studio skills with pre-extracted fields) without any LLM dependency. When enabled, `services/ai_core.py` uses `gen_ai_hub.proxy.langchain.init_llm()` to get a model pointed at a configured AI Core deployment, and `langchain.agents.create_agent()` to build a tool-calling agent per request.
5. **Reports as a download link, not a payload**: since A2A task responses carry text/data parts rather than binary attachments, `report_builder.py` renders the file and returns a token; the agent's response includes a `download_url` that resolves through a plain REST route (`routers/reports.py`), separate from the A2A protocol entirely.
6. **Deployment target**: `manifest.yml` targets SAP BTP Cloud Foundry directly (Python buildpack, `uvicorn` start command, `destination`/`connectivity` service bindings) — no containerization step.
