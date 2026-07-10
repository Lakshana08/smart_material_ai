"""Action capability: update material master fields in S/4HANA (T-code MM02),
and create new material masters (T-code MM01).

Two real, confirmed targets (checked live against API_PRODUCT_SRV, 2026-07-08):

- "update_status" -> PATCH A_Product('<material>') - CrossPlantStatus lives
  directly on A_Product, and this entity returns no ETag (neither an HTTP
  ETag header nor a __metadata.etag), so no If-Match/concurrency handling
  is needed for it.

- "update_description" -> PATCH A_ProductDescription(Product='<material>',
  Language='<lang>') - descriptions do NOT live on A_Product itself (it
  only has a to_Description navigation property); this related entity has
  its own composite key (Product + Language) and its own field
  (ProductDescription).

Both confirmed via live GETs, not guessed. NOT yet confirmed: an actual
PATCH has never been executed against this system - if the Gateway turns
out to require If-Match despite no ETag being exposed, add
headers={"If-Match": "*"} to the mutate call.

- "create_material" -> POST A_Product, then (if a description was given) a
  second POST A_ProductDescription. Two separate calls, not a deep insert:
  confirmed live (2026-07-09) that this Gateway rejects a to_Description
  deep insert on the A_Product POST with "API_PRD_MSG/003 - Cannot process
  multiple products in a single change set request." Product/ProductType/
  IndustrySector/BaseUnit are the fields SAP's own API_PRODUCT_SRV docs mark
  mandatory for creation. Only external material number assignment is
  supported (material_number is required) - internal number ranges
  (omitting Product) are not handled here.
"""

from app.services.s4_client import get_s4_client

_PRODUCT_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product"
_PRODUCT_DESCRIPTION_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_ProductDescription"

_DEFAULT_LANGUAGE = "EN"


def perform_material_action(material_number: str, action: str, payload: dict | None = None) -> dict:
    """Update material master fields. action is one of "update_status" or
    "update_description"; payload carries the new value(s).
    """
    payload = payload or {}

    if action == "update_status":
        status = payload.get("status") or payload.get("cross_plant_status")
        if not status:
            raise ValueError("update_status requires payload.status (the new CrossPlantStatus code)")
        client = get_s4_client()
        client.patch(f"{_PRODUCT_PATH}('{material_number}')", json_body={"CrossPlantStatus": status})
        return {"status": "ok", "material_number": material_number, "action": action, "new_status": status}

    if action == "update_description":
        description = payload.get("description")
        if not description:
            raise ValueError("update_description requires payload.description (the new text)")
        language = payload.get("language", _DEFAULT_LANGUAGE)
        client = get_s4_client()
        client.patch(
            f"{_PRODUCT_DESCRIPTION_PATH}(Product='{material_number}',Language='{language}')",
            json_body={"ProductDescription": description},
        )
        return {
            "status": "ok",
            "material_number": material_number,
            "action": action,
            "language": language,
            "new_description": description,
        }

    if action == "create_material":
        product_type = payload.get("product_type")
        industry_sector = payload.get("industry_sector")
        base_unit = payload.get("base_unit")
        if not product_type or not industry_sector or not base_unit:
            raise ValueError(
                "create_material requires payload.product_type (e.g. 'FERT', 'HAWA', 'ROH'), "
                "payload.industry_sector (e.g. 'M', 'C', 'P', 'R'), and payload.base_unit (e.g. 'EA', 'KG')"
            )

        body: dict = {
            "Product": material_number,
            "ProductType": product_type,
            "IndustrySector": industry_sector,
            "BaseUnit": base_unit,
        }
        material_group = payload.get("material_group")
        if material_group:
            body["ProductGroup"] = material_group

        client = get_s4_client()
        client.post(_PRODUCT_PATH, json_body=body)

        description = payload.get("description")
        language = payload.get("language", _DEFAULT_LANGUAGE)
        if description:
            client.post(
                _PRODUCT_DESCRIPTION_PATH,
                json_body={"Product": material_number, "Language": language, "ProductDescription": description},
            )

        return {
            "status": "ok",
            "material_number": material_number,
            "action": action,
            "product_type": product_type,
            "industry_sector": industry_sector,
            "base_unit": base_unit,
        }

    raise ValueError(
        f"Unknown action '{action}', expected 'update_status', 'update_description', or 'create_material'"
    )