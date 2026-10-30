"""手作業で起こした名簿・告示（情報公開請求の回答など）をCSVで取り込む。

列: license_no, name, address, representative, valid_from, valid_until, sub_scope[, event]
event は new / renew / change / revoke（告示として取り込むときのみ）。日本語の見出しも可。
"""

from __future__ import annotations

import csv
from pathlib import Path

from .columns import ALIASES, extract, find_header
from .pdf import classify_event


def parse_csv(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    header = rows[0]
    if all(h.strip() in ALIASES for h in header if h.strip()):
        mapping = {h.strip(): i for i, h in enumerate(header) if h.strip()}
        start = 0
    else:
        found = find_header(rows)
        if not found:
            raise ValueError(f"見出し行（番号・名称）が見つかりません: {path}")
        start, mapping = found
    records = []
    for row in rows[start + 1:]:
        rec = extract(row, mapping)
        if rec:
            if rec.get("event") and rec["event"] not in ("new", "renew", "change", "revoke"):
                rec["event"] = classify_event(rec["event"])
            records.append(rec)
    return records
