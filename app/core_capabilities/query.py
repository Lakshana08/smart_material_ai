"""Query capability: read-only S/4HANA lookups - MM03 (material master),
MMBE (stock, and serialized stock), COOIS (production order info)."""

from app.core_capabilities._s4_apis import (
    MATERIAL_SERIAL_NUMBER,
    MATERIAL_STOCK,
    PRODUCTION_ORDER,
    PRODUCT_MASTER,
)
from app.services.odata_utils import extract_count, extract_rows, odata_literal, strip_odata_noise
from app.services.s4_client import get_s4_client

# Caps every live S/4HANA fetch below this module. Without it, a broad filter
# (e.g. "production orders in plant 1710" with no order/material narrowing)
# can pull back thousands of rows straight into the LLM's tool-result message
# and blow the model's context window (seen live: 525k tokens on one COOIS
# fetch). $inlinecount=allpages gets the true total from S/4 in the same
# request, so callers still know if more rows exist beyond this cap.
QUERY_ROW_LIMIT = 200


def _capped(data: dict, rows: list[dict], limit: int = QUERY_ROW_LIMIT) -> tuple[int, bool]:
    total = extract_count(data)
    if total is not None:
        return total, total > limit
    return len(rows), len(rows) >= limit


def query_material_master(product: str) -> dict:
    """MM03 - material/product master data. Descriptions live on the
    related A_ProductDescription entity, reached via to_Description and
    flattened into a plain "descriptions" list here."""
    data = get_s4_client().get(
        PRODUCT_MASTER.path,
        params={
            "$filter": f"Product eq '{odata_literal(product)}'",
            "$expand": "to_Description",
            "$top": QUERY_ROW_LIMIT,
            "$inlinecount": "allpages",
        },
    )
    rows = extract_rows(data)[:QUERY_ROW_LIMIT]
    for row in rows:
        row["descriptions"] = _clean_nested(row.pop("to_Description", None))
    count, truncated = _capped(data, rows)
    return {"product": product, "results": rows, "count": count, "truncated": truncated}


def _clean_nested(nested) -> list[dict]:
    """Normalizes an expanded OData v2 nav property into a plain, cleaned list."""
    if isinstance(nested, dict):
        nested = nested.get("results", [])
    if not isinstance(nested, list):
        return []
    return [strip_odata_noise(entry) for entry in nested]


def query_material_stock(material: str, plant: str | None = None) -> dict:
    """MMBE - stock overview, optionally scoped to a plant. Plant isn't a
    top-level $filter field, so results are flattened and filtered client-side."""
    params = {
        "$filter": f"Material eq '{odata_literal(material)}'",
        "$expand": "to_MatlStkInAcctMod",
        "$top": QUERY_ROW_LIMIT,
        "$inlinecount": "allpages",
    }
    data = get_s4_client().get(MATERIAL_STOCK.path, params=params)
    rows = flatten_stock_rows(extract_rows(data)[:QUERY_ROW_LIMIT], plant)
    count, truncated = _capped(data, rows)
    return {"material": material, "plant": plant, "results": rows, "count": count, "truncated": truncated}


def flatten_stock_rows(rows: list[dict], plant: str | None = None) -> list[dict]:
    """Flattens A_MaterialStock rows into one row per plant/storage-location
    line item, optionally filtered to a plant. Shared with report.py."""
    flattened = []
    for row in rows:
        nested = row.get("to_MatlStkInAcctMod")
        if isinstance(nested, dict):
            nested = nested.get("results", [])
        if not isinstance(nested, list):
            continue
        for entry in nested:
            if plant and entry.get("Plant") != plant:
                continue
            # entry comes from the raw expanded sub-collection, not through
            # extract_rows(), so it still carries __metadata/__deferred noise.
            flattened.append(strip_odata_noise({"Material": row.get("Material"), **entry}))
    return flattened


def query_production_order(
    production_order: str | None = None,
    material: str | None = None,
    plant: str | None = None,
) -> dict:
    """COOIS - production order info, filterable by order/material/plant."""
    filters = []
    if production_order:
        filters.append(f"ManufacturingOrder eq '{odata_literal(production_order)}'")
    if material:
        filters.append(f"Material eq '{odata_literal(material)}'")
    if plant:
        filters.append(f"Plant eq '{odata_literal(plant)}'")
    params = {"$top": QUERY_ROW_LIMIT, "$inlinecount": "allpages"}
    if filters:
        params["$filter"] = " and ".join(filters)
    data = get_s4_client().get(PRODUCTION_ORDER.path, params=params)
    rows = extract_rows(data)[:QUERY_ROW_LIMIT]
    count, truncated = _capped(data, rows)
    return {
        "production_order": production_order,
        "material": material,
        "plant": plant,
        "results": rows,
        "count": count,
        "truncated": truncated,
    }


def query_material_serial_numbers(
    material: str,
    plant: str | None = None,
    serial_number: str | None = None,
) -> dict:
    """MMBE (serialized stock) - stock broken down by serial number/equipment."""
    filters = [f"Material eq '{odata_literal(material)}'"]
    if plant:
        filters.append(f"Plant eq '{odata_literal(plant)}'")
    if serial_number:
        filters.append(f"SerialNumber eq '{odata_literal(serial_number)}'")
    params = {
        "$filter": " and ".join(filters),
        "$top": QUERY_ROW_LIMIT,
        "$inlinecount": "allpages",
    }
    data = get_s4_client().get(MATERIAL_SERIAL_NUMBER.path, params=params)
    rows = extract_rows(data)[:QUERY_ROW_LIMIT]
    count, truncated = _capped(data, rows)
    return {
        "material": material,
        "plant": plant,
        "serial_number": serial_number,
        "results": rows,
        "count": count,
        "truncated": truncated,
    }