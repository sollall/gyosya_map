"""名簿の表の見出しを licensee の項目に対応付ける。"""

from __future__ import annotations

import re

from ..normalize import nfkc

# 見出しに含まれる語 → 項目。長い語から順に当てる。
ALIASES: dict[str, tuple[str, ...]] = {
    "license_no": ("登録番号", "許可番号", "指定番号", "番号"),
    "name": ("氏名又は名称", "商号又は名称", "商号", "名称", "事業者名", "業者名", "氏名"),
    "address": ("主たる営業所の所在地", "営業所所在地", "所在地", "住所"),
    "representative": ("代表者氏名", "代表者名", "代表者"),
    "valid_from": ("登録年月日", "許可年月日", "指定年月日", "更新年月日", "有効期間開始", "登録日", "指定日"),
    "valid_until": ("有効期間満了日", "有効期限", "満了日", "有効期間"),
    "sub_scope": ("許可の範囲", "業務の範囲", "取扱品目", "範囲"),
    "event": ("区分", "種別", "異動内容"),
}

_REQUIRED = ("license_no", "name")


def _clean(cell) -> str:
    return re.sub(r"\s+", "", nfkc("" if cell is None else str(cell)))


def map_header(row) -> dict[str, int] | None:
    """見出し行なら {項目: 列番号} を返す。番号と名称の列がなければ None。"""
    mapping: dict[str, int] = {}
    cells = [_clean(c) for c in row]
    for field, words in ALIASES.items():
        for word in words:
            for i, c in enumerate(cells):
                if word in c and i not in mapping.values():
                    # 「代表者氏名」を名称列と取り違えない
                    if field == "name" and "代表" in c:
                        continue
                    # 「指定番号」は番号、「有効期間」は開始日と取り違えない
                    if field == "valid_until" and ("開始" in c or "から" in c):
                        continue
                    mapping[field] = i
                    break
            if field in mapping:
                break
    return mapping if all(f in mapping for f in _REQUIRED) else None


def find_header(rows: list, scan: int = 20) -> tuple[int, dict[str, int]] | None:
    for i, row in enumerate(rows[:scan]):
        m = map_header(row)
        if m:
            return i, m
    return None


def extract(row, mapping: dict[str, int]) -> dict | None:
    rec = {}
    for field, i in mapping.items():
        v = row[i] if i < len(row) else None
        rec[field] = v.strip() if isinstance(v, str) else v
    if not rec.get("license_no") or not rec.get("name"):
        return None
    rec["license_no"] = str(rec["license_no"]).strip()
    rec["name"] = re.sub(r"\s*\n\s*", "", str(rec["name"]))
    if isinstance(rec.get("address"), str):
        rec["address"] = re.sub(r"\s*\n\s*", "", rec["address"])
    return rec
