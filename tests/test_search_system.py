"""検索システム（国交省 建設業者検索）への照会。

画面のHTMLは実物と同じ作りにして、業者はすべて架空。ネットワークにはつながない。
"""

import pytest
from conftest import TODAY

from gyosya_map import search_system as ss
from gyosya_map.matcher import AUTHORIZED, EXPIRED, NOT_FOUND, UNDETERMINED, check
from gyosya_map.normalize import strip_corp_form

KAWASAKI = "神奈川県川崎市中原区小杉町3丁目1"

# code（許可行政庁コード＋許可番号）, 許可行政庁, 商号, 本店所在地, 許可の有効期間
KANAGAWA = ("14012345", "神奈川県", "見本建設（株）", "神奈川県川崎市中原区小杉町３－１",
            ("R07年04月01日", "R12年03月31日"))
MINISTER = ("00000777", "関東地方整備局", "（株）見本工務店", "東京都千代田区霞が関２－１－３",
            ("R05年06月01日", "R10年05月31日"))
TOKYO = ("13099999", "東京都", "見本建設（株）", "東京都新宿区西新宿２－８－１",
         ("R06年01月01日", "R10年12月31日"))
SIMILAR = ("14054321", "神奈川県", "見本建設工業（株）", "神奈川県横浜市中区本町１",
           ("R07年04月01日", "R12年03月31日"))
EXPIRED_ONE = ("14077777", "神奈川県", "見本解体（株）", "神奈川県川崎市幸区1",
               ("R02年04月01日", "R07年03月31日"))


def _row_html(record, no):
    code, authority, name, address, _ = record
    return f"""
       <tr>
        <td style="text-align:right;">{no}</td>
        <td style="text-align:left; white-space : nowrap;">{authority}</td>
        <td style="text-align:left; white-space : nowrap;" title="licenseNo">第{code[2:]}号</td>
        <td style="text-align:left;"><a href="javascript:void(0);"
            onclick="js_ShowDetail('{code}')">{name}</a></td>
        <td style="text-align:left; white-space : nowrap;">見本　太郎</td>
        <td style="text-align:left;"></td>
        <td style="text-align:left;">{address}</td>
      </tr>"""


def list_page(records, total=None, headers=ss.LIST_HEADERS):
    total = len(records) if total is None else total
    head = "".join(f"<th>{h}</th>" for h in ("No.", *headers))
    rows = "".join(_row_html(r, i + 1) for i, r in enumerate(records))
    return f"""<html><body>
    <div id="messageArea" style="display:none"><label id="messageText"></label></div>
    <input id="resultCount" name="resultCount" type="hidden" value="{total}"/>
    <input id="pageCount" name="pageCount" type="hidden" value="{max(1, -(-total // ss.PAGE_SIZE))}"/>
    <table class="re_disp"><tr>{head}</tr>{rows}</table>
    </body></html>"""


def message_page(text):
    return f"""<html><body>
    <div id="messageArea"><div class="warning_area"><label id="messageText">
        「{text}」</label></div></div>
    <input id="resultCount" name="resultCount" type="hidden" value="0"/>
    </body></html>"""


def detail_page(record):
    code, authority, name, address, (start, end) = record
    kind = "国土交通大臣許可" if code.startswith("00") else f"{authority}知事許可"
    return f"""<html><body><table class="re_summ">
    <tr><th>許可番号</th><td>{kind}　第{code[2:]}号</td></tr>
    <tr><th>商号又は名称</th><td><p class="phonetic">ミホン</p>{name}</td></tr>
    <tr><th>代表者の氏名</th><td><p class="phonetic">ミホン　タロウ</p>見本　太郎</td></tr>
    <tr><th>主たる営業所の<br />所在地</th><td>〒210-0000<br />{address}</td></tr>
    <tr><th>電話番号</th><td>044-000-0000</td></tr>
    </table>
    <table class="re_summ_5">
    <tr><th>許可の有効期間</th><td>{start}から{end}まで</td></tr>
    <tr><th>許可条件</th><td> </td></tr>
    </table></body></html>"""


