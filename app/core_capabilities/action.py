"""Action capability: update/create material master fields in S/4HANA
(MM01/MM02). update_status PATCHes CrossPlantStatus on A_Product directly
(no ETag needed). update_description PATCHes the related A_ProductDescription
entity (Product+Language key); on 404 (never maintained for that language)
it falls back to POST to create that variant. create_material POSTs
A_Product then, separately, A_ProductDescription - deep insert is rejected
live by this Gateway ("cannot process multiple products in a single change
set"). Only external material numbers (material_number required) are
supported, not internal number ranges."""

from app.services.odata_utils import odata_literal
from app.services.s4_client import S4ClientError, get_s4_client

_PRODUCT_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product"
_PRODUCT_DESCRIPTION_PATH = "/sap/opu/odata/sap/API_PRODUCT_SRV/A_ProductDescription"

_DEFAULT_LANGUAGE = "EN"


def perform_material_action(material_number: str, action: str, payload: dict | None = None) -> dict:
    """Update material master fields, or delete a description. action is one of
    "update_status", "update_description", "create_material", or
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

    if action == "delete_description":
        language = payload.get("language", _DEFAULT_LANGUAGE)
        client = get_s4_client()
        client.delete(
            f"{_PRODUCT_DESCRIPTION_PATH}(Product='{odata_literal(material_number)}',"
            f"Language='{odata_literal(language)}')"
        )
        return {"status": "ok", "material_number": material_number, "action": action, "language": language}

    raise ValueError(
        "Unknown action '{}', expected 'update_status', 'update_description', 'create_material', "
        "or 'delete_description'".format(action)
    )