"""全件名簿（PDF）と告示PDFのパーサー。PDF由来の行は目視検品の対象にする。

**新しい自治体の受け皿にはしない。** ここは既存の川崎市・神奈川県分の維持にとどめる。
PDFの書式は自治体ごとに違い、ここに分岐を足すほどコードが読めなくなって壊れやすくなるため、
新しい自治体は原本の読み取りを収集担当のエージェントに任せ、正規化済みの抽出データ
（`data_extracts/`、`doc/extract-format.md`）を `import-extract` で取り込む。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..normalize import nfkc
from .columns import ALIASES, extract, map_header
from .config import ParserConfig

# 告示の見出し → 異動の種類。上から順に判定する（「指定の取消」を「指定」と誤らない）
_EVENT_WORDS = (
    ("revoke", ("廃止", "取消", "取り消", "失効", "辞退")),
    ("renew", ("更新",)),
    ("change", ("変更",)),
    ("new", ("新規", "指定", "登録", "許可")),
)


def classify_event(text: str, config: ParserConfig | None = None) -> str | None:
    text = re.sub(r"\s+", "", text or "")
    extra = config.events if config else {}
    # 設定で足した語は既定の語より先に見る（自治体独自の言い回しを優先）
    for event, words in extra.items():
        if any(w in text for w in words):
            return event
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


def parse_pdf_list(path: str | Path, config: ParserConfig | None = None) -> list[dict]:
    """全件名簿PDF。見出しのない続きのページは直前の見出しを引き継ぐ。"""
    config = config or ParserConfig()
    records: list[dict] = []
    mapping = None
    for _, rows in _tables(path):
        for row in rows:
            m = map_header(row, config)
            if m:
                mapping = m
                continue
            if mapping:
                rec = extract(row, mapping, config)
                if rec:
                    rec["needs_review"] = True
                    records.append(rec)
    if not records:
        records = [{**r, "needs_review": True} for r in parse_text_records(_lines(path), config)]
        for r in records:
            r.pop("event", None)
    return records


def parse_notice_pdf(path: str | Path, config: ParserConfig | None = None) -> list[dict]:
    """告示PDF。表ごとに直前の見出し（新たに指定／更新／変更／廃止）で異動の種類を決める。"""
    config = config or ParserConfig()
    events: list[dict] = []
    mapping = None
    event = config.default_event
    for above, rows in _tables(path):
        heading_event = None
        for line in reversed(above.splitlines()):
            heading_event = classify_event(line, config)
            if heading_event:
                break
        if heading_event:
            event = heading_event
        for row in rows:
            m = map_header(row, config)
            if m:
                mapping = m
                continue
            if mapping:
                rec = extract(row, mapping, config)
                if not rec:
                    continue
                rec["event"] = classify_event(str(rec.get("event") or ""), config) or event
                if rec["event"] is None:
                    continue
                rec["needs_review"] = True
                events.append(rec)
    if not events:
        # 表になっていない告示（「指定番号 第2096号」「氏名又は名称 ○○」の行が並ぶ形）
        events = [{**r, "needs_review": True} for r in parse_text_records(_lines(path), config, notice=True)
                  if r.get("event")]
    return events


_ISSUER = re.compile(r"(市長|町長|村長|区長|知事|管理者|局長|大臣)")


def _lines(path: str | Path) -> list[str]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return [nfkc(l).strip() for page in pdf.pages for l in (page.extract_text() or "").splitlines()]


def _label_regex(word: str) -> str:
    # 「指 定 番 号」のように文字の間に空白が入っても当てる
    return r"\s*".join(re.escape(ch) for ch in word)


def _label_patterns(config: ParserConfig) -> list[tuple[str, re.Pattern]]:
    words = []
    for f, aliases in ALIASES.items():
        if f == "event":
            continue
        for w in {*aliases, *config.aliases.get(f, ())}:
            words.append((f, w))
    words.sort(key=lambda fw: len(fw[1]), reverse=True)
    # 行頭の項目番号（「１ 指定番号」の「１」）は読み飛ばす
    return [(f, re.compile(r"^(?:[0-9]+[.．)）]?\s+)?" + _label_regex(w) + r"\s*[:：]?\s*(.*)$")) for f, w in words]


_PERIOD = re.compile(r"有\s*効\s*期\s*間\s*[:：]?\s*(.+?)\s*から\s*(.+?)\s*まで")


def parse_text_records(lines: list[str], config: ParserConfig | None = None, notice: bool = False) -> list[dict]:
    """表になっていない名簿・告示を読む。

        １ 指 定 番 号 第２０９６号
        氏名又は名称 株式会社○○
        住 所 横浜市緑区…
        有 効 期 限 令和１３年９月３０日

    のように「項目名 値」の行が並び、番号の行で1件が始まる形。「指定有効期間 …から …まで」が
    冒頭にまとめて書かれていれば全件に当てはめる。告示なら、題名（「〜の指定について」）や
    途中の見出し（「廃止した者」など）から異動の種類を決める。
    """
    config = config or ParserConfig()
    patterns = _label_patterns(config)
    joined = " ".join(lines)
    common: dict = {}
    if m := _PERIOD.search(joined):
        common = {"valid_from": m.group(1), "valid_until": m.group(2)}

    event = config.default_event
    records: list[dict] = []
    cur: dict | None = None
    for line in lines:
        hit = None
        for f, rx in patterns:
            if m := rx.match(line):
                hit = (f, m.group(1).strip())
                break
        if hit is None or not hit[1]:
            if notice and cur is None or notice and len(line) <= 30:
                # 題名・見出し。新規の語（「指定」）だけでは種類を変えない（「２ 指定工事店」など）
                e = classify_event(line, config)
                if e and (e != "new" or event is None or re.search("新規|新たに", line)):
                    event = e
            continue
        f, value = hit
        if f == "license_no":
            if cur:
                records.append(cur)
            cur = {**common, "license_no": value}
            if notice:
                cur["event"] = event
        elif cur is not None and f not in cur or (cur is not None and f in common):
            cur[f] = value
    if cur:
        records.append(cur)
    return [r for r in records if r.get("license_no") and r.get("name")
            and not (config.skip(r["name"]))]



def detect_notice_date(path: str | Path):
    """告示PDFの告示日を推定する。分からなければ None。

    告示は本文のあとに「令和8年9月30日 川崎市上下水道事業管理者 …」と日付と告示者が並ぶ。
    表の中の指定年月日を拾わないよう、告示者（市長・管理者など）と同じ行か、その直前の
    日付だけの行にある日付だけを採る。
    """
    import pdfplumber

    from ..normalize import AD_RE, ERA_RE, nfkc, parse_date

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:3]:
            lines = [re.sub(r"\s+", "", nfkc(l)) for l in (page.extract_text() or "").splitlines()]
            for i, line in enumerate(lines):
                issuer = _ISSUER.search(line)
                if not issuer:
                    continue
                # 告示者と同じ行で、告示者の前にある日付
                head = line[:issuer.start()]
                dates = [m for rx in (ERA_RE, AD_RE) for m in rx.finditer(head)]
                if dates:
                    return parse_date(max(dates, key=lambda m: m.end()).group(0))
                # 告示者の前の行が日付だけ（表の行の日付は採らない）
                if i and (m := ERA_RE.fullmatch(lines[i - 1]) or AD_RE.fullmatch(lines[i - 1])):
                    return parse_date(m.group(0))
    return None
