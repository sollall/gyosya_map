import datetime as dt

import pytest
from conftest import TODAY
from fixtures import make_xlsx

from gyosya_map import db
from gyosya_map.ingest.download import (DownloadConfig, Link, decode_html, dest_name, disposition_filename,
                                        download_scheme, find_links, is_file_content, is_file_url, list_links)

PAGE = "https://www.pref.example.jp/docs/kaitai/index.html"
HTML = """<html><head><meta charset="Shift_JIS"></head><body>
<a href="#top">ページの先頭へ</a>
<a href="../../documents/123/meibo_r0810.xlsx">解体工事業者登録名簿（令和8年10月1日現在）（Excel：50KB）</a>
<a href="/documents/123/meibo_r0810.pdf">解体工事業者登録名簿（PDF）</a>
<a href="/documents/123/tebiki.pdf"><img alt="申請の手引き">申請の手引き</a>
<a href="/documents/123/meibo_r0810.xlsx">同じファイルへのリンク</a>
<a href="other.html">関連ページ</a>
</body></html>"""


def test_find_links_resolves_relative_and_filters():
    links = find_links(HTML, PAGE)
    assert [l.url for l in links] == ["https://www.pref.example.jp/documents/123/meibo_r0810.xlsx",
                                      "https://www.pref.example.jp/documents/123/meibo_r0810.pdf",
                                      "https://www.pref.example.jp/documents/123/tebiki.pdf"]
    assert links[0].text.startswith("解体工事業者登録名簿")
    only = find_links(HTML, PAGE, DownloadConfig(text_pattern="名簿", href_pattern=r"\.pdf$"))
    assert [l.url.rsplit("/", 1)[1] for l in only] == ["meibo_r0810.pdf"]


def test_decode_shift_jis_page():
    assert "解体工事業者" in decode_html(HTML.encode("cp932"))
    # meta の宣言と中身が食い違っていても読める
    assert "名簿" in decode_html(HTML.replace("Shift_JIS", "utf-8").encode("cp932"))


def test_config_validation():
    with pytest.raises(ValueError):
        DownloadConfig(pick="newest")


# --- 拡張子の無いURL（東京都の新CMS）--------------------------------------------------
# 掲載ページ https://www.waterworks.metro.tokyo.lg.jp/kurashi/shitei/ichiran の実物に合わせた形。
# ファイルは拡張子の無い /documents/d/<局>/<ID> で配られ、HEAD だけが形式を教える。
CMS_PAGE = "https://www.waterworks.metro.tokyo.lg.jp/kurashi/shitei/ichiran"
CMS_HTML = """<html><body>
<a href="/kurashi/shitei">指定給水装置工事事業者について</a>
<a href="/documents/d/waterworks/103013001-01-1" target="_blank">東京23区<img alt="PDF">（2.41MB）</a>
<a href="/documents/d/waterworks/103013001-01-2" target="_blank">多摩地区・他府県など<img alt="PDF">（2.53MB）</a>
<a href="/documents/d/waterworks/103013001-03">新規指定事業者一覧（令和８年９月指定分）<img alt="PDF">（269KB）</a>
<a href="/tetsuduki/suidoapp">東京都水道局アプリのダウンロード・登録方法</a>
</body></html>"""
PDF_HEADERS = {"content-type": "application/pdf",
               "content-disposition": 'inline; filename="103013001-01-1.pdf"'}
CMS_HEADERS = {
    "https://www.waterworks.metro.tokyo.lg.jp/documents/d/waterworks/103013001-01-1": PDF_HEADERS,
    "https://www.waterworks.metro.tokyo.lg.jp/documents/d/waterworks/103013001-01-2":
        {"content-type": "application/pdf", "content-disposition": 'inline; filename="103013001-01-2.pdf"'},
    "https://www.waterworks.metro.tokyo.lg.jp/documents/d/waterworks/103013001-03":
        {"content-type": "application/pdf"},
    "https://www.waterworks.metro.tokyo.lg.jp/tetsuduki/suidoapp": {"content-type": "text/html;charset=UTF-8"},
}


def _fake_prober(headers):
    """HEAD の代わり。実ネットワークには出ない。"""
    def prober(url):
        calls.append(url)
        if url not in headers:
            raise OSError(f"HTTP Error 404: {url}")
        return headers[url]
    calls = prober.calls = []
    return prober


def test_is_file_url_uses_extension_without_touching_the_network():
    never = _fake_prober({})
    assert is_file_url("https://www.pref.example.jp/documents/123/meibo.XLSX", never)
    assert is_file_url("https://www.pref.example.jp/docs/%E5%90%8D%E7%B0%BF.pdf", never)
    assert not is_file_url(CMS_PAGE + "/index.html", never)
    assert not is_file_url("mailto:x@example.jp", never)
    assert never.calls == []        # 拡張子で決まるURLには HEAD を打たない
    # prober が無ければ拡張子だけで見るので、拡張子の無いURLは従来どおり見つからない
    assert not is_file_url(CMS_PAGE + "/documents/d/waterworks/103013001-01-1")


