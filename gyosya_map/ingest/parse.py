"""ファイルと制度の読み込み設定から、使うパーサーを選ぶ。"""

from __future__ import annotations

from pathlib import Path

from .config import ParserConfig
from .csvfile import parse_csv
from .excel import parse_excel
from .pdf import parse_notice_pdf, parse_pdf_list


def detect_format(path: Path, config: ParserConfig, notice: bool) -> str:
    if config.format:
        return config.format
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        return "excel"
    if ext == ".csv":
        return "csv"
    if ext == ".pdf":
        return "notice_pdf" if notice else "pdf"
    raise ValueError(f"未対応の形式です: {path}")


def parse_file(path: str | Path, config: ParserConfig | None = None, *, notice: bool = False) -> list[dict]:
    path = Path(path)
    config = config or ParserConfig()
    fmt = detect_format(path, config, notice)
    return {"excel": parse_excel, "csv": parse_csv, "pdf": parse_pdf_list,
            "notice_pdf": parse_notice_pdf}[fmt](path, config)
