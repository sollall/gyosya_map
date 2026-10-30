"""全件名簿（Excel）のパーサー。"""

from __future__ import annotations

from pathlib import Path

from .columns import extract, find_header
from .config import ParserConfig


def parse_excel(path: str | Path, config: ParserConfig | None = None) -> list[dict]:
    import openpyxl

    config = config or ParserConfig()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = [wb[s] for s in config.sheets] if config.sheets else wb.worksheets
    records: list[dict] = []
    for ws in sheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        found = find_header(rows, config)
        if not found:
            continue
        start, mapping = found
        for row in rows[start + 1:]:
            rec = extract(row, mapping, config)
            if rec:
                records.append(rec)
    wb.close()
    return records
