"""Aging rule (Technical Spec §6.3) - fixed 14-day threshold, not
configurable per site or material category.
"""

from datetime import datetime, timezone

AGING_THRESHOLD_DAYS = 14


def compute_aging(
    aging_rows: list[dict], *, material: str | None = None, storage_location: str | None = None
) -> list[dict]:
    results = []
    for row in aging_rows:
        aging_days = row.get("Aging Days") or 0
        results.append(
            {
                "material": row.get("Material"),
                "storage_location": row.get("Storage Location"),
                "plant": row.get("Plant"),
                "aging_days": aging_days,
                "is_aged": aging_days > AGING_THRESHOLD_DAYS,
                "data_as_of": datetime.now(timezone.utc).isoformat(),
            }
        )

    if material:
        results = [r for r in results if r["material"] == material]
    if storage_location:
        results = [r for r in results if r["storage_location"] == storage_location]

    return results
