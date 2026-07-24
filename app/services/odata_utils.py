from typing import Any


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