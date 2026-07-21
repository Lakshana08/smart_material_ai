"""Machine-Head Material (机头料) candidate-surfacing (Technical Spec §6.4).

NOT a deterministic rule - this is a heuristic that surfaces candidates for
human review only. Every returned row carries needs_review=True
unconditionally; nothing here ever resolves a row to a confirmed status.
"""

from datetime import datetime, timezone

DEFAULT_ORDER_TYPE = "ZNPC"


def compute_machine_head_candidates(
    work_orders: list[dict], components: list[dict], *, order_type: str = DEFAULT_ORDER_TYPE
) -> list[dict]:
    matching_orders = {row.get("Order") for row in work_orders if row.get("Type") == order_type}

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
