"""全件名簿（PDF）と告示PDFのパーサー。PDF由来の行は目視検品の対象にする。"""

from __future__ import annotations

import re
from pathlib import Path

from .columns import extract, map_header

# 告示の見出し → 異動の種類。上から順に判定する（「指定の取消」を「指定」と誤らない）
_EVENT_WORDS = (
    ("revoke", ("廃止", "取消", "取り消", "失効", "辞退")),
    ("renew", ("更新",)),
    ("change", ("変更",)),
    ("new", ("新規", "指定", "登録", "許可")),
)


def classify_event(text: str) -> str | None:
    text = re.sub(r"\s+", "", text or "")
    for event, words in _EVENT_WORDS:
        if any(w in text for w in words):
            return event
    return None


def _tables(path: str | Path):
    """(表の直前の文章, 表の行) をページ順に返す。"""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            prev_bottom = 0
            for table in page.find_tables():
                top = table.bbox[1]
                above = ""
                if top > prev_bottom:
                    above = page.crop((0, prev_bottom, page.width, top)).extract_text() or ""
                prev_bottom = table.bbox[3]
                yield above, table.extract()


def parse_pdf_list(path: str | Path) -> list[dict]:
    """全件名簿PDF。見出しのない続きのページは直前の見出しを引き継ぐ。"""
    records: list[dict] = []
    mapping = None
    for _, rows in _tables(path):
        for row in rows:
            m = map_header(row)
            if m:
                mapping = m
                continue
            if mapping:
                rec = extract(row, mapping)
                if rec:
                    rec["needs_review"] = True
                    records.append(rec)
    return records


def parse_notice_pdf(path: str | Path) -> list[dict]:
    """告示PDF。表ごとに直前の見出し（新たに指定／更新／変更／廃止）で異動の種類を決める。"""
    events: list[dict] = []
    mapping = None
    event = None
    for above, rows in _tables(path):
        heading_event = None
        for line in reversed(above.splitlines()):
            heading_event = classify_event(line)
            if heading_event:
                break
        if heading_event:
            event = heading_event
        for row in rows:
            m = map_header(row)
            if m:
                mapping = m
                continue
            if mapping:
                rec = extract(row, mapping)
                if not rec:
                    continue
                rec["event"] = classify_event(str(rec.get("event") or "")) or event
                if rec["event"] is None:
                    continue
                rec["needs_review"] = True
                events.append(rec)
    return events
