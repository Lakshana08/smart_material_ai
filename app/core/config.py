from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SAP Destination service - resolves the S/4HANA system these agents call.
    # Used only when s4_base_url below is empty (i.e. the real BTP deployment path).
    s4_destination_name: str = "S4HANA_MATERIAL_API"

    # Direct-connect mode: when set, S4Client talks to this host directly
    # instead of resolving a BTP Destination - useful for developing/testing
    # against a directly reachable S/4 system (e.g. over VPN) before the
    # Destination/Connectivity services are provisioned. Leave empty to use
    # the full BTP Destination path in Cloud Foundry.
    s4_base_url: str = ""
    s4_username: str = ""
    s4_password: str = ""

    # Public base URL this app is reachable at once deployed - used to build
    # each A2A agent's AgentCard.supported_interfaces[].url and the report
    # download links returned by report_tool.
    app_public_url: str = "http://localhost:8000"

    report_download_ttl_seconds: int = 60 * 15

    # SAP AI Core - direct Foundation Model access (gen_ai_hub.proxy.native.openai),
    # used by the 3 agents to (a) parse free-text task input into parameters
    # and (b) phrase natural-language response summaries. When False, agents
    # only accept structured JSON input and use their own template summaries
    # (current behavior, unaffected). NOTE: credentials themselves
    # (AICORE_CLIENT_ID/CLIENT_SECRET/AUTH_URL/BASE_URL/RESOURCE_GROUP) are
    # read directly from the process environment by the gen_ai_hub SDK, not
    # through this Settings class - see main.py's load_dotenv() call.
    ai_core_enabled: bool = False
    ai_core_model_name: str = "gpt-4o-mini"

    # If set, identifies the exact AI Core deployment to call directly
    # (preferred - skips the model_name catalog lookup). Falls back to
    # ai_core_model_name above when empty.
    llm_deployment_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()