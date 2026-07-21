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
  (ProductDescription). Confirmed live (2026-07-13) that most products only
  carry descriptions for a handful of languages, not every language SAP
  ships - PATCHing a Product/Language combo that was never maintained 404s
  since PATCH only updates existing entities, so on a 404 this falls back
  to POST A_ProductDescription to create that language variant instead.

Both confirmed via live GETs, not guessed. NOT yet confirmed: an actual
PATCH against an *existing* description has never been executed against
this system - if the Gateway turns out to require If-Match despite no
ETag being exposed, add headers={"If-Match": "*"} to the mutate call.

- "create_material" -> POST A_Product, then (if a description was given) a
  second POST A_ProductDescription. Two separate calls, not a deep insert:
  confirmed live (2026-07-09) that this Gateway rejects a to_Description
  deep insert on the A_Product POST with "API_PRD_MSG/003 - Cannot process
  multiple products in a single change set request." Product/ProductType/
  IndustrySector/BaseUnit are the fields SAP's own API_PRODUCT_SRV docs mark
  mandatory for creation. Only external material number assignment is
  supported (material_number is required) - internal number ranges
  (omitting Product) are not handled here.

- "update_mrp_area" -> PATCH A_ProductPlantMRPArea(Product='<material>',
  Plant='<plant>',MRPArea='<mrp_area>') - MRP planning fields (reorder
  point, safety stock, MRP type/controller, lot sizing) live on this entity,
  keyed by Product+Plant+MRPArea per $metadata. Confirmed live (2026-07-16)
  that a PATCH with a valid X-CSRF-Token succeeds (SafetyStockQuantity
  round-tripped 0 -> 5, verified via a follow-up GET) and needs no If-Match -
  same as update_status/update_description above. An earlier round of
  testing without a valid CSRF token on this same entity returned a
  misleading Gateway error ("The specified HTTP method is not allowed for
  the resource identified by the Data Service Request URI"), which looks
  like a missing-operation error but was actually just the CSRF rejection -
  not something to read as "this entity is read-only" if it recurs. Also
  confirmed live (2026-07-16): this entity's decimal fields (e.g.
  SafetyStockQuantity) must be sent as JSON strings, not numbers - a raw
  number 400s with CX_SXML_PARSE_ERROR ("Failed to read property ... at
  offset ..."), so numeric payload values are stringified before the PATCH.

- "update_supply_planning" -> PATCH A_ProductSupplyPlanning(Product='<material>',
  Plant='<plant>') - plant-level MRP1/MRP2 supply planning fields (same
  reorder point/safety stock/lot sizing/MRP type-controller fields as
  update_mrp_area, per $metadata), keyed by Product+Plant only (no MRP
  area). Confirmed against SAP's own API Business Hub docs for
  API_PRODUCT_SRV (2026-07-16): PATCH is explicitly documented here
  ("Updates supply planning data of a product master record", 204 on
  success), and the documented writable field names match
  _PLANNING_FIELD_MAP - unlike A_ProductPlantMRPArea, which looked writable
  on paper too but needed an actual live PATCH to prove it (missing CSRF
  token gave a misleading "method not allowed" error there). Still NOT yet
  live-tested against this specific system - documentation-level
  confirmation isn't the same as a live PATCH succeeding here, and this
  system's version could differ from what's shown on API Business Hub.
"""

from app.services.s4_client import S4ClientError, get_s4_client

_PRODUCT_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product"
_PRODUCT_DESCRIPTION_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_ProductDescription"
_PRODUCT_PLANT_MRP_AREA_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_ProductPlantMRPArea"
_PRODUCT_SUPPLY_PLANNING_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_ProductSupplyPlanning"

_DEFAULT_LANGUAGE = "EN"

# Friendly payload key -> OData field name, for the planning parameters
# callers actually adjust day-to-day. Shared by update_mrp_area and
# update_supply_planning below - A_ProductPlantMRPArea and
# A_ProductSupplyPlanning use identical field names for these (confirmed via
# $metadata), they just differ in key shape (+ MRPArea vs not). Both entities
# have other fields too (see core_capabilities/query.py for the full set
# fetched on read) but only these are exposed for write here.
_PLANNING_FIELD_MAP = {
    "mrp_type": "MRPType",
    "mrp_controller": "MRPResponsible",
    "mrp_group": "MRPGroup",
    "reorder_point": "ReorderThresholdQuantity",
    "safety_stock": "SafetyStockQuantity",
    "minimum_lot_size": "MinimumLotSizeQuantity",
    "maximum_lot_size": "MaximumLotSizeQuantity",
    "maximum_stock": "MaximumStockQuantity",
    "lot_sizing_procedure": "LotSizingProcedure",
    "planning_time_fence": "PlanningTimeFence",
}


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
        try:
            client.patch(
                f"{_PRODUCT_DESCRIPTION_PATH}(Product='{material_number}',Language='{language}')",
                json_body={"ProductDescription": description},
            )
        except S4ClientError as exc:
            if exc.status_code != 404:
                raise
            # No description exists yet for this Product/Language combo -
            # PATCH only updates existing entities, so create it instead.
            client.post(
                _PRODUCT_DESCRIPTION_PATH,
                json_body={"Product": material_number, "Language": language, "ProductDescription": description},
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

    if action == "update_mrp_area":
        plant = payload.get("plant")
        mrp_area = payload.get("mrp_area")
        if not plant or not mrp_area:
            raise ValueError("update_mrp_area requires payload.plant and payload.mrp_area")

        # This Gateway's OData v2 JSON serializes Edm.Decimal fields as
        # strings (confirmed live: every GET returns e.g.
        # "SafetyStockQuantity": "0"), and rejects a bare JSON number with
        # CX_SXML_PARSE_ERROR ("Failed to read property ... at offset ...") -
        # so numeric payload values must be stringified before sending.
        fields = {
            sap_field: str(payload[key]) if isinstance(payload[key], (int, float)) else payload[key]
            for key, sap_field in _PLANNING_FIELD_MAP.items()
            if payload.get(key) is not None
        }
        if not fields:
            raise ValueError(
                "update_mrp_area requires at least one field to update, one of: "
                + ", ".join(sorted(_PLANNING_FIELD_MAP))
            )

        client = get_s4_client()
        client.patch(
            f"{_PRODUCT_PLANT_MRP_AREA_PATH}(Product='{material_number}',Plant='{plant}',MRPArea='{mrp_area}')",
            json_body=fields,
        )
        return {
            "status": "ok",
            "material_number": material_number,
            "action": action,
            "plant": plant,
            "mrp_area": mrp_area,
            "updated_fields": fields,
        }

    if action == "update_supply_planning":
        plant = payload.get("plant")
        if not plant:
            raise ValueError("update_supply_planning requires payload.plant")

        fields = {
            sap_field: str(payload[key]) if isinstance(payload[key], (int, float)) else payload[key]
            for key, sap_field in _PLANNING_FIELD_MAP.items()
            if payload.get(key) is not None
        }
        if not fields:
            raise ValueError(
                "update_supply_planning requires at least one field to update, one of: "
                + ", ".join(sorted(_PLANNING_FIELD_MAP))
            )

        client = get_s4_client()
        client.patch(
            f"{_PRODUCT_SUPPLY_PLANNING_PATH}(Product='{material_number}',Plant='{plant}')",
            json_body=fields,
        )
        return {
            "status": "ok",
            "material_number": material_number,
            "action": action,
            "plant": plant,
            "updated_fields": fields,
        }

    raise ValueError(
        f"Unknown action '{action}', expected 'update_status', 'update_description', "
        "'create_material', 'update_mrp_area', or 'update_supply_planning'"
    )
