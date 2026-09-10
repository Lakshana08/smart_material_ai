"""Query capability: read-only S/4HANA lookups - MM03 (material master),
MMBE (stock, and serialized stock), COOIS (production order info)."""

from app.core_capabilities._s4_apis import (
    MATERIAL_SERIAL_NUMBER,
    MATERIAL_STOCK,
    PRODUCTION_ORDER,
    PRODUCT_MASTER,
)
from app.services.odata_utils import extract_rows, odata_literal, strip_odata_noise
from app.services.s4_client import get_s4_client


def query_material_master(product: str) -> dict:
    """MM03 - material/product master data. Descriptions live on the
    related A_ProductDescription entity, reached via to_Description and
    flattened into a plain "descriptions" list here."""
    data = get_s4_client().get(
        PRODUCT_MASTER.path,
        params={"$filter": f"Product eq '{odata_literal(product)}'", "$expand": "to_Description"},
    )
    rows = extract_rows(data)
    for row in rows:
        row["descriptions"] = _clean_nested(row.pop("to_Description", None))
    return {"product": product, "results": rows, "count": len(rows)}


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
    params = {"$filter": f"Material eq '{odata_literal(material)}'", "$expand": "to_MatlStkInAcctMod"}
    data = get_s4_client().get(MATERIAL_STOCK.path, params=params)
    rows = flatten_stock_rows(extract_rows(data), plant)
    return {"material": material, "plant": plant, "results": rows, "count": len(rows)}


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
    params = {"$filter": " and ".join(filters)} if filters else None
    data = get_s4_client().get(PRODUCTION_ORDER.path, params=params)
    rows = extract_rows(data)
    return {
        "production_order": production_order,
        "material": material,
        "plant": plant,
        "results": rows,
        "count": len(rows),
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
    data = get_s4_client().get(MATERIAL_SERIAL_NUMBER.path, params={"$filter": " and ".join(filters)})
    rows = extract_rows(data)
    return {
        "material": material,
        "plant": plant,
        "serial_number": serial_number,
        "results": rows,
        "count": len(rows),
    }