"""名簿の表の見出しを licensee の項目に対応付ける。"""

from __future__ import annotations

import re

from ..normalize import nfkc
from .config import ParserConfig

# 見出しに含まれる語 → 項目。長い語から順に当てる。自治体ごとの言い回しは ParserConfig.aliases で足す。
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

# 既存のパーサー（Excel・CSV・PDF）は番号の列がある名簿だけを読む。原本に許可番号の欄が無い制度は
# 抽出データ（extracts.py）で取り込む。番号の列を読み落とした表をここで黙って通さないため必須のまま。
_REQUIRED = ("license_no", "name")


def _clean(cell) -> str:
    return re.sub(r"\s+", "", nfkc("" if cell is None else str(cell)))


def map_header(row, config: ParserConfig | None = None) -> dict[str, int] | None:
    """見出し行なら {項目: 列番号} を返す。番号と名称の列がなければ None。"""
    config = config or ParserConfig()
    cells = [_clean(c) for c in row]
    mapping: dict[str, int] = {}
    # 設定で決めた列が先
    for f, col in config.columns.items():
        if isinstance(col, int):
            mapping[f] = col - 1
        elif _clean(col) in cells:
            mapping[f] = cells.index(_clean(col))
    for f, words in ALIASES.items():
        if f in mapping:
            continue
        for word in sorted({*words, *config.aliases.get(f, ())}, key=len, reverse=True):
            for i, c in enumerate(cells):
                if word in c and i not in mapping.values():
                    # 「代表者氏名」を名称列と、「有効期間開始」を満了日と取り違えない
                    if f == "name" and "代表" in c:
                        continue
                    if f == "valid_until" and ("開始" in c or "から" in c):
                        continue
                    mapping[f] = i
                    break
            if f in mapping:
                break
    return mapping if all(f in mapping for f in _REQUIRED) else None


def find_header(rows: list, config: ParserConfig | None = None, scan: int = 20) -> tuple[int, dict[str, int]] | None:
    config = config or ParserConfig()
    if config.header_row:
        i = config.header_row - 1
        m = map_header(rows[i], config) if i < len(rows) else None
        if m is None:
            raise ValueError(f"{config.header_row}行目に番号・名称の見出しがありません")
        return i, m
    for i, row in enumerate(rows[:scan]):
        m = map_header(row, config)
        if m:
            return i, m
    return None


def extract(row, mapping: dict[str, int], config: ParserConfig | None = None) -> dict | None:
    rec = {}
    for f, i in mapping.items():
        v = row[i] if i < len(row) else None
        rec[f] = v.strip() if isinstance(v, str) else v
    if not rec.get("license_no") or not rec.get("name"):
        return None
    rec["license_no"] = str(rec["license_no"]).strip()
    rec["name"] = re.sub(r"\s*\n\s*", "", str(rec["name"]))
    if config and config.skip(rec["name"]):
        return None
    if isinstance(rec.get("address"), str):
        rec["address"] = re.sub(r"\s*\n\s*", "", rec["address"])
    return rec
