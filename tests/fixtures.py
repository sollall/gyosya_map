"""テスト用の名簿・告示ファイルを作る。業者名・番号はすべて架空。"""

from pathlib import Path


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
