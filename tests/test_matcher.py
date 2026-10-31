import datetime as dt
import itertools

import pytest
from conftest import TODAY

from gyosya_map import freshness
from gyosya_map.ingest import apply_notice_events, import_full_list
from gyosya_map.ingest.fetch import check_pages
from gyosya_map.matcher import (AUTHORIZED, EXPIRED, NO_SCHEME, NOT_FOUND, OTHER_ONLY, UNDETERMINED, check)

KAWASAKI = "神奈川県川崎市中原区小杉町3丁目1"
_seq = itertools.count()


def _doc(conn, scheme_id, url="https://example.invalid/list"):
    cur = conn.execute("INSERT INTO source_document (scheme_id, url, path, sha256, kind, fetched_at) "
                       "VALUES (?, ?, 'x', ?, 'full_list', ?)",
                       (scheme_id, url, f"fake{next(_seq)}", TODAY.isoformat()))
    return cur.lastrowid


def _full(conn, scheme_id, records, fetched_at=TODAY):
    import_full_list(conn, scheme_id, records, doc_id=_doc(conn, scheme_id), fetched_at=fetched_at, today=TODAY)


def _notice(conn, scheme_id, events, date=dt.date(2026, 9, 30)):
    apply_notice_events(conn, scheme_id, events, doc_id=_doc(conn, scheme_id), notice_date=date,
                        fetched_at=TODAY, today=TODAY)


def test_no_scheme_trade(conn):
    r = check(conn, address=KAWASAKI, trade="locksmith", name="どこかの鍵屋")
    assert r.verdict == NO_SCHEME


def test_authorized_by_number_with_expiry_message(conn):
    _notice(conn, "kawasaki-suido.water", [
        {"event": "new", "license_no": "第１２３号", "name": "株式会社サンプル管工", "valid_from": "令和8年10月1日"}])
    r = check(conn, address=KAWASAKI, trade="water", license_no="123", today=TODAY)
    assert r.verdict == AUTHORIZED
    assert r.message == "川崎市上下水道局の川崎市指定給水装置工事事業者あり（有効期限：令和13年9月30日）"
    assert r.evidence[0].source_url == "https://example.invalid/list"
    assert r.evidence[0].fetched_at == TODAY.isoformat()
    # 商号の表記ゆれでも引ける
    assert check(conn, address=KAWASAKI, trade="water", name="サンプル管工（株）", today=TODAY).verdict == AUTHORIZED


def test_other_authority_only(conn):
    _notice(conn, "yokohama-suido.water", [{"event": "new", "license_no": "Y-77", "name": "見本設備"}])
    r = check(conn, address=KAWASAKI, trade="water", license_no="Y-77", today=TODAY)
    assert r.verdict == OTHER_ONLY
    assert r.message == "横浜市水道局の公認はあるが、川崎市上下水道局の公認は確認できない"
    assert any("完全ではない" in w for w in r.warnings)


def test_expired_and_revoked(conn):
    _notice(conn, "kawasaki-suido.water", [{"event": "new", "license_no": "5", "name": "古い設備"}],
            date=dt.date(2020, 4, 1))
    r = check(conn, address=KAWASAKI, trade="water", license_no="5", today=TODAY)
    assert r.verdict == EXPIRED and "令和7年3月31日に有効期限切れ" in r.message
    _notice(conn, "kawasaki-suido.water", [{"event": "revoke", "license_no": "5", "name": "古い設備"}])
    assert "取消" in check(conn, address=KAWASAKI, trade="water", license_no="5", today=TODAY).message


def test_notice_diff_not_found_is_undetermined(conn):
    _notice(conn, "kawasaki-suido.water", [{"event": "new", "license_no": "1", "name": "別の業者"}])
    r = check(conn, address=KAWASAKI, trade="water", name="載っていない業者", today=TODAY)
    assert r.verdict == UNDETERMINED          # 名簿に無い＝無指定 と断定しない
    assert "川崎市上下水道局" in r.message


def test_high_completeness_not_found(conn):
    _full(conn, "muni-141305.septic-maintenance", [{"license_no": "201", "name": "見本メンテ"}])
    r = check(conn, address=KAWASAKI, trade="septic", name="載っていない業者", today=TODAY)
    assert r.verdict == NOT_FOUND
    # important の注意点は必ず出る
    assert any(n["severity"] == "important" and "直営" in n["body"] for n in r.notes)


