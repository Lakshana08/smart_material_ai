from typing import Any


def extract_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalizes OData v2 (`{"d": {"results": [...]}}`) and v4
    (`{"value": [...]}`) response shapes into a plain list of rows, with
    SAP's OData boilerplate stripped from each row.
    """
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("d"), dict):
        rows = data["d"].get("results", [])
    else:
        rows = data.get("value", [])
    return [strip_odata_noise(row) for row in rows]


def strip_odata_noise(row: dict[str, Any]) -> dict[str, Any]:
    """Removes SAP OData boilerplate - the `__metadata` block and
    unexpanded navigation-property `__deferred` stubs - leaving only real
    business fields. These add no value once a response leaves the OData
    layer (they're not followed further downstream) and roughly double
    payload size on entities with several navigation properties.
    """
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