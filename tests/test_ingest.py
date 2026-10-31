import csv
import datetime as dt

from conftest import TODAY
from fixtures import make_pdf, make_xlsx

from gyosya_map.ingest import apply_notice_events, import_full_list, store_document
from gyosya_map.ingest.csvfile import parse_csv
from gyosya_map.ingest.excel import parse_excel
from gyosya_map.ingest.pdf import classify_event, parse_notice_pdf, parse_pdf_list

S = "pref-14.demolition"


def _licensee(conn, scheme, no):
    return conn.execute("SELECT * FROM licensee WHERE scheme_id = ? AND license_no = ?", (scheme, no)).fetchone()


def test_excel_full_list_import_and_replace(conn, tmp_path, raw_dir):
    f = make_xlsx(tmp_path / "kaitai.xlsx", [
        ["神奈川県知事（解－3）第101号", "株式会社サンプル解体", "見本 太郎", "川崎市幸区1-1", "令和5年4月1日", None],
        ["神奈川県知事（解－3）第102号", "テスト工業㈱", "試験 花子", "横浜市西区2-2", "R3.1.10", "R8.1.9"],
    ])
    recs = parse_excel(f)
    assert [r["name"] for r in recs] == ["株式会社サンプル解体", "テスト工業㈱"]
    doc, new = store_document(conn, S, f, kind="full_list", raw_dir=raw_dir, fetched_at=TODAY)
    assert new and (raw_dir / S).exists()
    assert store_document(conn, S, f, kind="full_list", raw_dir=raw_dir)[1] is False   # 同じ原本は1回
    stats = import_full_list(conn, S, recs, doc_id=doc, fetched_at=TODAY, today=TODAY)
    assert stats == {"inserted": 2, "updated": 0, "removed": 0}

    a = _licensee(conn, S, "神奈川県知事（解－3）第101号")
    assert a["valid_from"] == "2023-04-01" and a["valid_until"] == "2028-03-31"   # 有効期間5年から算出
    assert a["status"] == "active" and a["name_key"] == "サンプル解体" and a["source_doc"] == doc
    assert _licensee(conn, S, "神奈川県知事（解－3）第102号")["status"] == "expired"

    # 翌月の名簿から 101 が消えた → 有効でなくなる
    f2 = make_xlsx(tmp_path / "kaitai2.xlsx", [
        ["神奈川県知事（解－3）第102号", "テスト工業㈱", "試験 花子", "横浜市西区2-2", "R8.1.10", "R13.1.9"],
    ])
    doc2, _ = store_document(conn, S, f2, kind="full_list", raw_dir=raw_dir)
    stats = import_full_list(conn, S, parse_excel(f2), doc_id=doc2, fetched_at=TODAY, today=TODAY)
    assert stats == {"inserted": 0, "updated": 1, "removed": 1}
    assert _licensee(conn, S, "神奈川県知事（解－3）第101号")["status"] == "unknown"
    assert _licensee(conn, S, "神奈川県知事（解－3）第102号")["status"] == "active"


def test_pdf_list_continues_header_across_pages(tmp_path):
    header = ["登録番号", "名称", "所在地", "登録日"]
    f = make_pdf(tmp_path / "list.pdf", [
        ("浄化槽保守点検業者登録名簿", [header, ["201", "見本メンテ株式会社", "川崎市高津区1", "令和6年4月1日"]]),
        ("<pagebreak>", []),
        (None, [["202", "架空浄化サービス", "川崎市多摩区2", "令和7年5月1日"]]),
    ])
    recs = parse_pdf_list(f)
    assert [(r["license_no"], r["name"]) for r in recs] == [("201", "見本メンテ株式会社"), ("202", "架空浄化サービス")]
    assert all(r["needs_review"] for r in recs)


def test_notice_pdf_event_by_heading(tmp_path):
    header = ["指定番号", "氏名又は名称", "住所", "指定年月日"]
    f = make_pdf(tmp_path / "notice.pdf", [
        ("1 新たに指定した指定給水装置工事事業者", [header, ["301", "サンプル水道", "川崎市中原区1", "令和8年9月1日"]]),
        ("2 指定を廃止した指定給水装置工事事業者", [header, ["302", "見本設備", "川崎市幸区1", "令和8年9月10日"]]),
    ])
    evs = parse_notice_pdf(f)
    assert [(e["license_no"], e["event"]) for e in evs] == [("301", "new"), ("302", "revoke")]


def test_classify_event_order():
    assert classify_event("指定の取消し") == "revoke"
    assert classify_event("指定の更新") == "renew"
    assert classify_event("新たに指定") == "new"


