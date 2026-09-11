"""Action capability: update material master fields in S/4HANA (T-code MM02),
and create new material masters (T-code MM01), via SAP's Product Master API
(API_PRODUCT_SRV - entities A_Product / A_ProductDescription). Also creates
and updates production orders (T-code CO01/CO02) via the Production Order
API (API_PRODUCTION_ORDER_2_SRV - entity A_ProductionOrder_2) - see
perform_production_order_action below.

Two real, confirmed targets (checked live against API_PRODUCT_SRV, 2026-07-08):

- "update_status" -> PATCH A_Product('<material>') - CrossPlantStatus lives
  directly on A_Product, and this entity returns no ETag (neither an HTTP
  ETag header nor a __metadata.etag), so no If-Match/concurrency handling
  is needed for it.

- "update_weight" -> PATCH A_Product('<material>') - GrossWeight/WeightUnit
  are also top-level A_Product fields, same no-ETag behavior as CrossPlantStatus.

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

- "create_material" -> single POST A_Product with to_Description as a deep
  insert. Confirmed live (2026-09-11) that a create with no description at
  all is rejected outright with "API_PRD_MSG/009 - Create operation not
  allowed on entity." - this system requires at least one description to be
  present in the same create request, so payload.description is mandatory
  here (an earlier assumption that this Gateway rejects the to_Description
  deep insert with API_PRD_MSG/003 no longer holds on this system/version -
  the deep insert succeeds once a description is included). Product/
  ProductType/IndustrySector/BaseUnit are the fields SAP's own API_PRODUCT_SRV
  docs mark mandatory for creation. Only external material number assignment
  is supported (material_number is required) - internal number ranges
  (omitting Product) are not handled here.

- "delete_description" -> DELETE A_ProductDescription(Product='<material>',
  Language='<lang>'). Confirmed live (2026-09-11): the same "a product needs
  at least one description" rule from create_material applies here too -
  deleting a product's only remaining description is rejected with
  "PMD_MSG/032 - Description of product '<material>' not maintained" even
  though the entity demonstrably still exists (GET on the exact same key
  succeeds right up until the DELETE). The error text is misleading - it
  isn't that nothing is maintained, it's that deleting it would leave zero
  descriptions. Add a second language's description first if you need to
  remove the only existing one.
"""

from app.services.odata_utils import odata_literal
from app.services.s4_client import S4ClientError, get_s4_client

_PRODUCT_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product"
_PRODUCT_DESCRIPTION_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_ProductDescription"
_PRODUCTION_ORDER_PATH = "/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV/A_ProductionOrder_2"

_DEFAULT_LANGUAGE = "EN"


