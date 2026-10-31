"""全件名簿（Excel）のパーサー。.xlsx は openpyxl、古い .xls は xlrd で読む。"""

from __future__ import annotations

from pathlib import Path

from .columns import extract, find_header
from .config import ParserConfig


def _xlsx_sheets(path: Path, names: list[str] | None):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in ([wb[s] for s in names] if names else wb.worksheets):
            yield [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _xls_sheets(path: Path, names: list[str] | None):
    import xlrd

    book = xlrd.open_workbook(path)
    for sh in ([book.sheet_by_name(s) for s in names] if names else book.sheets()):
        rows = []
        for i in range(sh.nrows):
            row = []
            for c in sh.row(i):
                if c.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate_as_datetime(c.value, book.datemode))
                elif c.ctype == xlrd.XL_CELL_NUMBER and float(c.value).is_integer():
                    row.append(int(c.value))      # 番号が 101.0 にならないように
                elif c.ctype == xlrd.XL_CELL_EMPTY:
                    row.append(None)
                else:
                    row.append(c.value)
            rows.append(row)
        yield rows


def parse_excel(path: str | Path, config: ParserConfig | None = None) -> list[dict]:
    path = Path(path)
    config = config or ParserConfig()
    sheets = _xls_sheets if path.suffix.lower() == ".xls" else _xlsx_sheets
    records: list[dict] = []
    for rows in sheets(path, config.sheets):
        found = find_header(rows, config)
        if not found:
            continue
        start, mapping = found
        for row in rows[start + 1:]:
            rec = extract(row, mapping, config)
            if rec:
                records.append(rec)
    return records
