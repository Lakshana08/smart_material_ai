# Smart Material AI

A FastAPI application on **SAP BTP Cloud Foundry** that exposes four independent [A2A](https://a2a-protocol.org/) (Agent-to-Agent) agents for querying, acting on, reporting on, and evaluating business-rule status against material data — designed to be registered as skills in SAP Joule Studio, or called by any other A2A-compatible client (or plain JSON-RPC, e.g. from Postman).

Built for Jabil's Suzhou GP site's material shortage / inventory / aging use case (see `Spec/` for the Functional and Technical Specification documents this implements).

## What it does, end to end

Three of the four agents talk to a **live SAP S/4HANA system** over OData; the fourth evaluates **deterministic business rules** against four raw SAP report exports loaded from local Excel files, with no live SAP connection involved at all:

| Agent | Path | Data source | Purpose |
|---|---|---|---|
| Material Query Agent | `/a2a/query` | Live S/4HANA (OData) | Material master (MM03), stock overview (MMBE), production orders (COOIS) |
| Material Action Agent | `/a2a/action` | Live S/4HANA (OData) | Create/update/delete material master (MM01/MM02) |
| Material Report Agent | `/a2a/report` | Live S/4HANA (OData) | Downloadable reports (PDF/Excel/CSV) |
| **Status Agent** | `/a2a/status` | **`sample_data/*.xlsx`** | The 4 business rules: Non-Controlled Material, Over-Control / Over-Issued Material, Aging, Machine-Head Material Review |

Each agent advertises its own `AgentCard` at `/a2a/<agent>/.well-known/agent-card.json` and accepts JSON-RPC task requests (`method: "SendMessage"`) at `/a2a/<agent>/`.

### Dual input path (all 4 agents)

Every agent supports two ways of being called, and this is the core design principle of the whole app: **the LLM only ever routes and narrates — it never computes.**

- **Structured JSON** (e.g. a Joule Studio skill invocation with pre-extracted fields, or a machine caller) — dispatched directly to the relevant business-logic function. **Zero LLM calls.**
- **Plain free text** (English or Mandarin) — routed through a LangChain tool-calling agent backed by **SAP AI Core** (`services/ai_core.py`). The model picks which tool(s) to call and extracts parameters, the tool runs as plain Python and returns a computed result, then the model narrates that result into a sentence. **Exactly two LLM calls per turn**, regardless of how many tools get used — one to route, one to narrate.

`AI_CORE_ENABLED` (in `.env`) controls whether the free-text path is available at all; when `false`, every agent still works, but only via structured JSON.

## Architecture

```
A2A client (Joule Studio, Postman, curl, etc.)
        │  JSON-RPC (method: SendMessage)
        ▼
app/main.py  ──mounts──►  app/a2a_agents/{query,action,report,status}_agent.py
                                  │
                    (structured JSON)        (free text)
                                  │                │
                                  ▼                ▼
                     core_capabilities/*      services/ai_core.py
                                  │             (gen_ai_hub + LangChain
                                  │              tool-calling agent —
                                  │              picks a tool, narrates
                                  │              its result; never
                                  │              computes anything)
                                  │                │
                     ┌────────────┴────────────────┘
                     ▼                              ▼
     query.py / action.py / report.py      material_status.py
                     │                              │
                     ▼                              ▼
          services/s4_client.py             app/engine/*.py
                     │                    (order_status, material_status,
                     ▼                     aging, machine_head — pure
        services/destination.py            Python, zero LLM code, zero
             │            │                network calls)
    (BTP Destination) (BTP Connectivity,          │
             │           if OnPremise)             ▼
             ▼            ▼                app/engine/loader.py
     S/4HANA OData services                        │
   (Cloud or on-prem via                            ▼
      Cloud Connector)                    sample_data/*.xlsx
```

The two right-hand branches never cross: the Status Agent's business rules have no
dependency on `s4_client.py`, and the other three agents have no dependency on
`app/engine/`. If live S/4 data ever needs to back the same 4 business rules instead of
Excel, that's a new `s4_adapter.py` sitting behind the same `core_capabilities` call —
not a rewrite of the rules themselves.

Report generation additionally goes through `services/report_builder.py`, which renders
the file in memory, stores it under a short-lived token, and returns a `download_url`
served by `routers/reports.py` (`GET /reports/download/{token}`) — A2A responses carry
text/data parts, not binary attachments.

## The 4 business rules (Status Agent)

Implemented in `app/engine/*.py` — pure Python, no LLM, no pandas (plain `openpyxl` +
dict/list processing). Verified against the real `sample_data/` files:

| Rule | Formula | Verified result on sample data |
|---|---|---|
| Non-Controlled Material (无管制料) | Material+StorageLocation has no matching production-order requirement in the 14C component data | 1,678 records |
| Over-Control / Over-Issued Material (领超管制) | `Unrestricted Stock − Controlled Remaining Issuance < 0` | 68 records |
| Aging | `Aging Days > 14` (fixed threshold) | 499 of 2,644 records |
| Machine-Head Material Review (机头料) | `order_type = ZNPC` + prior GI issuance history — **candidates for human review only, never a resolved status** | 0 on this sample set (see note below) |

Orders flagged `NMVT` in COOIS are excluded before any rule runs (`app/engine/order_status.py`).

Broad, unscoped questions (e.g. "show non-controlled material in the warehouse") are
valid — each rule returns the true total count plus up to 50 matching rows
(`truncated: true` if more exist), rather than requiring the caller to narrow down
first or dumping thousands of rows into an LLM's context.

**Data-quality note found during verification:** the `Order` numbers in
`RAW_PP134_OrderMaster.xlsx` and `RAW_14C_ComponentIssuance.xlsx` have zero overlap in
the current sample set (checked across all order types, not just ZNPC) — this is why
machine-head review returns 0 candidates; it's a property of the sample data, not a
bug in the rule.

**Not implemented** (no confirmed rule per the Functional Spec's open questions):
"Buck material" exclusion, and "-S" suffix material handling.

## File structure

```
smart_material_ai/
├── app/
│   ├── main.py                    # FastAPI entrypoint - builds & mounts the 4 A2A sub-apps + /health + reports router
│   │
│   ├── a2a_agents/                # One AgentExecutor per A2A agent
│   │   ├── common.py              #   shared AgentCard builder, structured-input detection, response-message helper
│   │   ├── query_agent.py         #   Material Query Agent - MM03/MMBE/COOIS lookups (live S/4)
│   │   ├── action_agent.py        #   Material Action Agent - MM01/MM02 create/update/delete (live S/4)
│   │   ├── report_agent.py        #   Material Report Agent - PDF/Excel/CSV report generation (live S/4)
│   │   └── status_agent.py        #   Status Agent - 4 business rules (sample_data/ Excel only)
│   │
│   ├── core_capabilities/         # Business logic - what each agent can actually do, independent of A2A/LLM plumbing
│   │   ├── _s4_apis.py            #   registry of S/4 OData service paths, key fields, plant-filter support
│   │   ├── query.py               #   read-only lookups (material master / stock / production order / serial numbers)
│   │   ├── action.py              #   create/update/delete, scoped per-resource to SAP's actual capabilities
│   │   ├── report.py              #   fetches rows for a report_type and hands them to report_builder
│   │   └── material_status.py     #   bridges Status Agent's tools to app/engine/* - zero LLM code
│   │
│   ├── engine/                    # Deterministic business rules for the Status Agent - pure Python, zero LLM
│   │   ├── loader.py              #   reads sample_data/*.xlsx into plain lists of dicts (openpyxl only, no pandas)
│   │   ├── order_status.py        #   COOIS NMVT exclusion filter
│   │   ├── material_status.py     #   Non-Controlled + Over-Control (shared Material+StorageLocation join)
│   │   ├── aging.py                #   >14-day aging threshold
│   │   └── machine_head.py         #   machine-head candidate surfacing - needs_review flag only, never resolved
│   │
│   ├── services/                  # Infrastructure / integration concerns
│   │   ├── s4_client.py           #   generic OData v2/v4 HTTP client (CSRF handling, JSON format, error wrapping)
│   │   ├── destination.py         #   resolves SAP BTP Destination (+ Connectivity proxy for on-prem systems)
│   │   ├── odata_utils.py         #   normalizes OData v2/v4 response shapes, strips __metadata/__deferred noise
│   │   ├── ai_core.py             #   LangChain tool-calling agent backed by SAP AI Core - the ONLY place any agent calls an LLM
│   │   └── report_builder.py      #   renders CSV/XLSX/PDF in memory, token-keyed short-lived storage
│   │
│   ├── core/
│   │   └── config.py              # pydantic-settings Settings (.env-backed) - destinations, AI Core toggle, public URL
│   │
│   └── routers/
│       └── reports.py             # GET /reports/download/{token} - streams a generated report file
│
├── sample_data/                    # The 4 raw SAP report exports the Status Agent reads
│   ├── RAW_PP134_OrderMaster.xlsx        # PP-134 - work order master data
│   ├── RAW_14C_ComponentIssuance.xlsx    # 14C - component issuance / theoretical vs actual usage
│   ├── RAW_COOIS_OrderStatus.xlsx        # COOIS - order status flags (NMVT exclusion)
│   └── RAW_ZAGEDINV_Inventory.xlsx       # Z_AGEDINV - stock and aging by material/storage location
│
├── Spec/                           # Functional & Technical Specification source documents
├── manifest.yml                    # Cloud Foundry deployment manifest (buildpack, start command, bound services)
├── .env.example                    # Local dev config template (copy to .env, gitignored)
├── requirements.txt                # Python dependencies
└── README.md
```

## Running it locally

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Confirm it's up:
```powershell
curl http://localhost:8000/health
curl http://localhost:8000/a2a/status/.well-known/agent-card.json
```

Configuration lives in `.env` (copy from `.env.example`):
- `S4_BASE_URL` / `S4_USERNAME` / `S4_PASSWORD` — direct-connect S/4 for local dev, or leave empty to use `S4_DESTINATION_NAME` via BTP Destination/Connectivity in Cloud Foundry.
- `AI_CORE_ENABLED` — `false` disables the free-text path entirely (structured JSON still works on all 4 agents); `true` requires the `AICORE_*` credentials below it.
- `APP_PUBLIC_URL` — the base URL each agent advertises in its `AgentCard`.

## Testing an agent directly (JSON-RPC)

The wire protocol is JSON-RPC 2.0 with method `SendMessage`, and requires the header
`a2a-version: 1.0` (without it, the server rejects the request as an unsupported older
protocol version). Structured example against the Status Agent:

```bash
curl -X POST http://localhost:8000/a2a/status/ \
  -H "Content-Type: application/json" \
  -H "a2a-version: 1.0" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendMessage",
    "params": {
      "message": {
        "messageId": "11111111-1111-1111-1111-111111111111",
        "role": "ROLE_USER",
        "parts": [ { "data": { "check_type": "over_control", "material": "ACPE21667" } } ]
      },
      "configuration": {}
    }
  }'
```

Swap `parts` for `[{ "text": "Is material ACPE21667 over-control?" }]` to exercise the
free-text/LLM path instead. The `data` payload shape differs per agent:

- **Query** (`/a2a/query/`): `{ "query_type": "material_stock" | "material_master" | "production_order", ... }`
- **Action** (`/a2a/action/`): `{ "material_number": "...", "action": "update_status" | "update_description" | "create_material" | "delete_description", "payload": {...} }` — **mutates real S/4HANA data**
- **Report** (`/a2a/report/`): `{ "report_type": "material_stock" | "material_master" | "production_order", "report_format": "pdf" | "xlsx" | "csv", ... }`
- **Status** (`/a2a/status/`): `{ "check_type": "non_controlled" | "over_control" | "aging" | "machine_head_review", ... }`

## How it was built

1. **Transport layer first**: `app/main.py` mounts four independently-addressable A2A sub-apps (one per agent), each with its own `AgentCard` and JSON-RPC endpoint, using the `a2a-sdk` package.
2. **Business logic decoupled from transport**: each agent's `AgentExecutor` is a thin adapter — it extracts input, and either dispatches straight to a business-logic function (structured JSON path) or hands the same functions to `services/ai_core.run_agent()` as LangChain tools (free-text path). The business-logic functions themselves have no knowledge of A2A or LLMs.
3. **Two independent data paths behind the same pattern**: Query/Action/Report go through `core_capabilities/*.py` → `services/s4_client.py` → live S/4HANA OData. Status goes through `core_capabilities/material_status.py` → `app/engine/*.py` → `app/engine/loader.py` → `sample_data/*.xlsx`. Neither path depends on the other.
4. **AI Core wired in as an optional layer**: `AI_CORE_ENABLED` defaults to `false`. When enabled, `services/ai_core.py` uses `gen_ai_hub.proxy.langchain.init_llm()` and `langchain.agents.create_agent()` to build a tool-calling agent per request — exactly two LLM calls per free-text turn (route, then narrate), never for computation. The AI Core deployment lookup is cached (`lru_cache`) rather than hit on every request, and tool-level failures (e.g. S/4 connectivity errors) are logged distinctly from actual AI Core/LLM failures.
5. **Business rules stay governed, not LLM-inferred**: every formula in `app/engine/*.py` is plain, synchronous Python with zero imports from `langchain`/`gen_ai_hub` — a structural guarantee that the LLM can route to a rule and narrate its result, but can never compute one itself. Machine-head review results are forced to carry a human-review disclaimer server-side, appended after the model's narration, not left to the model's phrasing.
6. **Reports as a download link, not a payload**: since A2A task responses carry text/data parts rather than binary attachments, `report_builder.py` renders the file and returns a token; the agent's response includes a `download_url` that resolves through a plain REST route (`routers/reports.py`).
7. **Deployment target**: `manifest.yml` targets SAP BTP Cloud Foundry directly (Python buildpack, `uvicorn` start command, `destination`/`connectivity` service bindings).