def perform_material_action(material_number: str, action: str, payload: dict | None = None) -> dict:
    """Update material master fields, or delete a description. action is one of
    "update_status", "update_weight", "update_description", "create_material", or
    "delete_description"; payload carries the new value(s).
    """
    payload = payload or {}

    if action == "update_status":
        status = payload.get("status") or payload.get("cross_plant_status")
        if not status:
            raise ValueError("update_status requires payload.status (the new CrossPlantStatus code)")
        client = get_s4_client()
        client.patch(f"{_PRODUCT_PATH}('{odata_literal(material_number)}')", json_body={"CrossPlantStatus": status})
        return {"status": "ok", "material_number": material_number, "action": action, "new_status": status}

    if action == "update_weight":
        gross_weight = payload.get("gross_weight")
        weight_unit = payload.get("weight_unit")
        if not gross_weight or not weight_unit:
            raise ValueError("update_weight requires payload.gross_weight and payload.weight_unit")
        client = get_s4_client()
        client.patch(
            f"{_PRODUCT_PATH}('{odata_literal(material_number)}')",
            json_body={"GrossWeight": gross_weight, "WeightUnit": weight_unit},
        )
        return {
            "status": "ok",
            "material_number": material_number,
            "action": action,
            "gross_weight": gross_weight,
            "weight_unit": weight_unit,
        }

    if action == "update_description":
        description = payload.get("description")
        if not description:
            raise ValueError("update_description requires payload.description (the new text)")
        language = payload.get("language", _DEFAULT_LANGUAGE)
        client = get_s4_client()
        try:
            client.patch(
                f"{_PRODUCT_DESCRIPTION_PATH}(Product='{odata_literal(material_number)}',"
                f"Language='{odata_literal(language)}')",
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
        description = payload.get("description")
        if not product_type or not industry_sector or not base_unit or not description:
            raise ValueError(
                "create_material requires payload.product_type (e.g. 'FERT', 'HAWA', 'ROH'), "
                "payload.industry_sector (e.g. 'M', 'C', 'P', 'R'), payload.base_unit (e.g. 'EA', 'KG'), "
                "and payload.description - this system rejects a create with no description at all"
            )

        language = payload.get("language", _DEFAULT_LANGUAGE)
        body: dict = {
            "Product": material_number,
            "ProductType": product_type,
            "IndustrySector": industry_sector,
            "BaseUnit": base_unit,
            "to_Description": {
                "results": [{"Product": material_number, "Language": language, "ProductDescription": description}]
            },
        }
        material_group = payload.get("material_group")
        if material_group:
            body["ProductGroup"] = material_group

        client = get_s4_client()
        client.post(_PRODUCT_PATH, json_body=body)

        return {
            "status": "ok",
            "material_number": material_number,
            "action": action,
            "product_type": product_type,
            "industry_sector": industry_sector,
            "base_unit": base_unit,
        }

    if action == "delete_description":
        language = payload.get("language", _DEFAULT_LANGUAGE)
        client = get_s4_client()
        client.delete(
            f"{_PRODUCT_DESCRIPTION_PATH}(Product='{odata_literal(material_number)}',"
            f"Language='{odata_literal(language)}')"
        )
        return {"status": "ok", "material_number": material_number, "action": action, "language": language}

    raise ValueError(
        "Unknown action '{}', expected 'update_status', 'update_weight', 'update_description', "
        "'create_material', or 'delete_description'".format(action)
    )


def perform_production_order_action(order_number: str | None, action: str, payload: dict | None = None) -> dict:
    """Create or update a production order in S/4HANA (T-code CO01/CO02) via the
    Production Order API (API_PRODUCTION_ORDER_2_SRV, entity A_ProductionOrder_2).
    action is one of "create_production_order" or "update_production_order";
    order_number (the ManufacturingOrder number) is required for update, not
    for create (SAP assigns the number on creation). payload carries the
    field values.

    Unlike A_Product, this entity has real optimistic concurrency control
    (an ETag) - a PATCH without a matching If-Match header is rejected, so
    update_production_order fetches the current ETag live before patching.

    Confirmed live (2026-09-11): payload.production_version is NOT optional
    despite an earlier assumption here - a create with no ProductionVersion
    at all is rejected outright with "C2/144 - Specify production version".
    """
    payload = payload or {}
    client = get_s4_client()

    if action == "create_production_order":
        material = payload.get("material")
        production_plant = payload.get("production_plant")
        order_type = payload.get("manufacturing_order_type")
        total_quantity = payload.get("total_quantity")
        planned_end_date = payload.get("mfg_order_planned_end_date")
        production_version = payload.get("production_version")
        if (
            not material
            or not production_plant
            or not order_type
            or not total_quantity
            or not planned_end_date
            or not production_version
        ):
            raise ValueError(
                "create_production_order requires payload.material, payload.production_plant, "
                "payload.manufacturing_order_type, payload.total_quantity, "
                "payload.mfg_order_planned_end_date (ISO 8601, e.g. '2026-12-01T00:00:00'), and "
                "payload.production_version - this system rejects a create with no production "
                "version at all ('C2/144 - Specify production version')"
            )

        body = {
            "Material": material,
            "ProductionPlant": production_plant,
            "ManufacturingOrderType": order_type,
            "TotalQuantity": total_quantity,
            "MfgOrderPlannedEndDate": planned_end_date,
            "ProductionVersion": production_version,
        }

        result = client.post(_PRODUCTION_ORDER_PATH, json_body=body)
        created_order = result.get("d", {}).get("ManufacturingOrder")
        return {
            "status": "ok",
            "action": action,
            "material": material,
            "production_plant": production_plant,
            "manufacturing_order_type": order_type,
            "total_quantity": total_quantity,
            "manufacturing_order": created_order,
        }

    if action == "update_production_order":
        if not order_number:
            raise ValueError("update_production_order requires order_number (the ManufacturingOrder number)")

        total_quantity = payload.get("total_quantity")
        planned_end_date = payload.get("mfg_order_planned_end_date")
        if not total_quantity and not planned_end_date:
            raise ValueError(
                "update_production_order requires at least one of payload.total_quantity or "
                "payload.mfg_order_planned_end_date"
            )

        update_body = {}
        if total_quantity:
            update_body["TotalQuantity"] = total_quantity
        if planned_end_date:
            update_body["MfgOrderPlannedEndDate"] = planned_end_date

        path = f"{_PRODUCTION_ORDER_PATH}('{odata_literal(order_number)}')"
        etag = client.get_etag(path)
        client.patch(path, json_body=update_body, extra_headers={"If-Match": etag} if etag else None)
        return {"status": "ok", "action": action, "order_number": order_number, **update_body}

    raise ValueError(
        "Unknown action '{}', expected 'create_production_order' or 'update_production_order'".format(action)
    )