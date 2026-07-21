"""Order exclusion filter (Technical Spec §5.1 - COOIS User Status).

Orders flagged NMVT are excluded from every downstream rule before it runs -
this is applied inside material_status.compute_material_status(), not
exposed as a tool of its own.
"""

EXCLUDED_USER_STATUS = "NMVT"


def excluded_orders(order_status_rows: list[dict]) -> set:
    """Order numbers whose COOIS User Status marks them for exclusion."""
    return {row["Order"] for row in order_status_rows if row.get("User Status") == EXCLUDED_USER_STATUS}
