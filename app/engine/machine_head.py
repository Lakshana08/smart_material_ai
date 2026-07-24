"""Machine-Head Material (机头料) candidate-surfacing (Technical Spec §6.4) -
a heuristic, not a rule. needs_review=True always; never a confirmed status."""

from datetime import datetime, timezone

from app.engine.order_status import excluded_orders

DEFAULT_ORDER_TYPE = "ZNPC"


def compute_machine_head_candidates(
    work_orders: list[dict], components: list[dict], order_status: list[dict], *, order_type: str = DEFAULT_ORDER_TYPE
) -> list[dict]:
    excluded = excluded_orders(order_status)
    matching_orders = {
        row.get("Order")
        for row in work_orders
        if row.get("Type") == order_type and row.get("Order") not in excluded
    }

    results = []
    for row in components:
        if row.get("Order") not in matching_orders:
            continue
        net_gi = row.get("Net GI (CO11N)") or 0
        if net_gi <= 0:
            continue  # heuristic: prior GI history is part of the candidate signal
        results.append(
            {
                "work_order": row.get("Order"),
                "component": row.get("Component"),
                "storage_location": row.get("Storage Location"),
                "net_gi": net_gi,
                "needs_review": True,
                "data_as_of": datetime.now(timezone.utc).isoformat(),
            }
        )

    return results
