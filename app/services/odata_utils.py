import re
from datetime import datetime, time, timezone
from typing import Any

_ODATA_DATE_RE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


def humanize_odata_dates(value: Any) -> Any:
    """Recursively converts OData v2's /Date(<epoch-ms>)/ literal date format
    into a plain ISO date/datetime string. Intended for data handed to an LLM
    for narration - confirmed live that the model converts these epoch-
    millisecond timestamps to a human date unreliably (three different,
    all-wrong dates across three tries narrating the same real field), which
    is exactly the kind of computation this app's architecture keeps out of
    the LLM's hands. Structured/direct callers are unaffected - only the
    free-text tool wrappers apply this before returning to the LLM."""
    if isinstance(value, dict):
        return {k: humanize_odata_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [humanize_odata_dates(v) for v in value]
    if isinstance(value, str):
        match = _ODATA_DATE_RE.match(value)
        if match:
            dt = datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
            return dt.date().isoformat() if dt.timetz().replace(tzinfo=None) == time(0, 0) else dt.isoformat()
    return value


def odata_literal(value: str) -> str:
    """Escapes a value for a $filter string literal (OData doubles `'` as
    `''`) - without this, a quote in the value can inject extra clauses."""
    return value.replace("'", "''")


def extract_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalizes OData v2/v4 response shapes into a plain list of rows."""
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("d"), dict):
        rows = data["d"].get("results", [])
    else:
        rows = data.get("value", [])
    return [strip_odata_noise(row) for row in rows]


def extract_count(data: dict[str, Any]) -> int | None:
    """Returns the true total row count from an OData v2 `$inlinecount=allpages`
    response (`d.__count`) or an OData v4 `$count=true` response
    (`@odata.count`) - None if the caller didn't request a count."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("d"), dict) and "__count" in data["d"]:
        try:
            return int(data["d"]["__count"])
        except (TypeError, ValueError):
            return None
    if "@odata.count" in data:
        try:
            return int(data["@odata.count"])
        except (TypeError, ValueError):
            return None
    return None


def strip_odata_noise(row: dict[str, Any]) -> dict[str, Any]:
    """Strips SAP's `__metadata` and unexpanded `__deferred` nav-property
    stubs, leaving only real business fields."""
    if not isinstance(row, dict):
        return row
    cleaned = {}
    for key, value in row.items():
        if key == "__metadata":
            continue
        if isinstance(value, dict) and "__deferred" in value:
            continue
        cleaned[key] = value
    return cleaned