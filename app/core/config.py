from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # BTP Destination resolving S/4HANA - used only when s4_base_url is empty.
    s4_destination_name: str = "S4HANA_MATERIAL_API"

    # Direct-connect mode for local dev - bypasses BTP Destination/Connectivity.
    s4_base_url: str = ""
    s4_username: str = ""
    s4_password: str = ""

    # Base URL this app is reachable at - used in AgentCard URLs and report links.
    app_public_url: str = "http://localhost:8000"

    report_download_ttl_seconds: int = 60 * 15

    # AI Core credentials (AICORE_CLIENT_ID etc.) are read from the process
    # environment directly by the gen_ai_hub SDK, not through this class.
    ai_core_enabled: bool = False
    ai_core_model_name: str = "gpt-4o-mini"

    # Preferred over ai_core_model_name when set - skips the catalog lookup.
    llm_deployment_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()