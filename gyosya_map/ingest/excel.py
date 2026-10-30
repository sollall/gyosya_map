"""全件名簿（Excel）のパーサー。"""

from __future__ import annotations

from pathlib import Path

from .columns import extract, find_header


def parse_excel(path: str | Path) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    records: list[dict] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        found = find_header(rows)
        if not found:
            continue
        start, mapping = found
        for row in rows[start + 1:]:
            rec = extract(row, mapping)
            if rec:
                records.append(rec)
    wb.close()
    return records
