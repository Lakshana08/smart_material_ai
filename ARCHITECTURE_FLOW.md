# Smart Material AI — Architecture Flow Document

## 1. Overview

The Smart Material AI Platform is a Python application running on **SAP BTP Cloud Foundry**, exposed to end users through desktop/mobile clients and Microsoft Teams, and backed by SAP's Generative AI Hub for LLM-driven agents that query, act on, and report against SAP HANA Cloud and on-premise SAP data. The platform also exposes integration surfaces (MCP Server / API) so external AI agents and clients (Google Cloud, Microsoft Azure, AWS, IBM Cloud, others) can participate as first-class consumers.

## 2. Components

| Layer | Component | Purpose |
|---|---|---|
| Clients | Mobile/Desktop Application Clients | Direct end-user access to the platform |
| Clients | Microsoft Teams Interface | Chat-based access via Teams |
| Identity | Microsoft (Teams identity) | Authenticates Teams users |
| Application | Smart Material AI Platform (Cloud Foundry, Python) | Core app: Teams Adapter, API Orchestration, Authentication & Authorization, Data Processing & Mapping, Business Logic |
| Connectivity | SAP Destination Service | Resolves target system endpoints/credentials |
| Connectivity | SAP Connectivity Service | Secure tunnel to on-premise systems |
| Data | SAP HANA Cloud | Primary cloud data store for business data |
| On-Premise | Cloud Connector | Reverse-invoke proxy bridging BTP to on-prem landscape |
| On-Premise | SAP On-Premise Solutions | Backend ERP/business systems (e.g., S/4HANA) |
| Conversational AI | SAP Joule User Interface | Conversational entry point into the Generative AI Hub |
| Generative AI Hub | SAP AI Launchpad | Management UI for AI Core assets |
| Generative AI Hub | SAP AI Core — Prompt Registry & Optimization | Manages and tunes prompts |
| Generative AI Hub | Orchestration (Grounding, Templating, Data Masking, I/O Filtering, Translation) | Pipeline that wraps every LLM call with grounding, PII masking, filtering, translation |
| Generative AI Hub | Foundation Model Access (Partner-built, SAP-built) | Abstraction over model providers |
| Generative AI Hub | Foundation Models (SAP-hosted) | The underlying LLMs |
| AI Agents (Cloud Foundry) | SAP HANA Query Agent | Retrieves business data from SAP HANA Cloud using LLM-generated SQL |
| AI Agents (Cloud Foundry) | SAP HANA Action Agent | Creates/updates business objects and performs business processes via SAP RFC/BAPI |
| AI Agents (Cloud Foundry) | Report Generation Agent | Generates reports/exports (PDF/Excel/CSV) |
| Identity | SAP Cloud Identity Services | Central IdP/authorization for the BTP subaccount and its AI agents |
| 3rd Party | MCP Server / API | Exposes platform capabilities to external tools |
| 3rd Party | AI Agents & Clients (Google Cloud, Azure, AWS, IBM Cloud, others) | External AI ecosystems consuming the platform via MCP/API |
| 3rd Party | 3rd-party Identity Provider | Authenticates/authorizes external agents and clients |

## 3. End-to-End Flows

### Flow A — User interaction (chat/app)
1. A user opens the **Mobile/Desktop Application Client** or the **Microsoft Teams Interface**.
2. Requests hit the **Smart Material AI Platform** (Python app on Cloud Foundry). Teams traffic is normalized by the **Teams Adapter**; all traffic passes through **API Orchestration**.
3. **Authentication & Authorization** validates the caller against **SAP Cloud Identity Services**.
4. **Data Processing & Mapping** and **Business Logic** decide whether the request needs live business data, an AI-driven action, or both.

### Flow B — Direct data access (SAP HANA Cloud)
5. For straightforward data needs, the platform queries **SAP HANA Cloud** directly.

### Flow C — On-premise data access
6. For on-premise data, the platform calls out via the **SAP Destination Service** (endpoint/credential resolution) and **SAP Connectivity Service** (secure channel).
7. Traffic crosses the **network boundary** over **HTTPS** to the **Cloud Connector**, which reverse-proxies into **SAP On-Premise Solutions**.

### Flow D — Generative AI / conversational flow
8. Conversational or AI-augmented requests are routed to the **SAP Joule User Interface**, which has a **trust relationship** with the **Generative AI Hub**.
9. Inside the hub, **SAP AI Core** pulls the appropriate prompt from the **Prompt Registry & Optimization** store.
10. The request passes through the **Orchestration** pipeline: **Grounding** → **Templating** → **Data Masking** → **I/O Filtering** → **Translation** (order configurable per use case).
11. **Foundation Model Access** routes the orchestrated request to a **Foundation Model** (SAP-hosted, partner-built or SAP-built) and returns the LLM response back through the same orchestration pipeline (output filtering/masking applied symmetrically).
12. **SAP AI Launchpad** is used out-of-band by administrators to manage/monitor AI Core assets (not part of the runtime request path).

### Flow E — Agentic execution
13. When the LLM response requires querying or acting on business data, the Generative AI Hub invokes one of the **AI Agents** (second Cloud Foundry space):
    - **SAP HANA Query Agent** — generates SQL via LLM and retrieves data from SAP HANA Cloud.
    - **SAP HANA Action Agent** — creates/updates business objects and triggers business processes via SAP RFC/BAPI (this in turn can reach on-premise systems through Flow C).
    - **Report Generation Agent** — compiles results into PDF/Excel/CSV.
14. All AI Agents authenticate/authorize through **SAP Cloud Identity Services** before touching HANA/on-prem systems.
15. Results flow back up through the Generative AI Hub → SAP Joule UI (or directly to API Orchestration) → back to the originating client (Teams or App).

### Flow F — Third-party ecosystem integration
16. External tools and AI agents (Google Cloud, Microsoft Azure, AWS, IBM Cloud, others) reach the platform through the exposed **MCP Server** and **API** in the 3rd-party zone, across the network boundary.
17. These external callers are authenticated by a **3rd-party Identity Provider / Identity Management**, which maintains a **trust relationship** (federation) with SAP Cloud Identity Services so tokens issued externally are honored inside the BTP subaccount.
18. Once trusted, external agents can invoke the same API Orchestration layer as internal clients, subject to the same Authentication & Authorization checks.

## 4. Trust Boundaries

- **SAP BTP Subaccount boundary** (green) — everything SAP-managed and multi-cloud runs inside; this is the primary security perimeter.
- **NETWORK boundary** (vertical divider) — separates BTP-hosted services from the customer's on-premise landscape and the outside 3rd-party ecosystem; all crossings are over HTTPS via Cloud Connector or the MCP/API surface.
- **TRUST relationships (dashed lines)**:
  - Python Application ⇄ SAP Joule UI ⇄ Generative AI Hub (magenta) — internal trust for conversational AI.
  - 3rd-party Identity Provider ⇄ SAP Cloud Identity Services (teal) — federated trust enabling external AI agents/clients to authenticate against the platform.

## 5. Identity & Security Notes

- **SAP Cloud Identity Services** is the single internal IdP: it backs authentication for the Python application, the AI Agents Cloud Foundry space, and indirectly the Generative AI Hub.
- **Data Masking** and **I/O Filtering** in the Orchestration layer are the controls that prevent sensitive business data from leaking to Foundation Models, especially relevant when Foundation Models are partner-hosted.
- External access (3rd-party Tools/Agents) is isolated behind MCP Server/API and a separate identity provider, joined to the internal trust fabric only via explicit federation — internal services never trust 3rd-party callers directly.
