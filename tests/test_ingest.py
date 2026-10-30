import csv
import datetime as dt

from conftest import TODAY
from fixtures import make_pdf, make_xlsx

from gyosya_map.ingest import apply_notice_events, import_full_list, store_document
from gyosya_map.ingest.csvfile import parse_csv
from gyosya_map.ingest.excel import parse_excel
from gyosya_map.ingest.pdf import classify_event, parse_notice_pdf, parse_pdf_list

S = "pref-kanagawa.demolition"


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
