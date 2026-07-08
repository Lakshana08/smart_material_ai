"""Action capability: perform a business action on a material in S/4HANA
(create/update/trigger a process).

TODO: the OData service path and body shape below are placeholders -
replace with the real service/entity/function-import names once the S/4
API details are provided.
"""

from app.services.s4_client import get_s4_client

_MATERIAL_ACTION_PATH = "/sap/opu/odata/sap/API_MATERIAL_SRV/MaterialAction"


def perform_material_action(material_number: str, action: str, payload: dict | None = None) -> dict:
    """Create/update a material or trigger a business process against it."""
    client = get_s4_client()
    body = {"Material": material_number, "Action": action, **(payload or {})}
    result = client.post(_MATERIAL_ACTION_PATH, json_body=body)
    return {"status": "ok", "material_number": material_number, "action": action, "result": result}
