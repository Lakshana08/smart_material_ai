"""Query capability: read-only lookups against S/4HANA.

Four real S/4 transactions are wired up, each behind its own function so
the query agent can route explicitly rather than guessing:
  - MM03 (material/product master)     -> query_material_master()
  - MMBE (stock overview)              -> query_material_stock()
  - COOIS (production order info)      -> query_production_order()
  - MMBE (serialized stock)            -> query_material_serial_numbers()
"""

from app.core_capabilities._s4_apis import (
    MATERIAL_SERIAL_NUMBER,
    MATERIAL_STOCK,
    PRODUCTION_ORDER,
    PRODUCT_MASTER,
)
from app.services.odata_utils import extract_rows, strip_odata_noise
from app.services.s4_client import get_s4_client


def query_material_master(product: str) -> dict:
    """MM03 - material/product master data."""
    data = get_s4_client().get(PRODUCT_MASTER.path, params={"$filter": f"Product eq '{product}'"})
    rows = extract_rows(data)
    return {"product": product, "results": rows, "count": len(rows)}


def query_material_stock(material: str, plant: str | None = None) -> dict:
    """MMBE - stock overview, optionally scoped to a plant.

    Plant isn't a top-level filter field on A_MaterialStock (confirmed via
    a live 400 from this system: "Property Plant not found in type
    A_MaterialStockType") - the per-plant/storage-location breakdown is
    nested under the expanded to_MatlStkInAcctMod association, so results
    are flattened into one row per line item and plant filtering happens
    client-side over that nested collection instead of via $filter.
    """
    params = {"$filter": f"Material eq '{material}'", "$expand": "to_MatlStkInAcctMod"}
    data = get_s4_client().get(MATERIAL_STOCK.path, params=params)
    rows = flatten_stock_rows(extract_rows(data), plant)
    return {"material": material, "plant": plant, "results": rows, "count": len(rows)}


def flatten_stock_rows(rows: list[dict], plant: str | None = None) -> list[dict]:
    """Flattens A_MaterialStock rows (with expanded to_MatlStkInAcctMod)
    into one flat row per material/plant/storage-location line item,
    optionally filtered to a single plant. Shared with report.py so a
    material_stock report gets the same real line items as a query.
    """
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
        filters.append(f"ProductionOrder eq '{production_order}'")
    if material:
        filters.append(f"Material eq '{material}'")
    if plant:
        filters.append(f"Plant eq '{plant}'")
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
    filters = [f"Material eq '{material}'"]
    if plant:
        filters.append(f"Plant eq '{plant}'")
    if serial_number:
        filters.append(f"SerialNumber eq '{serial_number}'")
    data = get_s4_client().get(MATERIAL_SERIAL_NUMBER.path, params={"$filter": " and ".join(filters)})
    rows = extract_rows(data)
    return {
        "material": material,
        "plant": plant,
        "serial_number": serial_number,
        "results": rows,
        "count": len(rows),
    }
