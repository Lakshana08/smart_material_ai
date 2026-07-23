"""Registry of the S/4 OData APIs these capabilities call. If a $filter
returns a 400, check the property name on the live system's own
`GET {path}/$metadata` before assuming the client code is wrong."""

from dataclasses import dataclass


@dataclass(frozen=True)
class S4Api:
    path: str
    key_field: str
    has_plant_field: bool = True


# MM03 - material master. S/4's OData API calls it "Product", not "Material".
PRODUCT_MASTER = S4Api(
    path="/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product",
    key_field="Product",
    has_plant_field=False,
)

# MMBE - stock overview. Plant is NOT filterable at the top level (confirmed
# live 400: "Property Plant not found") - per-plant data is nested under the
# expanded to_MatlStkInAcctMod association; query.py filters by plant client-side.
MATERIAL_STOCK = S4Api(
    path="/sap/opu/odata/sap/API_MATERIAL_STOCK_SRV/A_MaterialStock",
    key_field="Material",
    has_plant_field=False,
)

# COOIS - production order info. UNRESOLVED: filtering ProductionOrder eq '...'
# 400s live ("Property ProductionOrder not found") - may need direct key
# access instead of $filter; check $metadata before trusting this as-is.
PRODUCTION_ORDER = S4Api(
    path="/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV/A_ProductionOrder_2",
    key_field="ProductionOrder",
)

# MMBE serialized stock - composite key Material+SerialNumber. Unlike
# A_MaterialStock, fields are top-level (confirmed live) - no $expand needed.
MATERIAL_SERIAL_NUMBER = S4Api(
    path="/sap/opu/odata/sap/API_MATERIAL_STOCK_SRV/A_MaterialSerialNumber",
    key_field="Material",
)

BY_NAME = {
    "material_master": PRODUCT_MASTER,
    "material_stock": MATERIAL_STOCK,
    "production_order": PRODUCTION_ORDER,
    "material_serial_number": MATERIAL_SERIAL_NUMBER,
}