class FakeSystem:
    """検索システムの代わり。商号は部分一致、許可番号は他県の同番号も返す（実物と同じ）。"""

    def __init__(self, records):
        self.records = list(records)
        self.calls: list[tuple[str, dict]] = []
        self.requests = 0
        self.fail = None

    def __call__(self, url, fields):
        self.calls.append((url, dict(fields)))
        self.requests += 1
        if self.fail:
            raise self.fail
        if url == ss.DETAIL_URL:
            code = fields["sv_licenseNo"]
            return detail_page(next(r for r in self.records if r[0] == code))
        hits = [r for r in self.records if self._match(r, fields)]
        if not hits:
            return message_page("該当するデータが存在しません。")
        page = int(fields["pageListNo1"]) if fields["CMD"] == "selectPage" else 1
        start = (page - 1) * ss.PAGE_SIZE
        return list_page(hits[start:start + ss.PAGE_SIZE], total=len(hits))

    @staticmethod
    def _match(record, fields):
        code, _, name, _, _ = record
        if fields["licenseNoFrom"]:
            minister = fields["licenseNoKbn"] == "00"
            return code[2:] == fields["licenseNoFrom"] and code.startswith("00") == minister
        if fields["comNameKanjiOnly"]:
            return fields["comNameKanjiOnly"] in strip_corp_form(name)
        return False


def client(records, **kwargs):
    system = FakeSystem(records)
    return ss.MlitConstructionSearch(system, **kwargs), system


PREF_14 = {"scheme_id": "pref-14.construction", "authority_id": "pref-14", "trade": "construction",
           "publication_format": "search_system", "source_url": ss.SEARCH_URL}
MLIT = {**PREF_14, "scheme_id": "mlit.construction", "authority_id": "mlit"}


# --- 画面の読み取り -------------------------------------------------------

def test_parse_results_reads_rows_and_total():
    rows, total = ss.parse_results(list_page([KANAGAWA, MINISTER], total=2))
    assert total == 2
    assert [(r.authority_code, r.license_no, r.name) for r in rows] == [
        ("14", "012345", "見本建設（株）"), ("00", "000777", "（株）見本工務店")]
    assert rows[0].code == "14012345" and rows[0].address.startswith("神奈川県川崎市")


def test_parse_results_no_hit_is_empty_not_an_error():
    assert ss.parse_results(message_page("該当するデータが存在しません。")) == ([], 0)


def test_parse_results_detects_system_change():
    # 表もメッセージもない画面（ログイン画面に変わった、項目名が変わった など）
    with pytest.raises(ss.Changed):
        ss.parse_results("<html><body>お知らせ</body></html>")
    with pytest.raises(ss.Changed):
        ss.parse_results(message_page("ただいま混み合っています。"))
    renamed = ("許可行政庁", "許可番号", "名称", "代表者名", "営業所名", "所在地")
    with pytest.raises(ss.Changed) as e:
        ss.parse_results(list_page([KANAGAWA], headers=renamed))
    assert "商号又は名称" in str(e.value)


def test_parse_detail_drops_the_phonetic_and_reads_the_period():
    detail = ss.parse_detail(detail_page(KANAGAWA))
    assert detail["商号又は名称"] == "見本建設（株）"
    assert ss._period(detail["許可の有効期間"]) == (TODAY.replace(2025, 4, 1), TODAY.replace(2030, 3, 31))
    assert ss._address(detail["主たる営業所の所在地"]) == "神奈川県川崎市中原区小杉町３－１"


def test_parse_detail_detects_system_change():
    with pytest.raises(ss.Changed):
        ss.parse_detail("<html><body><table class=\"re_summ\"><tr><th>許可番号</th><td></td></tr></table></body></html>")