def test_is_file_url_falls_back_to_head_content_type():
    url = "https://www.waterworks.metro.tokyo.lg.jp/documents/d/waterworks/103013001-01-1"
    prober = _fake_prober(CMS_HEADERS)
    assert is_file_url(url, prober)
    assert not is_file_url("https://www.waterworks.metro.tokyo.lg.jp/tetsuduki/suidoapp", prober)
    assert prober.calls == [url, "https://www.waterworks.metro.tokyo.lg.jp/tetsuduki/suidoapp"]


@pytest.mark.parametrize("headers, expected", [
    ({"content-type": "application/pdf"}, True),
    ({"content-type": "APPLICATION/PDF; charset=binary"}, True),
    ({"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}, True),
    ({"content-type": "application/vnd.ms-excel"}, True),
    ({"content-type": "text/csv; charset=Shift_JIS"}, True),
    ({"content-type": "text/html; charset=utf-8"}, False),
    ({}, False),
    # 形式を octet-stream で返すCMSは、添付指定かファイル名の拡張子で見る
    ({"content-type": "application/octet-stream", "content-disposition": "attachment"}, True),
    ({"content-type": "application/octet-stream"}, False),
    ({"content-type": "application/octet-stream",
      "content-disposition": 'attachment; filename="tebiki.html"'}, False),
    ({"content-type": "text/html", "content-disposition": 'attachment; filename="meibo.xlsx"'}, True),
    ({"content-type": "application/zip", "content-disposition": "attachment"}, False),
    ({"content-disposition": "attachment; filename*=UTF-8''%E5%90%8D%E7%B0%BF.csv"}, True),
])
def test_is_file_content(headers, expected):
    assert is_file_content(headers) is expected


def test_disposition_filename():
    assert disposition_filename({"content-disposition": 'inline; filename="103013001-01-1.pdf"'}) \
        == "103013001-01-1.pdf"
    assert disposition_filename({"content-disposition": "attachment; filename=meibo.xlsx; size=10"}) \
        == "meibo.xlsx"
    assert disposition_filename({"content-disposition": "attachment; filename*=UTF-8''%E5%90%8D%E7%B0%BF.csv"}) \
        == "名簿.csv"
    assert disposition_filename({}) == ""


def test_find_links_probes_only_download_like_links():
    prober = _fake_prober(CMS_HEADERS)
    links = find_links(CMS_HTML, CMS_PAGE, prober=prober)
    assert [l.url.rsplit("/", 1)[1] for l in links] == ["103013001-01-1", "103013001-01-2", "103013001-03"]
    # 形式・サイズの表示が無いリンク（/kurashi/shitei）には HEAD を打たない。
    # 「ダウンロード」の語があるページは打つが Content-Type で落ちる
    assert prober.calls == [l.url for l in links] + ["https://www.waterworks.metro.tokyo.lg.jp/tetsuduki/suidoapp"]
    # 絞り込みがあるときはその中だけを確かめる
    one = find_links(CMS_HTML, CMS_PAGE, DownloadConfig(href_pattern="103013001-01-1"),
                     prober=(p2 := _fake_prober(CMS_HEADERS)))
    assert [l.url for l in one] == p2.calls == [links[0].url]


def test_find_links_records_probe_failures_and_truncation():
    errors: list[str] = []
    assert find_links(CMS_HTML, CMS_PAGE, prober=_fake_prober({}), errors=errors) == []
    assert len(errors) == 4 and all("ヘッダを取れませんでした" in e for e in errors)
    errors.clear()
    links = find_links(CMS_HTML, CMS_PAGE, prober=_fake_prober(CMS_HEADERS), max_probes=1, errors=errors)
    assert len(links) == 1 and len(errors) == 1 and "打ち切りました" in errors[0]


def test_dest_name_adds_extension_for_extensionless_url(conn, tmp_path):
    db.load_seed_data(conn, {"schemes": [{"scheme_id": "tokyo-suido.water", "authority_id": "pref-13",
                                          "trade": "water", "license_name": "東京都指定給水装置工事事業者",
                                          "publication_format": "full_list_pdf", "source_url": CMS_PAGE,
                                          "download": {"href_pattern": "103013001-01-", "pick": "all"}}]})
    f = _fake_fetcher({CMS_PAGE: CMS_HTML.encode(),
                       **{u: b"%PDF-1.4" for u in list(CMS_HEADERS)[:2]}})
    got = download_scheme(conn, "tokyo-suido.water", tmp_path / "dl", fetcher=f, today=TODAY,
                          prober=_fake_prober(CMS_HEADERS))
    # Content-Disposition のファイル名から .pdf を補う（拡張子が無いとパーサーを選べない）
    assert [d.path.name for d in got] == ["103013001-01-1.pdf", "103013001-01-2.pdf"]
    assert all(d.path.read_bytes() == b"%PDF-1.4" for d in got)
    # Content-Disposition が無ければ Content-Type から補う
    assert dest_name(find_links(CMS_HTML, CMS_PAGE, prober=_fake_prober(CMS_HEADERS))[2]) \
        == "103013001-03.pdf"


@pytest.mark.parametrize("disposition, expected", [
    ('inline; filename="名簿 R0810 .pdf"', "名簿 R0810 .pdf"),           # 原本の名前はそのまま使う
    ('attachment; filename="../../etc/meibo.csv"', "meibo.csv"),        # ディレクトリは落とす
    ('attachment; filename="..\\\\..\\\\meibo.xls"', "meibo.xls"),
    ('attachment; filename=" meibo.xlsx "', "meibo.xlsx"),
    ('attachment; filename="tebiki.html"', "2026-10-14-094237-686.pdf"),  # 名簿の拡張子でなければ使わない
    ("attachment", "2026-10-14-094237-686.pdf"),
])
def test_dest_name_keeps_the_file_inside_the_destination(disposition, expected):
    url = "https://www.toshiseibi.metro.tokyo.lg.jp/documents/d/toshiseibi/2026-10-14-094237-686"
    assert dest_name(Link(url, "", {"content-type": "application/pdf", "content-disposition": disposition})) \
        == expected


def _fake_fetcher(files):
    calls = []

    def fetcher(url):
        calls.append(url)
        return files[url]
    fetcher.calls = calls
    return fetcher


def test_download_scheme_from_page(conn, tmp_path):
    xlsx = make_xlsx(tmp_path / "src.xlsx", [["第1号", "株式会社サンプル解体", "見本", "川崎市", "令和8年4月1日", None]])
    db.load_seed_data(conn, {"schemes": [{"scheme_id": "pref-14.demolition", "authority_id": "pref-14",
                                          "trade": "demolition", "license_name": "神奈川県解体工事業登録",
                                          "publication_format": "full_list_excel", "source_url": PAGE,
                                          "download": {"text_pattern": "名簿", "href_pattern": r"\.xlsx$"}}]})
    f = _fake_fetcher({PAGE: HTML.encode("cp932"),
                       "https://www.pref.example.jp/documents/123/meibo_r0810.xlsx": xlsx.read_bytes()})
    [d] = download_scheme(conn, "pref-14.demolition", tmp_path / "dl", fetcher=f, today=TODAY)
    assert d.path == tmp_path / "dl" / "pref-14.demolition" / "2026-10-30" / "meibo_r0810.xlsx"
    assert d.path.read_bytes() == xlsx.read_bytes() and d.page == PAGE
    assert len(list_links(conn, "pref-14.demolition", fetcher=f)) == 3       # 一覧は絞り込まない


def test_download_scheme_errors_and_direct_file(conn, tmp_path):
    db.load_seed_data(conn, {"schemes": [{"scheme_id": "pref-14.demolition", "authority_id": "pref-14",
                                          "trade": "demolition", "license_name": "x",
                                          "source_url": PAGE, "download": {"text_pattern": "存在しない"}},
                                         # 掲載ページが分からない制度（名簿が電話照会だけの制度など）
                                         {"scheme_id": "pref-14.unknown", "authority_id": "pref-14",
                                          "trade": "electrical", "license_name": "y",
                                          "publication_format": "phone_only"}]})
    with pytest.raises(ValueError, match="--list"):
        download_scheme(conn, "pref-14.demolition", tmp_path, fetcher=_fake_fetcher({PAGE: HTML.encode()}))
    # source_url がPDFそのもの（川崎市の告示）ならページを読まずに取る
    pdf_url = "https://www.city.kawasaki.jp/koho/cmsfiles/0000007/7422/7422.pdf"
    f = _fake_fetcher({pdf_url: b"%PDF-1.4"})
    [d] = download_scheme(conn, "kawasaki-suido.water", tmp_path, fetcher=f, today=TODAY)
    assert f.calls == [pdf_url] and d.path.name == "7422.pdf"
    with pytest.raises(ValueError, match="source_url"):
        download_scheme(conn, "pref-14.unknown", tmp_path, fetcher=f)


def test_xls_old_excel(tmp_path):
    import xlwt

    from gyosya_map.ingest import parse_file
    wb = xlwt.Workbook()
    ws = wb.add_sheet("名簿")
    for c, h in enumerate(["登録番号", "商号又は名称", "所在地", "登録年月日"]):
        ws.write(1, c, h)
    ws.write(2, 0, 101)
    ws.write(2, 1, "サンプル解体")
    ws.write(2, 2, "川崎市")
    ws.write(2, 3, dt.datetime(2026, 4, 1), xlwt.easyxf(num_format_str="yyyy/mm/dd"))
    f = tmp_path / "old.xls"
    wb.save(str(f))
    [r] = parse_file(f)
    assert r["license_no"] == "101" and r["name"] == "サンプル解体" and r["valid_from"] == dt.datetime(2026, 4, 1)


def test_cli_fetch_import(tmp_path, monkeypatch, capsys):
    from gyosya_map.cli import main
    from gyosya_map.ingest import download

    dbp, raw = str(tmp_path / "g.db"), str(tmp_path / "raw")
    main(["--db", dbp, "init"])
    xlsx = make_xlsx(tmp_path / "src.xlsx", [["第1号", "株式会社サンプル解体", "見本", "川崎市", "令和8年4月1日", None]])
    real_page = "https://www.pref.kanagawa.jp/docs/u2h/cnt/f531856/p449157.html"
    files = {real_page: HTML.encode("cp932"),
             "https://www.pref.kanagawa.jp/docs/u2h/documents/123/meibo_r0810.xlsx": xlsx.read_bytes()}
    monkeypatch.setattr(download._fetch, "fetch", lambda url: files[url])
    args = ["--db", dbp, "--raw-dir", raw, "fetch", "--scheme", "pref-14.demolition", "--import",
            "--dest", str(tmp_path / "dl")]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "ダウンロードしました" in out and '"inserted": 1' in out
    assert main(args) == 0
    assert "前回と同じファイル" in capsys.readouterr().out
    assert main(["--db", dbp, "fetch", "--scheme", "pref-14.demolition", "--list"]) == 0
    assert "meibo_r0810.pdf" in capsys.readouterr().out


def _notice_pdf(path, date_line="令和8年9月30日", separate=False):
    from fixtures import make_pdf
    header = ["指定番号", "氏名又は名称", "住所", "指定年月日"]
    footer = [(date_line, []), ("川崎市上下水道事業管理者　見本　太郎", [])] if separate else \
        [(f"{date_line}　川崎市上下水道事業管理者　見本　太郎", [])]
    return make_pdf(path, [
        ("川崎市上下水道局告示第99号　指定給水装置工事事業者を次のとおり指定した。", []),
        ("新たに指定した事業者", [header, ["501", "サンプル管工", "川崎市中原区井田1", "令和8年9月1日"]]),
        *footer,
    ])


def test_detect_notice_date(tmp_path):
    from gyosya_map.ingest.pdf import detect_notice_date
    # 表の指定年月日（9月1日）ではなく、告示者の前の日付を採る
    assert detect_notice_date(_notice_pdf(tmp_path / "a.pdf")) == dt.date(2026, 9, 30)
    assert detect_notice_date(_notice_pdf(tmp_path / "c.pdf", separate=True)) == dt.date(2026, 9, 30)
    assert detect_notice_date(_notice_pdf(tmp_path / "b.pdf", date_line="")) is None


def test_cli_fetch_imports_notice(tmp_path, monkeypatch, capsys):
    from gyosya_map.cli import main
    from gyosya_map.ingest import download

    dbp, raw = str(tmp_path / "g.db"), str(tmp_path / "raw")
    main(["--db", dbp, "init"])
    pdf = _notice_pdf(tmp_path / "n.pdf")
    url = "https://www.city.kawasaki.jp/koho/cmsfiles/0000007/7422/7422.pdf"
    monkeypatch.setattr(download._fetch, "fetch", lambda u: {url: pdf.read_bytes()}[u])
    assert main(["--db", dbp, "--raw-dir", raw, "fetch", "--scheme", "kawasaki-suido.water", "--import",
                 "--dest", str(tmp_path / "dl")]) == 0
    assert "告示日 2026-09-30" in capsys.readouterr().out
    row = db.connect(dbp).execute("SELECT valid_from, status FROM licensee WHERE license_no = '501'").fetchone()
    assert tuple(row) == ("2026-09-01", "active")
    # notice コマンドも --date を省略できる
    assert main(["--db", dbp, "--raw-dir", raw, "notice", "--scheme", "kawasaki-suido.sewer", "--file", str(pdf)]) == 0


def test_ambiguous_links_without_filter_are_refused(conn, tmp_path):
    db.load_seed_data(conn, {"schemes": [{"scheme_id": "muni-141305.septic-maintenance", "authority_id": "muni-141305",
                                          "trade": "septic", "license_name": "x", "source_url": PAGE}]})
    with pytest.raises(ValueError, match="どれが名簿か決められません") as e:
        download_scheme(conn, "muni-141305.septic-maintenance", tmp_path, fetcher=_fake_fetcher({PAGE: HTML.encode()}))
    assert "tebiki.pdf" in str(e.value)
