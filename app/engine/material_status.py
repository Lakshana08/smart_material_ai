"""Non-Controlled Material and Over-Control / Over-Issued Material rules
(Technical Spec §6.1 / §6.2). Both come from the same Material+StorageLocation
lookup, so one function computes both statuses - callers filter by status
afterward (see core_capabilities/material_status.py).

Pure Python, no LLM, no pandas - operates on the plain lists of dicts
produced by engine/loader.py.
"""

from datetime import datetime, timezone

from app.engine.order_status import excluded_orders


def compute_material_status(
    components: list[dict],
    stock: list[dict],
    order_status: list[dict],
    *,
    work_order: str | None = None,
    material: str | None = None,
    storage_location: str | None = None,
) -> list[dict]:
    excluded = excluded_orders(order_status)

    # Controlled remaining issuance, grouped by (Component, Storage Location),
    # summed across every open (non-excluded) order that touches that key.
    requirement: dict[tuple, dict] = {}
    for row in components:
        if row.get("Order") in excluded:
            continue
        key = (row.get("Component"), row.get("Storage Location"))
        if key[0] is None or key[1] is None:
            continue  # blank component/storage location - can't be keyed, skip
        theoretical_usage = row.get("TheoUsg (WtScp)") or 0
        net_gi_co11n = row.get("Net GI (CO11N)") or 0
        net_gi_other = row.get("Net GI (Other)") or 0
        remaining = theoretical_usage - net_gi_co11n - net_gi_other

        entry = requirement.setdefault(key, {"controlled_remaining_issuance": 0, "orders": set()})
        entry["controlled_remaining_issuance"] += remaining
        entry["orders"].add(row.get("Order"))

    # Join stock (Material + Storage Location) against that requirement.
    results = []
    for row in stock:
        stock_material, stock_location = row.get("Material"), row.get("Storage Location")
        req = requirement.get((stock_material, stock_location))
        unrestricted_stock = row.get("Unrestricted") or 0

        if req is None:
            status = "non_controlled"
            controlled_remaining_issuance = None
            available_to_issue_qty = None
            orders: list = []
        else:
            controlled_remaining_issuance = req["controlled_remaining_issuance"]
            available_to_issue_qty = unrestricted_stock - controlled_remaining_issuance
            status = "over_control" if available_to_issue_qty < 0 else "available"
            orders = sorted(req["orders"])

        results.append(
            {
                "material": stock_material,
                "storage_location": stock_location,
                "plant": row.get("Plant"),
                "unrestricted_stock": unrestricted_stock,
                "controlled_remaining_issuance": controlled_remaining_issuance,
                "available_to_issue_qty": available_to_issue_qty,
                "material_status": status,
                "orders": orders,
                "data_as_of": datetime.now(timezone.utc).isoformat(),
            }
        )

    if material:
        results = [r for r in results if r["material"] == material]
    if storage_location:
        results = [r for r in results if r["storage_location"] == storage_location]
    if work_order:
        results = [r for r in results if work_order in r["orders"]]

    return results