def test_helpers():
    assert ss.license_digits("神奈川県知事許可（般-3）第12345号") == "012345"
    assert ss.license_digits("第002100号") == "002100"
    assert ss.license_digits("許可なし") is None
    assert ss.query_name("株式会社 見本建設") == "見本建設"
    assert ss.authority_code(PREF_14) == "14" and ss.authority_code(MLIT) == "00"
    assert ss.authority_code({"authority_id": "muni-141305"}) is None


# --- 照会 -----------------------------------------------------------------

def test_lookup_by_name_keeps_only_the_scheme_authority():
    """同じ商号の他県・他機関の許可や、部分一致の別業者を混ぜない。"""
    c, system = client([KANAGAWA, TOKYO, SIMILAR, MINISTER])
    found = c.lookup(PREF_14, None, "株式会社見本建設")
    assert [r["license_no"] for r in found] == ["012345"]
    assert found[0]["valid_until"] == "2030-03-31" and found[0]["status"] == "active"
    assert found[0]["address"] == "神奈川県川崎市中原区小杉町３－１"
    assert "見本　太郎" not in str(found)                 # 代表者名は返さない
    assert c.log[-1].state == "found"
    # 検索1回＋業者概要1回（同じ検索は使い回す）
    assert [url for url, _ in system.calls] == [ss.SEARCH_URL, ss.DETAIL_URL]
    assert c.lookup(MLIT, None, "見本建設") == []        # 大臣許可には同名がいない
    assert c.log[-1].state == "not_found"


def test_lookup_by_license_no_ignores_other_prefectures_and_falls_back_to_name():
    same_number = ("13012345", "東京都", "別の見本（株）", "東京都中央区銀座１", ("R07年04月01日", "R12年03月31日"))
    c, _ = client([KANAGAWA, same_number])
    found = c.lookup(PREF_14, "第012345号", None)
    assert [r["name"] for r in found] == ["見本建設（株）"]
    # 番号で該当なしのときは商号で探し直す（matcher と同じ順番）
    c2, system = client([KANAGAWA])
    found = c2.lookup(PREF_14, "第099999号", "見本建設")
    assert [r["license_no"] for r in found] == ["012345"]
    assert [f["CMD"] for _, f in system.calls] == ["search", "search", ""]


def test_lookup_follows_pages_when_the_hit_is_not_on_the_first_page():
    others = [(f"14{i:06d}", "神奈川県", f"見本建設興業{i}（株）", "神奈川県横浜市中区本町1",
               ("R07年04月01日", "R12年03月31日")) for i in range(ss.PAGE_SIZE + 5)]
    c, system = client([*others, KANAGAWA])
    assert [r["license_no"] for r in c.lookup(PREF_14, None, "見本建設")] == ["012345"]
    assert [f["CMD"] for _, f in system.calls] == ["search", "selectPage", ""]
    # ページ数の上限を超えるときは「該当なし」と断定しない
    c2, _ = client([*others, KANAGAWA], max_pages=1)
    assert c2.lookup(PREF_14, None, "見本建設") is None
    assert c2.log[-1].state == "too_many"


def test_lookup_distinguishes_failure_from_no_hit():
    c, system = client([KANAGAWA])
    system.fail = ss.Unavailable("検索システムに接続できません: timed out")
    assert c.lookup(PREF_14, None, "見本建設") is None
    assert c.log[-1].state == "error" and "timed out" in c.last_error
    system.fail = None
    assert c.lookup(PREF_14, None, "存在しない商号") == []
    assert c.log[-1].state == "not_found" and c.last_error is None


def test_lookup_reports_a_changed_system():
    c, system = client([KANAGAWA])
    system.fail = ss.Changed("検索結果の見出しが変わりました")
    assert c.lookup(PREF_14, None, "見本建設") is None
    assert c.log[-1].state == "changed"