def test_notice_events_accumulate(conn, raw_dir, tmp_path):
    w = "kawasaki-suido.water"
    f = tmp_path / "n1.csv"
    with open(f, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows([
            ["区分", "指定番号", "氏名又は名称", "住所"],
            ["新規", "401", "株式会社サンプル管工", "川崎市中原区1"],
            ["新規", "402", "見本水道", "川崎市幸区1"],
        ])
    evs = parse_csv(f)
    doc, _ = store_document(conn, w, f, kind="notice", raw_dir=raw_dir)
    apply_notice_events(conn, w, evs, doc_id=doc, notice_date=dt.date(2021, 9, 30), today=TODAY)
    row = _licensee(conn, w, "401")
    assert row["valid_from"] == "2021-09-30" and row["valid_until"] == "2026-09-29" and row["status"] == "expired"

    # 更新で有効期間が延びる、廃止で revoked、変更で名称が変わる
    apply_notice_events(conn, w, [
        {"event": "renew", "license_no": "401", "name": "株式会社サンプル管工", "valid_from": "令和8年9月30日"},
        {"event": "revoke", "license_no": "402", "name": "見本水道"},
        {"event": "revoke", "license_no": "499", "name": "記録のない業者"},
    ], doc_id=doc, notice_date=dt.date(2026, 9, 30), today=TODAY)
    row = _licensee(conn, w, "401")
    assert row["valid_until"] == "2031-09-29" and row["status"] == "active"
    assert _licensee(conn, w, "402")["status"] == "revoked"
    assert _licensee(conn, w, "499")["status"] == "revoked"
    apply_notice_events(conn, w, [{"event": "change", "license_no": "401", "name": "サンプル管工合同会社"}],
                        doc_id=doc, notice_date=dt.date(2026, 10, 30), today=TODAY)
    row = _licensee(conn, w, "401")
    assert row["name"] == "サンプル管工合同会社" and row["valid_until"] == "2031-09-29"


def test_parser_config_columns_header_row_and_skip(tmp_path):
    from gyosya_map.ingest import ParserConfig, parse_file

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "表紙"
    ws.append(["番号", "名称"])                      # 読まないシート
    ws.append(["X", "表紙の行"])
    ws2 = wb.create_sheet("一覧")
    ws2.append(["令和8年10月1日現在"])
    ws2.append(["No.", "登録番号（知事）", "屋号", "営業所", "期限"])
    ws2.append([1, "A-1", "サンプル電設", "川崎市", "令和12年3月31日"])
    ws2.append([None, "-", "以下余白", None, None])
    f = tmp_path / "x.xlsx"
    wb.save(f)
    cfg = ParserConfig(sheets=["一覧"], header_row=2, columns={"license_no": "登録番号（知事）", "valid_until": 5},
                       aliases={"name": ["屋号"], "address": ["営業所"]}, skip_pattern="以下余白")
    assert parse_file(f, cfg) == [{"license_no": "A-1", "name": "サンプル電設", "address": "川崎市",
                                  "valid_until": "令和12年3月31日"}]


def test_parser_config_validation():
    import pytest

    from gyosya_map.ingest import ParserConfig
    with pytest.raises(ValueError):
        ParserConfig(format="html")
    with pytest.raises(ValueError):
        ParserConfig(columns={"phone": 3})
    with pytest.raises(ValueError):
        ParserConfig(default_event="delete")


def test_notice_config_events_and_default(tmp_path):
    from gyosya_map.ingest import ParserConfig, parse_file

    header = ["指定番号", "氏名又は名称", "住所"]
    f = make_pdf(tmp_path / "n.pdf", [("指定の効力を失った事業者", [header, ["9", "見本設備", "川崎市"]])])
    assert parse_file(f, ParserConfig(events={"revoke": ["効力を失った"]}), notice=True)[0]["event"] == "revoke"
    f2 = make_pdf(tmp_path / "n2.pdf", [("告示第100号", [header, ["8", "見本設備", "川崎市"]])])
    assert parse_file(f2, notice=True) == []                                  # 種類が分からない行は捨てる
    assert parse_file(f2, ParserConfig(default_event="new"), notice=True)[0]["event"] == "new"


def test_scheme_parser_config_from_seed(conn):
    from gyosya_map import db
    from gyosya_map.ingest import load_for_scheme

    db.load_seed_data(conn, {"prefecture": "14", "schemes": [{
        "scheme_id": "muni-142051.waste", "authority_id": "muni-142051", "trade": "waste",
        "license_name": "藤沢市一般廃棄物収集運搬業許可", "parser": {"header_row": 4, "aliases": {"name": ["屋号"]}}}],
        "municipalities": {"藤沢市": {"public_sewer": True}}})
    cfg = load_for_scheme(conn, "muni-142051.waste")
    assert cfg.header_row == 4 and cfg.aliases == {"name": ["屋号"]}


# 川崎市の告示（2026年10月時点）と同じ書式。業者・代表者は架空
WATER_NOTICE = """川崎市上下水道局告示第５４号
川崎市上下水道局指定給水装置工事事業者の指定について
川崎市上下水道局指定給水装置工事事業者規程（平成１０年川崎市水道局規
程第３号）第４条の規定に基づき、川崎市上下水道局指定給水装置工事事業者
として次の者を指定したので、同規程第９条第１号の規定により告示します。
令和８年９月３０日
川崎市上下水道事業管理者 見本 太郎
１ 指 定 番 号 第２０９６号
氏名又は名称 株式会社ＳＡＭＰＬＥ
住 所 横浜市緑区西八朔町１番地
代 表 者 氏 名 架空 一郎
指 定 年 月 日 令和８年１０月１日
有 効 期 限 令和１３年９月３０日
２ 指 定 番 号 第２０９７号
氏名又は名称 株式会社ＴＥＳＴ ＨＯＵＳＥ
住 所 川崎市多摩区宿河原二丁目１番１号
代 表 者 氏 名 架空 二郎
指 定 年 月 日 令和８年１０月１日
有 効 期 限 令和１３年９月３０日""".splitlines()

SEWER_NOTICE = """川崎市上下水道局告示第３２号
川崎市排水設備指定工事店の指定について
川崎市排水設備指定工事店の指定等に関する規程（平成２２年川崎市水道局
規程第６４号）第５条の規定に基づき、川崎市排水設備指定工事店として指定
したので、同規程第１２条第１号の規定により告示します。
令和８年６月２５日
川崎市上下水道事業管理者 見 本 太 郎
１ 指定有効期間
令和 ８年 ７月０１日から
令和１３年 ４月３０日まで
２ 指定工事店
指定番号 １３０６
商号又は名称 見本管工事有限会社
営業所所在地 横浜市泉区中田西四丁目１番１号
代表者氏名 架空 三郎
指定番号 １９７
商号又は名称 株式会社サンプル工業
営業所所在地 川崎市幸区遠藤町１番１
代表者氏名 架空 四郎""".splitlines()


def _text_pdf(path, lines):
    return make_pdf(path, [(line, []) for line in lines])


def test_text_notice_water(tmp_path):
    from gyosya_map.ingest.pdf import detect_notice_date

    f = _text_pdf(tmp_path / "7422.pdf", WATER_NOTICE)
    evs = parse_notice_pdf(f)
    assert [(e["event"], e["license_no"], e["name"]) for e in evs] == [
        ("new", "第2096号", "株式会社SAMPLE"), ("new", "第2097号", "株式会社TEST HOUSE")]
    assert evs[1]["address"] == "川崎市多摩区宿河原二丁目1番1号"
    assert (evs[0]["valid_from"], evs[0]["valid_until"]) == ("令和8年10月1日", "令和13年9月30日")
    assert evs[0]["representative"] == "架空 一郎"
    assert detect_notice_date(f) == dt.date(2026, 9, 30)


def test_text_notice_sewer_with_common_period(tmp_path, conn, raw_dir):
    from gyosya_map.ingest.pdf import detect_notice_date

    f = _text_pdf(tmp_path / "6517.pdf", SEWER_NOTICE)
    evs = parse_notice_pdf(f)
    assert [(e["event"], e["license_no"], e["name"], e["address"]) for e in evs] == [
        ("new", "1306", "見本管工事有限会社", "横浜市泉区中田西四丁目1番1号"),
        ("new", "197", "株式会社サンプル工業", "川崎市幸区遠藤町1番1")]
    assert all((e["valid_from"], e["valid_until"]) == ("令和 8年 7月01日", "令和13年 4月30日") for e in evs)
    d = detect_notice_date(f)
    assert d == dt.date(2026, 6, 25)
    doc, _ = store_document(conn, "kawasaki-suido.sewer", f, kind="notice", raw_dir=raw_dir)
    apply_notice_events(conn, "kawasaki-suido.sewer", evs, doc_id=doc, notice_date=d, today=TODAY)
    row = _licensee(conn, "kawasaki-suido.sewer", "197")
    assert (row["valid_from"], row["valid_until"], row["status"]) == ("2026-07-01", "2031-04-30", "active")


def test_text_notice_revoke_section(tmp_path):
    lines = ["指定給水装置工事事業者の指定の廃止について", "次の者から廃止の届出があったので告示します。",
             "令和８年９月３０日", "川崎市上下水道事業管理者 見本 太郎",
             "１ 指 定 番 号 第１号", "氏名又は名称 架空設備", "住 所 川崎市幸区１"]
    assert [(e["event"], e["license_no"]) for e in parse_notice_pdf(_text_pdf(tmp_path / "r.pdf", lines))] == \
        [("revoke", "第1号")]
