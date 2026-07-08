"""Registry of the S/4 OData APIs these capabilities call.

Confirmed against SAP's public API docs (api.sap.com / help.sap.com) where
possible, but NOT against this system's own $metadata - if a $filter
comes back with a 400, check the exact property name on the live system
first (`GET {path}/$metadata`) before assuming the client code is wrong.

path            - the OData resource path (relative to the S/4 base URL)
key_field       - the entity's key property used to filter by identifier
has_plant_field - whether "Plant" is a valid $filter field on this entity
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class S4Api:
    path: str
    key_field: str
    has_plant_field: bool = True


# MM03 - Material/Product master data. Key: Product (S/4's OData APIs expose
# the material master as "Product", not "Material").
PRODUCT_MASTER = S4Api(
    path="/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product",
    key_field="Product",
    has_plant_field=False,
)

# MMBE - Stock overview. Key: Material. CONFIRMED (live 400 error, 2026-07-07):
# "Property Plant not found in type A_MaterialStockType" - Plant is NOT a
# top-level filterable field on A_MaterialStock. Per-plant/quantity data
# lives on the related A_MatlStkInAcctMod entity - query.py expands it via
# "to_MatlStkInAcctMod" (a common SAP naming convention for this
# association, unverified against this system's own $metadata) and filters
# by plant client-side over the expanded rows.
MATERIAL_STOCK = S4Api(
    path="/sap/opu/odata/sap/API_MATERIAL_STOCK_SRV/A_MaterialStock",
    key_field="Material",
    has_plant_field=False,
)

# COOIS - Production order info system. Key: ProductionOrder, filterable by
# Material and Plant. UNRESOLVED (live 400 error, 2026-07-08): "Property
# ProductionOrder not found in type A_ProductionOrder_2Type" when filtering
# by ProductionOrder eq '...' - either the real field name differs, or this
# entity requires direct key access (A_ProductionOrder_2('...')) rather than
# $filter on the key. Needs $metadata to confirm; Material/Plant filters are
# unverified too until this is resolved.
PRODUCTION_ORDER = S4Api(
    path="/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV/A_ProductionOrder_2",
    key_field="ProductionOrder",
)

# MMBE (serialized stock) - stock broken down by individual serial number /
# equipment. Composite key: Material + SerialNumber. Confirmed live (sample
# response, 2026-07-08) that Material/Plant/SerialNumber appear as plain
# top-level properties (not nested like A_MaterialStock), so filtering by
# them directly should work - unlike A_MaterialStock, no $expand needed here.
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