def test_demolition_requires_both_sources_to_conclude(conn):
    _full(conn, "pref-14.demolition", [{"license_no": "D1", "name": "サンプル解体"}])
    assert check(conn, address=KAWASAKI, trade="demolition", name="サンプル解体", today=TODAY).verdict == AUTHORIZED
    # 解体登録に無くても建設業許可を確かめられないので判定不能
    r = check(conn, address=KAWASAKI, trade="demolition", name="無登録解体", today=TODAY)
    assert r.verdict == UNDETERMINED and "検索システム" in r.message

    class FakeSearch:
        def lookup(self, scheme, license_no, name):
            return [{"license_no": "般-5-999", "name": "建設許可工務店"}] if name == "建設許可工務店" else []

    assert check(conn, address=KAWASAKI, trade="demolition", name="無登録解体", today=TODAY,
                 search_client=FakeSearch()).verdict == NOT_FOUND
    r = check(conn, address=KAWASAKI, trade="reform", name="建設許可工務店", today=TODAY, search_client=FakeSearch())
    assert r.verdict == AUTHORIZED


def test_search_system_failure_is_not_a_complete_roster(conn):
    """問い合わせ失敗（None）を「名簿に見当たらない」にしない。"""

    class Broken:
        last_error = "検索システムに接続できません: timed out"

        def lookup(self, scheme, license_no, name):
            return None

    r = check(conn, address=KAWASAKI, trade="reform", name="建設許可工務店", today=TODAY, search_client=Broken())
    assert r.verdict == UNDETERMINED
    assert all(s.completeness == "none" for s in r.schemes)
    assert any("timed out" in w for w in r.warnings)
    assert "問い合わせできなかった" in r.message


def test_waste_sub_scope(conn):
    conn.execute("UPDATE scheme SET publication_format = 'full_list_pdf' WHERE scheme_id = 'muni-141305.waste'")
    _full(conn, "muni-141305.waste", [
        {"license_no": "W1", "name": "範囲あり回収", "sub_scope": "一時多量ごみ・事業系"},
        {"license_no": "W2", "name": "範囲なし回収", "sub_scope": "事業系一般廃棄物"},
    ])
    assert check(conn, address=KAWASAKI, trade="waste", license_no="W1", today=TODAY).verdict == AUTHORIZED
    r = check(conn, address=KAWASAKI, trade="waste", license_no="W2", today=TODAY)
    assert r.verdict == NOT_FOUND and "一時多量ごみ" in r.message


def test_sewer_out_of_area(conn):
    conn.execute("INSERT INTO jurisdiction_override VALUES ('141330', '小杉町', 'sewer', NULL, '公共下水道の区域外')")
    r = check(conn, address=KAWASAKI, trade="sewer", name="x", today=TODAY)
    assert r.verdict == NO_SCHEME and "区域外" in r.message


def test_unmapped_region_is_undetermined(conn):
    r = check(conn, address="横浜市中区本町1", trade="sewer", name="x", today=TODAY)
    assert r.verdict == UNDETERMINED and "整備されていません" in r.message


def test_requires_name_or_number(conn):
    with pytest.raises(ValueError):
        check(conn, address=KAWASAKI, trade="water")


def test_stale_roster_warning_and_auto_expire(conn):
    _full(conn, "pref-14.demolition",
          [{"license_no": "D9", "name": "期限間近解体", "valid_until": "2026-11-15"}],
          fetched_at=TODAY - dt.timedelta(days=91))
    r = check(conn, address=KAWASAKI, trade="demolition", name="期限間近解体", today=TODAY)
    assert any("90日" in w for w in r.warnings)
    assert freshness.expire(conn, dt.date(2026, 11, 16)) == 1
    r = check(conn, address=KAWASAKI, trade="demolition", name="期限間近解体", today=dt.date(2026, 11, 16))
    assert r.verdict == EXPIRED


