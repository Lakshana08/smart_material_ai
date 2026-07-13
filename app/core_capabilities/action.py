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

Two more, per SAP Help's "Create/Read/Update/Delete Product Master Data"
page - documented, NOT yet confirmed live against this system:

- "create_supply_planning" -> POST A_ProductSupplyPlanning. Plant-level MRP
  data (MRP type/responsible/group, procurement type, lot sizing, safety
  stock, etc.) for a material that must already exist (create_material
  first). Separate entity/call, same reasoning as the description split
  above. Only the commonly-set fields are named parameters; anything else
  SAP's docs list for this entity can be passed via payload.extra_fields.

- "delete_description" -> DELETE A_ProductDescription(Product='<material>',
  Language='<lang>'). This is the only delete SAP's docs demonstrate for
  this API - there's no documented hard-delete for A_Product itself (SAP
  generally treats material deletion as a status flag, not a real DELETE).
"""

from app.services.s4_client import get_s4_client

_PRODUCT_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product"
_PRODUCT_DESCRIPTION_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_ProductDescription"
_PRODUCT_SUPPLY_PLANNING_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_ProductSupplyPlanning"

_DEFAULT_LANGUAGE = "EN"


def perform_material_action(material_number: str, action: str, payload: dict | None = None) -> dict:
    """Create, update, or delete material master fields. action is one of
    "update_status", "update_description", "create_material",
    "create_supply_planning", or "delete_description"; payload carries the
    new value(s).
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

    if action == "create_supply_planning":
        plant = payload.get("plant")
        if not plant:
            raise ValueError("create_supply_planning requires payload.plant")

        body: dict = {"Product": material_number, "Plant": plant}
        field_map = {
            "mrp_type": "MRPType",
            "mrp_responsible": "MRPResponsible",
            "mrp_group": "MRPGroup",
            "procurement_type": "ProcurementType",
            "lot_sizing_procedure": "LotSizingProcedure",
            "availability_check_type": "AvailabilityCheckType",
            "abc_indicator": "ABCIndicator",
            "safety_stock_quantity": "SafetyStockQuantity",
            "planned_delivery_duration_in_days": "PlannedDeliveryDurationInDays",
        }
        for payload_key, odata_field in field_map.items():
            value = payload.get(payload_key)
            if value is not None:
                body[odata_field] = value
        body.update(payload.get("extra_fields") or {})

        client = get_s4_client()
        client.post(_PRODUCT_SUPPLY_PLANNING_PATH, json_body=body)
        return {"status": "ok", "material_number": material_number, "action": action, "plant": plant}

    if action == "delete_description":
        language = payload.get("language", _DEFAULT_LANGUAGE)
        client = get_s4_client()
        client.delete(f"{_PRODUCT_DESCRIPTION_PATH}(Product='{material_number}',Language='{language}')")
        return {"status": "ok", "material_number": material_number, "action": action, "language": language}

    raise ValueError(
        f"Unknown action '{action}', expected 'update_status', 'update_description', 'create_material', "
        "'create_supply_planning', or 'delete_description'"
    )