def test_lookup_rejects_too_many_identical_candidates():
    same = [(f"140000{i}0", "神奈川県", "見本建設（株）", "神奈川県川崎市1", ("R07年04月01日", "R12年03月31日"))
            for i in range(4)]
    c, _ = client(same, max_detail=2)
    assert c.lookup(PREF_14, None, "見本建設") is None
    assert c.log[-1].state == "too_many"


def test_lookup_skips_schemes_it_does_not_cover():
    c, system = client([KANAGAWA])
    assert c.lookup({"scheme_id": "pref-14.demolition", "authority_id": "pref-14", "trade": "demolition",
                     "publication_format": "full_list_excel", "source_url": "https://example.invalid/"},
                    None, "見本建設") is None
    assert c.log[-1].state == "unsupported" and system.calls == []


# --- check() との組み合わせ -----------------------------------------------

def test_check_uses_the_search_system_for_construction(conn):
    c, _ = client([KANAGAWA, MINISTER, SIMILAR])
    r = check(conn, address=KAWASAKI, trade="reform", name="見本建設株式会社", today=TODAY, search_client=c)
    assert r.verdict == AUTHORIZED
    assert r.message == "神奈川県の神奈川県知事 建設業許可あり（有効期限：令和12年3月31日）"
    assert [(e.license_no, e.fetched_at) for e in r.evidence] == [("012345", TODAY.isoformat())]
    assert r.evidence[0].source_url == ss.SEARCH_URL
    assert "見本　太郎" not in r.render()
    # 両方の制度を確かめられたので、見つからなければ「確認できず」
    assert check(conn, address=KAWASAKI, trade="reform", name="無許可工務店", today=TODAY,
                 search_client=c).verdict == NOT_FOUND


def test_check_reports_an_expired_license_from_the_search_system(conn):
    c, _ = client([EXPIRED_ONE])
    r = check(conn, address=KAWASAKI, trade="reform", name="見本解体", today=TODAY, search_client=c)
    assert r.verdict == EXPIRED and "令和7年3月31日に有効期限切れ" in r.message


def test_check_is_undetermined_when_the_search_system_cannot_be_asked(conn):
    c, system = client([KANAGAWA])
    system.fail = ss.Unavailable("検索システムに接続できません: timed out")
    r = check(conn, address=KAWASAKI, trade="reform", name="見本建設", today=TODAY, search_client=c)
    assert r.verdict == UNDETERMINED                        # 問い合わせ失敗を「名簿に無い」にしない
    assert any("問い合わせできませんでした" in w and "timed out" in w for w in r.warnings)
    assert all(s.completeness == "none" for s in r.schemes)
    assert "問い合わせできなかった" in r.message


def test_transport_posts_shift_jis_after_taking_a_session(monkeypatch):
    sent = []

    def fake_request(url, *, data=None, headers=None, timeout=60.0, opener=None):
        sent.append((url, data, opener))
        return list_page([KANAGAWA]).encode(ss.ENCODING)

    monkeypatch.setattr(ss._fetch, "request", fake_request)
    transport = ss.HttpTransport()
    body = transport(ss.SEARCH_URL, ss._fields(comNameKanjiOnly="見本建設"))
    assert "見本建設" in body
    # まず検索画面を GET（セッション）、次に POST。Cookie 付きの opener を使い回す
    assert [d is None for _, d, _ in sent] == [True, False]
    assert sent[0][2] is sent[1][2] is not None
    assert "comNameKanjiOnly=%8C%A9%96%7B%8C%9A%90%DD" in sent[1][1].decode("ascii")


def test_transport_turns_network_errors_into_unavailable(monkeypatch):
    def fail(url, **kwargs):
        raise OSError("timed out")

    monkeypatch.setattr(ss._fetch, "request", fail)
    with pytest.raises(ss.Unavailable) as e:
        ss.HttpTransport()(ss.SEARCH_URL, ss._fields())
    assert "timed out" in str(e.value)
