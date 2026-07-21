"""Business logic behind the Status Agent's four tools - Non-Controlled
Material, Over-Control / Over-Issued Material, Aging, and Machine-Head
Material Review (Technical Spec §6).

Loads the four sample_data/ reports fresh on each call (Excel-only phase -
no live-S4 adapter yet) and hands them to app/engine/*.py for computation.
No LLM code anywhere in this file - these functions are called identically
whether the caller is the structured-JSON path or a tool bound to the LLM.
"""

from app.engine.aging import compute_aging
from app.engine.loader import (
    load_component_issuance,
    load_inventory_aging,
    load_order_master,
    load_order_status,
)
from app.engine.machine_head import compute_machine_head_candidates
from app.engine.material_status import compute_material_status


DEFAULT_RESULT_LIMIT = 50


def _cap(results: list[dict], limit: int) -> dict:
    """Broad/unscoped queries can legitimately match thousands of rows (e.g.
    1,678 non-controlled records in the sample data) - dumping all of them
    into a tool result would be a real cost/latency problem for the LLM path,
    not just noise. count is always the TRUE total; results is capped so the
    caller (human or LLM) always knows whether it's seeing everything."""
    return {"count": len(results), "truncated": len(results) > limit, "results": results[:limit]}


def query_material_status(
    *,
    work_order: str | None = None,
    material: str | None = None,
    storage_location: str | None = None,
    status_filter: str | None = None,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> dict:
    """status_filter is one of 'non_controlled', 'over_control', 'available', or None for all.
    Safe to call with no filters at all - a broad/unscoped query is valid, not an error."""
    components = load_component_issuance()
    stock = load_inventory_aging()
    order_status = load_order_status()

    results = compute_material_status(
        components,
        stock,
        order_status,
        work_order=work_order,
        material=material,
        storage_location=storage_location,
    )
    if status_filter:
        results = [r for r in results if r["material_status"] == status_filter]

    return _cap(results, limit)


def query_aging(
    *, material: str | None = None, storage_location: str | None = None, limit: int = DEFAULT_RESULT_LIMIT
) -> dict:
    """Returns only rows where Aging Days > 14 - matching AgingCheck's stated
    purpose. Safe to call with no filters at all."""
    aging_rows = load_inventory_aging()
    results = compute_aging(aging_rows, material=material, storage_location=storage_location)
    aged_only = [r for r in results if r["is_aged"]]
    return _cap(aged_only, limit)


def query_machine_head_candidates(*, order_type: str = "ZNPC", limit: int = DEFAULT_RESULT_LIMIT) -> dict:
    work_orders = load_order_master()
    components = load_component_issuance()
    results = compute_machine_head_candidates(work_orders, components, order_type=order_type)
    return _cap(results, limit)
