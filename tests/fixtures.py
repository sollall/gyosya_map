"""テスト用の名簿・告示ファイル・抽出データを作る。業者名・番号はすべて架空。"""

import csv
import json
from pathlib import Path

DROP = object()   # その項目を書かない（必須項目の欠けを試すため）

TOWN_CSV_HEADER = ["都道府県コード", "都道府県名", "都道府県名カナ", "都道府県名ローマ字", "市区町村コード",
                   "市区町村名", "市区町村名カナ", "市区町村名ローマ字", "大字町丁目名", "大字町丁目名カナ",
                   "大字町丁目名ローマ字", "小字・通称名", "緯度", "経度"]


def make_towns(path: Path, rows: list[tuple]) -> Path:
    """Geolonia 住所データの形のCSV。行は (市区町村コード, 市区町村名, 町丁目名, 小字, 緯度, 経度)。"""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(TOWN_CSV_HEADER)
        for code, city, town, koaza, lat, lng in rows:
            w.writerow(["14", "神奈川県", "", "", code, city, "", "", town, "", "", koaza, lat, lng])
    return path


def extract_row(**over) -> dict:
    """抽出データ（rows.jsonl）の1行。DROP を渡すとその項目を書かない。"""
    row = {"license_no": "第1号", "name": "株式会社サンプル解体", "address": "川崎市中原区上小田中3-1-1",
           "representative": "見本 太郎", "valid_from": "2026-04-01", "valid_until": "2031-03-31",
           "sub_scope": None, "status": "active"}
    row.update(over)
    return {k: v for k, v in row.items() if v is not DROP}


def passed_review(sampled_rows=(1,), **over) -> dict:
    review = {"reviewed_by": "検品エージェント", "reviewed_at": "2026-11-05T11:00:00", "result": "passed",
              "method": "原本の件数と行数の一致を確認し、全行を原本の表示と突き合わせた",
              "sampled_rows": list(sampled_rows)}
    review.update(over)
    return {k: v for k, v in review.items() if v is not DROP}


def make_extract(root: Path, rows: list[dict], *, scheme_id: str = "pref-14.demolition",
                 date: str = "2026-11-05", rows_text: str | None = None, **manifest) -> Path:
    """data_extracts/<scheme_id>/<date>/ を作る。manifest の項目は上書き・DROP で削除できる。"""
    d = Path(root) / scheme_id / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "rows.jsonl").write_text(
        rows_text if rows_text is not None else
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    m = {"scheme_id": scheme_id, "source_url": "https://example.invalid/meibo.xlsx",
         "source_path": f"data/raw/{scheme_id}/meibo-{date}.xlsx", "source_sha256": "0" * 64,
         "fetched_at": date, "kind": "full_list", "published_on": None,
         "extracted_by": "テスト抽出エージェント", "extracted_at": f"{date}T10:00:00",
         "source_row_count": len(rows), "terms_of_use": "政府標準利用規約2.0", "notes": None, "review": None}
    m.update(manifest)
    (d / "manifest.json").write_text(
        json.dumps({k: v for k, v in m.items() if v is not DROP}, ensure_ascii=False, indent=2), "utf-8")
    return d


def make_xlsx(path: Path, rows: list[list], title_rows: int = 2) -> Path:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(title_rows):
        ws.append([f"解体工事業者登録簿（タイトル行{i + 1}）"])
    ws.append(["登録番号", "商号又は名称", "代表者氏名", "主たる営業所の所在地", "登録年月日", "有効期間満了日"])
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def make_pdf(path: Path, sections: list[tuple[str | None, list[list[str]]]]) -> Path:
    """見出しと表の組を並べたPDF。見出しが None なら表だけ（続きのページ）。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    style = ParagraphStyle("ja", fontName="HeiseiKakuGo-W5", fontSize=11)
    story = []
    for heading, table in sections:
        if heading == "<pagebreak>":
            story.append(PageBreak())
            continue
        if heading:
            story += [Paragraph(heading, style), Spacer(1, 8)]
        if not table:
            continue
        t = Table(table)
        t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "HeiseiKakuGo-W5"),
                               ("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        story += [t, Spacer(1, 16)]
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return path
