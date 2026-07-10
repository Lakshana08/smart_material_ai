"""Resolves an SAP BTP Destination and, for on-premise destinations, the
Connectivity service proxy needed to reach it through Cloud Connector.

Pattern: read service credentials from VCAP_SERVICES, get an OAuth token
from the destination service's own UAA, call the Destination Configuration
API to resolve the target system, and - if ProxyType is OnPremise - get a
second token from the Connectivity service's UAA to use as the
Proxy-Authorization header.

This is deliberately auth-type-agnostic: BasicAuthentication and
NoAuthentication destinations "just work" once resolved, because the
destination response already carries what's needed. PrincipalPropagation
is the one exception - it requires forwarding an end-user SAP identity
token that these A2A agents don't currently receive, so it's left as an
explicit NotImplementedError with guidance rather than silently behaving
like NoAuthentication.
"""

import threading
import time
from dataclasses import dataclass

import requests
from cfenv import AppEnv

from app.core.config import get_settings

_DESTINATION_CACHE_TTL_SECONDS = 300


@dataclass
class ResolvedDestination:
    url: str
    proxy_type: str  # "Internet" | "OnPremise"
    authentication: str  # e.g. "BasicAuthentication", "NoAuthentication", "OAuth2ClientCredentials"
    username: str | None = None
    password: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_authorization: str | None = None

    @property
    def requests_proxies(self) -> dict[str, str] | None:
        if self.proxy_type != "OnPremise":
            return None
        proxy_url = f"http://{self.proxy_host}:{self.proxy_port}"
        return {"http": proxy_url, "https": proxy_url}

    @property
    def extra_headers(self) -> dict[str, str]:
        headers = {}
        if self.proxy_type == "OnPremise" and self.proxy_authorization:
            headers["Proxy-Authorization"] = f"Bearer {self.proxy_authorization}"
        return headers


class DestinationService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[ResolvedDestination, float]] = {}
        self._env = AppEnv()

    def resolve(self, destination_name: str) -> ResolvedDestination:
        settings = get_settings()
        if settings.s4_base_url:
            return self._static_destination()

        with self._lock:
            cached = self._cache.get(destination_name)
            if cached and cached[1] > time.time():
                return cached[0]

        resolved = self._resolve_live(destination_name)

        with self._lock:
            self._cache[destination_name] = (resolved, time.time() + _DESTINATION_CACHE_TTL_SECONDS)
        return resolved

    def _static_destination(self) -> ResolvedDestination:
        settings = get_settings()
        has_credentials = bool(settings.s4_username)
        return ResolvedDestination(
            url=settings.s4_base_url,
            proxy_type="Internet",
            authentication="BasicAuthentication" if has_credentials else "NoAuthentication",
            username=settings.s4_username or None,
            password=settings.s4_password or None,
        )

    def _resolve_live(self, destination_name: str) -> ResolvedDestination:
        dest_service = self._env.get_service(label="destination")
        if dest_service is None:
            raise RuntimeError("No 'destination' service bound to this app (check manifest.yml / VCAP_SERVICES)")

        token = self._fetch_uaa_token(
            token_url=dest_service.credentials["url"] + "/oauth/token",
            client_id=dest_service.credentials["clientid"],
            client_secret=dest_service.credentials["clientsecret"],
        )

        config_url = (
            f"{dest_service.credentials['uri']}/destination-configuration/v1/destinations/{destination_name}"
        )
        resp = requests.get(config_url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        dest_config = payload["destinationConfiguration"]

        authentication = dest_config.get("Authentication", "NoAuthentication")
        if authentication == "PrincipalPropagation":
            raise NotImplementedError(
                "PrincipalPropagation destinations require forwarding an end-user SAP identity "
                "token, which these A2A agents do not currently receive. Use BasicAuthentication "
                "or OAuth2ClientCredentials on this destination instead."
            )

        resolved = ResolvedDestination(
            url=dest_config["URL"],
            proxy_type=dest_config.get("ProxyType", "Internet"),
            authentication=authentication,
            username=dest_config.get("User"),
            password=dest_config.get("Password"),
        )

        if resolved.proxy_type == "OnPremise":
            conn_service = self._env.get_service(label="connectivity")
            if conn_service is None:
                raise RuntimeError("Destination is OnPremise but no 'connectivity' service is bound")
            proxy_token = self._fetch_uaa_token(
                token_url=conn_service.credentials["url"] + "/oauth/token",
                client_id=conn_service.credentials["clientid"],
                client_secret=conn_service.credentials["clientsecret"],
            )
            resolved.proxy_host = conn_service.credentials["onpremise_proxy_host"]
            resolved.proxy_port = int(conn_service.credentials["onpremise_proxy_port"])
            resolved.proxy_authorization = proxy_token

        return resolved

    @staticmethod
    def _fetch_uaa_token(token_url: str, client_id: str, client_secret: str) -> str:
        resp = requests.post(
            token_url,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


_service = DestinationService()


def get_destination_service() -> DestinationService:
    return _service