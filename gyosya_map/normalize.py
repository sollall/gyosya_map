"""商号・番号・住所・日付の正規化。"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

# 旧字体・異体字 → 新字体（商号・地名の表記ゆれ）
_ITAIJI = str.maketrans({
    "﨑": "崎", "嵜": "崎", "髙": "高", "邊": "辺", "邉": "辺", "濱": "浜", "濵": "浜",
    "齋": "斎", "齊": "斉", "澤": "沢", "櫻": "桜", "廣": "広", "國": "国", "德": "徳",
    "眞": "真", "惠": "恵", "榮": "栄", "藏": "蔵", "龍": "竜", "曾": "曽", "冨": "富",
    "淵": "渕", "條": "条", "壽": "寿", "實": "実", "驒": "騨", "槇": "槙",
    "ヶ": "ケ", "ヵ": "ケ",
})

# 法人格の表記（前株・後株どちらでも外す）
_CORP_FORMS = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社", "一般社団法人", "公益社団法人",
    "一般財団法人", "公益財団法人", "特定非営利活動法人", "NPO法人", "協同組合", "企業組合",
    "(株)", "(有)", "(合)", "(同)", "(資)", "(名)", "(社)", "(財)", "(一社)", "(公社)",
]
_CORP_RE = re.compile("|".join(re.escape(f) for f in sorted(_CORP_FORMS, key=len, reverse=True)))
_SPACE_RE = re.compile(r"[\s　・･.,，、]+")
_DASH_RE = re.compile(r"[‐‑‒–—―−ーｰ\-]")


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def strip_corp_form(name: str) -> str:
    """商号から法人格（株式会社・(株) など）を外す。前株・後株どちらでも。"""
    return _CORP_RE.sub("", name or "")


def normalize_name(name: str) -> str:
    """商号の照合キー。全角半角・法人格の前後・空白・旧字体を吸収する。"""
    s = nfkc(name).translate(_ITAIJI)
    s = _CORP_RE.sub("", s)
    s = _SPACE_RE.sub("", s)
    return s.lower()


def normalize_license_no(no: str) -> str:
    """番号の照合キー。番号は必ず scheme_id とセットで扱うこと。"""
    s = nfkc(no)
    s = s.replace("第", "").replace("号", "")
    s = _DASH_RE.sub("-", s)
    s = re.sub(r"\s+", "", s)
    # 先頭ゼロの差は同一とみなす（"0123" と "123"）
    s = re.sub(r"(?<![0-9])0+(?=[0-9])", "", s)
    return s.lower()


_KANJI_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9}


def kanji_to_int(s: str) -> int | None:
    """「十二」「二十三」「百五」程度の漢数字を整数に。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _KANJI_DIGITS:
            cur = cur * 10 + _KANJI_DIGITS[ch] if cur else _KANJI_DIGITS[ch]
        elif ch in "十百千":
            unit = {"十": 10, "百": 100, "千": 1000}[ch]
            total += (cur or 1) * unit
            cur = 0
        else:
            return None
    return total + cur


def normalize_address(address: str) -> str:
    s = nfkc(address).translate(_ITAIJI)
    s = re.sub(r"\s+", "", s)
    s = _DASH_RE.sub("-", s)
    # 「三丁目」→「3丁目」
    s = re.sub(r"([〇零一二三四五六七八九十百]+)丁目",
               lambda m: f"{kanji_to_int(m.group(1))}丁目", s)
    return s


_ERAS = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018,
         "M": 1867, "T": 1911, "S": 1925, "H": 1988, "R": 2018}
ERA_RE = re.compile(r"(明治|大正|昭和|平成|令和|[MTSHR])\s*(元|[0-9]+)\s*[年.\-/]\s*([0-9]+)\s*[月.\-/]\s*([0-9]+)\s*日?")
AD_RE = re.compile(r"([0-9]{4})\s*[年.\-/]\s*([0-9]{1,2})\s*[月.\-/]\s*([0-9]{1,2})\s*日?")


def parse_date(value) -> dt.date | None:
    """和暦・西暦・Excelの日付をISO日付に。読めなければ None。"""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = nfkc(str(value)).strip().upper()
    m = ERA_RE.search(s)
    if m:
        era, y, mo, d = m.groups()
        year = _ERAS[era] + (1 if y == "元" else int(y))
        return _safe_date(year, int(mo), int(d))
    m = AD_RE.search(s)
    if m:
        return _safe_date(*(int(x) for x in m.groups()))
    return None


def _safe_date(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def to_wareki(d: dt.date) -> str:
    """表示用の和暦（令和のみ対応、それ以前は西暦）。"""
    if d >= dt.date(2019, 5, 1):
        y = d.year - 2018
        return f"令和{'元' if y == 1 else y}年{d.month}月{d.day}日"
    return f"{d.year}年{d.month}月{d.day}日"
