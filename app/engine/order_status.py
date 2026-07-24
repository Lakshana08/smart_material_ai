"""Order exclusion filter (Technical Spec §5.1 - COOIS User Status). NMVT
orders are excluded in material_status.py and machine_head.py; aging.py has
no Order field in its source data (Z_AGEDINV), so this can't apply there."""

EXCLUDED_USER_STATUS = "NMVT"


def excluded_orders(order_status_rows: list[dict]) -> set:
    """Order numbers whose COOIS User Status marks them for exclusion."""
    return {row["Order"] for row in order_status_rows if row.get("User Status") == EXCLUDED_USER_STATUS}
