import datetime as dt

import pytest
from conftest import TODAY
from fixtures import make_xlsx

from gyosya_map import db
from gyosya_map.ingest.download import DownloadConfig, decode_html, download_scheme, find_links, list_links

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
                                          "source_url": PAGE, "download": {"text_pattern": "存在しない"}}]})
    with pytest.raises(ValueError, match="--list"):
        download_scheme(conn, "pref-14.demolition", tmp_path, fetcher=_fake_fetcher({PAGE: HTML.encode()}))
    # source_url がPDFそのもの（川崎市の告示）ならページを読まずに取る
    pdf_url = "https://www.city.kawasaki.jp/koho/cmsfiles/0000007/7422/7422.pdf"
    f = _fake_fetcher({pdf_url: b"%PDF-1.4"})
    [d] = download_scheme(conn, "kawasaki-suido.water", tmp_path, fetcher=f, today=TODAY)
    assert f.calls == [pdf_url] and d.path.name == "7422.pdf"
    with pytest.raises(ValueError, match="source_url"):
        download_scheme(conn, "meti.electrical", tmp_path, fetcher=f)


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
