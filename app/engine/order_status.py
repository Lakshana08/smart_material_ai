"""Order exclusion filter (Technical Spec §5.1 - COOIS User Status).

Orders flagged NMVT are excluded from every rule keyed by Order - applied
inside material_status.compute_material_status() (Non-Controlled/Over-Control)
and machine_head.compute_machine_head_candidates(). Not exposed as a tool of
its own. The Aging rule has no Order field in its source data (Z_AGEDINV is
Material/Plant/StorageLocation only), so this exclusion cannot apply there.
"""

EXCLUDED_USER_STATUS = "NMVT"


def excluded_orders(order_status_rows: list[dict]) -> set:
    """Order numbers whose COOIS User Status marks them for exclusion."""
    return {row["Order"] for row in order_status_rows if row.get("User Status") == EXCLUDED_USER_STATUS}
