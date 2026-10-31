"""PDF・Excel から読み取れる文字と表をそのまま見せる。新しい自治体の読み込み設定を作るときに使う。"""

from __future__ import annotations

from pathlib import Path


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return s.replace("\n", "⏎")[:30]


def _table(rows, limit: int) -> list[str]:
    out = [" | ".join(_cell(c) for c in r) for r in rows[:limit]]
    if len(rows) > limit:
        out.append(f"…（全 {len(rows)} 行）")
    return out


def inspect_file(path: Path, pages: int = 2, rows: int = 12) -> str:
    if not path.exists():
        raise ValueError(f"ファイルが見つかりません: {path}")
    ext = path.suffix.lower()
    out: list[str] = []
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            out.append(f"{path}（{len(pdf.pages)} ページ）")
            for i, page in enumerate(pdf.pages[:pages], 1):
                text = page.extract_text() or ""
                lines = text.splitlines()
                out.append(f"\n=== {i} ページ目: 文字 {len(lines)} 行 ===")
                out += [f"  {l}" for l in lines[:40]]
                if len(lines) > 40:
                    out.append(f"  …（全 {len(lines)} 行）")
                if not text.strip():
                    out.append("  （文字が取れません。画像のPDF（スキャン）の可能性があります）")
                tables = page.extract_tables()
                out.append(f"--- {i} ページ目: 表 {len(tables)} 個 ---")
                for j, t in enumerate(tables, 1):
                    out.append(f"[表{j}]")
                    out += [f"  {r}" for r in _table(t, rows)]
    elif ext in (".xlsx", ".xlsm", ".xls"):
        from .excel import _xls_sheets, _xlsx_sheets
        import openpyxl
        names = (openpyxl.load_workbook(path, read_only=True).sheetnames if ext != ".xls" else
                 __import__("xlrd").open_workbook(path).sheet_names())
        sheets = (_xls_sheets if ext == ".xls" else _xlsx_sheets)(path, None)
        for name, data in zip(names, sheets):
            out.append(f"\n=== シート「{name}」: {len(data)} 行 ===")
            out += [f"  {i + 1}: {r}" for i, r in enumerate(_table(data, rows))]
    elif ext == ".csv":
        out += path.read_text("utf-8-sig", errors="replace").splitlines()[:rows]
    else:
        raise ValueError(f"未対応の形式です: {path}")
    return "\n".join(out)
