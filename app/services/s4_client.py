"""Generic OData v2/v4 client for the S/4HANA system resolved via
DestinationService. Handles CSRF token fetch/retry for mutating calls
(OData v2 requires this for POST/PATCH/DELETE) and injects the Connectivity
proxy transparently when the destination is on-premise.

The exact OData service/entity paths used by app/core_capabilities/*.py are
placeholders pending the real S/4 API details - only the request/response
plumbing below needs to be correct now.
"""

from typing import Any

import requests

from app.core.config import get_settings
from app.services.destination import DestinationService, ResolvedDestination, get_destination_service


class S4ClientError(Exception):
    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"S/4 request failed ({status_code}): {detail}")


class S4Client:
    def __init__(self, destination_service: DestinationService, destination_name: str):
        self._destination_service = destination_service
        self._destination_name = destination_name
        self._csrf_token: str | None = None
        self._csrf_cookies = None

    def get(self, path: str, params: dict | None = None) -> dict:
        resolved = self._resolve()
        kwargs = self._base_kwargs(resolved)
        kwargs["headers"]["Accept"] = "application/json"
        # This system's SAP Gateway returns XML unless $format=json is
        # passed explicitly as a query param, not just via the Accept header.
        merged_params = {"$format": "json", **(params or {})}
        resp = requests.get(self._url(resolved, path), params=merged_params, **kwargs)
        self._raise_for_odata_error(resp)
        return resp.json()

    def post(self, path: str, json_body: dict | None = None) -> dict:
        return self._mutate("POST", path, json_body)

    def patch(self, path: str, json_body: dict | None = None) -> dict:
        return self._mutate("PATCH", path, json_body)

    def _mutate(self, method: str, path: str, json_body: dict | None) -> dict:
        resolved = self._resolve()
        self._ensure_csrf_token(resolved, path)

        def attempt() -> requests.Response:
            kwargs = self._base_kwargs(resolved)
            kwargs["headers"]["Accept"] = "application/json"
            kwargs["headers"]["X-CSRF-Token"] = self._csrf_token
            kwargs["cookies"] = self._csrf_cookies
            return requests.request(method, self._url(resolved, path), json=json_body, **kwargs)

        resp = attempt()
        if resp.status_code == 403 and "csrf" in resp.headers.get("X-CSRF-Token", "").lower():
            self._csrf_token = None
            self._ensure_csrf_token(resolved, path)
            resp = attempt()

        self._raise_for_odata_error(resp)
        return resp.json() if resp.content else {}

    def _ensure_csrf_token(self, resolved: ResolvedDestination, path: str) -> None:
        if self._csrf_token:
            return
        kwargs = self._base_kwargs(resolved)
        kwargs["headers"]["X-CSRF-Token"] = "Fetch"
        # Fetch against the entity SET (key predicate stripped), not the exact
        # keyed entity being mutated - the token isn't tied to a specific
        # entity, and the keyed path 404s whenever that entity doesn't exist
        # yet (e.g. PATCHing a Product/Language description combo that
        # hasn't been created), which would otherwise block CSRF fetch for
        # every subsequent POST/create fallback too. Must still be an actual
        # service path, not the bare host root - GETting resolved.url alone
        # 404s since there's no service mounted there.
        entity_set_path = path.split("(", 1)[0]
        resp = requests.get(self._url(resolved, entity_set_path), **kwargs)
        resp.raise_for_status()
        self._csrf_token = resp.headers.get("X-CSRF-Token")
        self._csrf_cookies = resp.cookies

    def _resolve(self) -> ResolvedDestination:
        return self._destination_service.resolve(self._destination_name)

    @staticmethod
    def _url(resolved: ResolvedDestination, path: str) -> str:
        return f"{resolved.url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _base_kwargs(resolved: ResolvedDestination) -> dict:
        auth = None
        if resolved.authentication == "BasicAuthentication":
            auth = (resolved.username, resolved.password)
        return {
            "proxies": resolved.requests_proxies,
            "headers": dict(resolved.extra_headers),
            "auth": auth,
            "timeout": 30,
        }

    @staticmethod
    def _raise_for_odata_error(resp: requests.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise S4ClientError(resp.status_code, detail)


_client = S4Client(get_destination_service(), get_settings().s4_destination_name)


def get_s4_client() -> S4Client:
    return _client