def test_representative_not_in_result(conn):
    _full(conn, "pref-14.demolition", [{"license_no": "D2", "name": "表示テスト", "representative": "見本 太郎"}])
    r = check(conn, address=KAWASAKI, trade="demolition", name="表示テスト", today=TODAY)
    assert "見本 太郎" not in str(r.to_dict()) and "見本 太郎" not in r.render()


def test_scheme_excluded_municipality_is_not_a_candidate(conn):
    """制度が対象外としている市区町村では、その制度を候補にしない。

    対象外の市の住所で県の名簿に当たると、「県の名簿に無い＝未登録」という誤判定になる。
    """
    r = check(conn, address="埼玉県川越市元町1-3-1", trade="septic", name="載っていない業者", today=TODAY)
    names = [s.license_name for s in r.schemes]
    assert "埼玉県浄化槽保守点検業者登録" not in names      # 川越市は県知事登録の対象外
    assert "埼玉県浄化槽工事業登録" in names                # 工事業は県が全域
    assert r.verdict == UNDETERMINED
    assert any("対象外" in w for w in r.warnings)
    # 対象の市なら候補に残る
    tokorozawa = check(conn, address="埼玉県所沢市並木1-1", trade="septic", name="載っていない業者", today=TODAY)
    assert "埼玉県浄化槽保守点検業者登録" in [s.license_name for s in tokorozawa.schemes]
    # 政令市の行政区の住所も、市のコードで対象外になる（市自身の制度は残る）
    saitama = check(conn, address="埼玉県さいたま市西区西大宮1-1", trade="septic",
                    name="載っていない業者", today=TODAY)
    names = [s.license_name for s in saitama.schemes]
    assert "埼玉県浄化槽保守点検業者登録" not in names and "さいたま市浄化槽保守点検業者登録" in names


def test_excluded_scheme_is_still_other_authority(conn):
    """対象外として外した制度に載っていれば「他機関の公認のみ」として根拠に出す。"""
    _full(conn, "pref-11.septic-maintenance", [{"license_no": "S1", "name": "県登録メンテ"}])
    r = check(conn, address="埼玉県川越市元町1-3-1", trade="septic", license_no="S1", today=TODAY)
    assert r.verdict == OTHER_ONLY
    assert [e.license_name for e in r.evidence] == ["埼玉県浄化槽保守点検業者登録"]
    # 機関名はどちらも「埼玉県」なので、制度名で区別して出す
    assert r.message == "埼玉県浄化槽保守点検業者登録はあるが、埼玉県浄化槽工事業登録は確認できない"


def test_page_monitor_queues_changes_and_errors(conn):
    bodies = {}

    def fetcher(url):
        if "tebiki" in url:
            raise OSError("404")
        return bodies.get(url, b"v1")

    first = check_pages(conn, fetcher)
    assert {r["result"] for r in first} == {"first", "error"}
    url = "https://www.pref.kanagawa.jp/docs/u2h/cnt/f531856/p449157.html"
    bodies[url] = b"v2"
    second = {r["url"]: r["result"] for r in check_pages(conn, fetcher)}
    assert second[url] == "changed"
    reasons = [r[0] for r in conn.execute("SELECT reason FROM refetch_queue WHERE url = ?", (url,))]
    assert reasons == ["掲載ページが変化"]


def test_new_prefecture_needs_only_schemes(conn):
    """既定ルールがあるので、他県は機関と制度を書くだけで照合できる。"""
    from gyosya_map import db
    db.load_seed_data(conn, {"prefecture": "13", "schemes": [{
        "scheme_id": "pref-13.demolition", "authority_id": "pref-13", "trade": "demolition",
        "license_name": "東京都解体工事業登録", "validity_years": 5, "publication_format": "full_list_excel"}]})
    _full(conn, "pref-13.demolition", [{"license_no": "T1", "name": "サンプル解体"}])
    r = check(conn, address="東京都府中市宮西町1", trade="demolition", name="サンプル解体", today=TODAY)
    assert r.verdict == AUTHORIZED and r.message.startswith("東京都の東京都解体工事業登録あり")
    # 神奈川県の住所で照会すると、東京都の登録は他機関の公認
    r = check(conn, address=KAWASAKI, trade="demolition", license_no="T1", today=TODAY)
    assert r.verdict == OTHER_ONLY
