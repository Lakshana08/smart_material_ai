"""Aggregate counts for the dashboard sidebar/KPI widgets - business-friendly
summary numbers, optionally scoped by plant/storage_location/material_group.
Reuses the same engine rules the Status Agent uses; this is a read-only
summary view over them, not a new rule of its own."""

from app.engine.aging import compute_aging
from app.engine.loader import (
    load_component_issuance,
    load_inventory_aging,
    load_order_master,
    load_order_status,
)
from app.engine.machine_head import compute_machine_head_candidates
from app.engine.material_status import compute_material_status
from app.engine.order_status import excluded_orders

# Plant and Material Group only exist on the stock/aging side (Z_AGEDINV) -
# 14C (components) and PP-134 (orders) don't carry them, so scoping is
# applied by filtering the stock rows before they're joined, not by adding
# filter params to the engine functions themselves.
def _filter_stock(
    stock: list[dict],
    *,
    plant: str | None = None,
    storage_location: str | None = None,
    material_group: str | None = None,
) -> list[dict]:
    if plant:
        stock = [r for r in stock if r.get("Plant") == plant]
    if storage_location:
        stock = [r for r in stock if r.get("Storage Location") == storage_location]
    if material_group:
        stock = [r for r in stock if r.get("Material Group") == material_group]
    return stock


def get_scope_options() -> dict:
    """Real distinct values for the sidebar's scope filter - not placeholders."""
    stock = load_inventory_aging()
    return {
        "plants": sorted({r["Plant"] for r in stock if r.get("Plant")}),
        "storage_locations": sorted({r["Storage Location"] for r in stock if r.get("Storage Location")}),
        "material_groups": sorted({r["Material Group"] for r in stock if r.get("Material Group")}),
    }


def get_summary(
    *,
    plant: str | None = None,
    storage_location: str | None = None,
    material_group: str | None = None,
) -> dict:
    """Counts behind every badge in the sidebar. Machine-head and exception-
    order counts are NOT scoped by plant/storage/material group - COOIS and
    PP-134 don't carry those fields, so scoping them would be fabricated."""
    stock = _filter_stock(
        load_inventory_aging(), plant=plant, storage_location=storage_location, material_group=material_group
    )
    components = load_component_issuance()
    order_status = load_order_status()
    order_master = load_order_master()

    status_rows = compute_material_status(components, stock, order_status)
    non_controlled = sum(1 for r in status_rows if r["material_status"] == "non_controlled")
    over_control = sum(1 for r in status_rows if r["material_status"] == "over_control")

    aging_rows = compute_aging(stock)
    aged = sum(1 for r in aging_rows if r["is_aged"])
    missing_data = sum(1 for r in aging_rows if r["aging_days_unknown"])

    blocked_stock = sum(1 for r in stock if (r.get("Blocked") or 0) > 0)

    machine_head = compute_machine_head_candidates(order_master, components, order_status)
    exception_orders = len(excluded_orders(order_status))

    return {
        "total_material": len(stock),
        "non_controlled": non_controlled,
        "over_control": over_control,
        "aging": aged,
        "machine_head": len(machine_head),
        "blocked_stock": blocked_stock,
        "missing_data": missing_data,
        "exception_orders": exception_orders,
    }
