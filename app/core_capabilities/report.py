"""Report capability: build a downloadable report from any of the three
S/4 query sources (material master / material stock / production order).
"""

from app.core_capabilities._s4_apis import BY_NAME
from app.core_capabilities.query import flatten_stock_rows
from app.services.odata_utils import extract_rows, odata_literal
from app.services.report_builder import get_report_builder
from app.services.s4_client import get_s4_client


def generate_material_report(
    report_type: str = "material_stock",
    identifiers: list[str] | None = None,
    plant: str | None = None,
    report_format: str = "pdf",
) -> dict:
    if report_type not in BY_NAME:
        raise ValueError(f"Unknown report_type '{report_type}', expected one of {sorted(BY_NAME)}")

    rows = _fetch_report_rows(report_type, identifiers, plant)

    result = get_report_builder().build(rows, report_format, base_filename=f"{report_type}_report")
    return {
        "download_url": result["download_url"],
        "filename": result["filename"],
        "row_count": len(rows),
        "report_type": report_type,
    }


def _fetch_report_rows(report_type: str, identifiers: list[str] | None, plant: str | None) -> list[dict]:
    api = BY_NAME[report_type]

    if report_type == "material_stock":
        # Same treatment as query_material_stock(): Plant isn't a top-level
        # filter field on A_MaterialStock, so expand the nested association
        # and flatten into real line-item rows instead.
        params = {"$expand": "to_MatlStkInAcctMod"}
        if identifiers:
            params["$filter"] = " or ".join(f"Material eq '{odata_literal(i)}'" for i in identifiers)
        data = get_s4_client().get(api.path, params=params)
        return flatten_stock_rows(extract_rows(data), plant)

    filters = []
    if identifiers:
        filters.append(
            " or ".join(f"{api.key_field} eq '{odata_literal(identifier)}'" for identifier in identifiers)
        )
    if plant and api.has_plant_field:
        filters.append(f"Plant eq '{odata_literal(plant)}'")
    params = {"$filter": " and ".join(f"({f})" for f in filters)} if filters else None
    data = get_s4_client().get(api.path, params=params)
    return extract_rows(data)