"""Reads the four raw SAP report exports in sample_data/ into plain lists of
dicts, one per row, keyed by each sheet's real column headers.

No pandas - openpyxl only (already in requirements.txt), since this is the
only place in the engine that touches a file at all. Everything downstream
(app/engine/*.py rule functions) works on plain lists/dicts.
"""

import os

import openpyxl

_SAMPLE_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sample_data"
)


def _read_sheet(filename: str, sheet_name: str) -> list[dict]:
    path = os.path.join(_SAMPLE_DATA_DIR, filename)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook[sheet_name]
    rows = worksheet.iter_rows(values_only=True)
    header = list(next(rows))
    return [dict(zip(header, row)) for row in rows if any(value is not None for value in row)]


def load_order_master() -> list[dict]:
    """RAW_PP134_OrderMaster.xlsx - work order master data (PP-134)."""
    return _read_sheet("RAW_PP134_OrderMaster.xlsx", "Order Master (PP-134)")


def load_component_issuance() -> list[dict]:
    """RAW_14C_ComponentIssuance.xlsx - component issuance / theoretical vs
    actual usage (14C). Columns K and L in the source sheet are both
    literally named 'B' (a real export quirk); neither is read by any rule,
    so dict(zip(header, row)) silently keeping only the last 'B' value is
    harmless here.
    """
    return _read_sheet("RAW_14C_ComponentIssuance.xlsx", "Component Issuance (14C)")


def load_order_status() -> list[dict]:
    """RAW_COOIS_OrderStatus.xlsx - order status flags (COOIS). Two columns: Order, User Status."""
    return _read_sheet("RAW_COOIS_OrderStatus.xlsx", "Order Status (COOIS)")


def load_inventory_aging() -> list[dict]:
    """RAW_ZAGEDINV_Inventory.xlsx - stock and aging by material/storage location (Z_AGEDINV)."""
    return _read_sheet("RAW_ZAGEDINV_Inventory.xlsx", "Inventory & Aging (Z_AGEDINV)")
