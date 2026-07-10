"""Builds a downloadable report (PDF/Excel/CSV) from tabular S/4 data and
holds it in a short-lived, token-keyed store.

A2A task responses carry text/data parts, not binary attachments, so
report_agent.py calls `build()` and puts only the returned download_url in
its response message; a user clicks the link, which hits the
/reports/download/{token} route and streams the file back via `get()`.
"""

import csv
import io
import secrets
import threading
import time

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from app.core.config import get_settings

SUPPORTED_FORMATS = {"csv", "xlsx", "pdf"}

_CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


class ReportNotFoundError(Exception):
    pass


class ReportBuilder:
    def __init__(self) -> None:
        self._storage: dict[str, tuple[bytes, str, str, float]] = {}
        self._lock = threading.Lock()

    def build(self, rows: list[dict], report_format: str, base_filename: str) -> dict:
        if report_format not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported report format '{report_format}', expected one of {SUPPORTED_FORMATS}")

        content = {
            "csv": self._to_csv,
            "xlsx": self._to_xlsx,
            "pdf": self._to_pdf,
        }[report_format](rows)

        token = secrets.token_urlsafe(16)
        filename = f"{base_filename}.{report_format}"
        ttl = get_settings().report_download_ttl_seconds
        with self._lock:
            self._storage[token] = (content, filename, _CONTENT_TYPES[report_format], time.time() + ttl)

        download_url = f"{get_settings().app_public_url}/reports/download/{token}"
        return {"token": token, "filename": filename, "download_url": download_url}

    def get(self, token: str) -> tuple[bytes, str, str]:
        with self._lock:
            entry = self._storage.get(token)
            if entry is None or entry[3] < time.time():
                self._storage.pop(token, None)
                raise ReportNotFoundError(f"No report found for token '{token}' (expired or never existed)")
            return entry[0], entry[1], entry[2]

    @staticmethod
    def _to_csv(rows: list[dict]) -> bytes:
        buffer = io.StringIO()
        if rows:
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return buffer.getvalue().encode("utf-8")

    @staticmethod
    def _to_xlsx(rows: list[dict]) -> bytes:
        workbook = Workbook()
        sheet = workbook.active
        if rows:
            headers = list(rows[0].keys())
            sheet.append(headers)
            for row in rows:
                sheet.append([row.get(h, "") for h in headers])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    @staticmethod
    def _to_pdf(rows: list[dict]) -> bytes:
        # Landscape + explicit even colWidths + Paragraph-wrapped cells:
        # S/4 tables commonly have 10+ columns (e.g. material_stock has 13),
        # which overflows a portrait page with ReportLab's default
        # auto-sized Table - cells get clipped/cut off rather than wrapping.
        buffer = io.BytesIO()
        page_size = landscape(A4)
        doc = SimpleDocTemplate(buffer, pagesize=page_size, leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)

        cell_style = getSampleStyleSheet()["BodyText"]
        cell_style.fontSize = 7
        cell_style.leading = 9

        if rows:
            headers = list(rows[0].keys())
            available_width = page_size[0] - doc.leftMargin - doc.rightMargin
            col_width = available_width / len(headers)
            data = [[Paragraph(str(h), cell_style) for h in headers]]
            for row in rows:
                data.append([Paragraph(str(row.get(h, "")), cell_style) for h in headers])
            table = Table(data, colWidths=[col_width] * len(headers), repeatRows=1)
        else:
            table = Table([["No data"]])

        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a6ed1")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        doc.build([table])
        return buffer.getvalue()


_builder = ReportBuilder()


def get_report_builder() -> ReportBuilder:
    return _builder