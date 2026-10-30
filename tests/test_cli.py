from fixtures import make_xlsx

from gyosya_map.cli import main


def test_cli_end_to_end(tmp_path, capsys):
    dbp, raw = str(tmp_path / "g.db"), str(tmp_path / "raw")
    assert main(["--db", dbp, "init"]) == 0
    f = make_xlsx(tmp_path / "k.xlsx", [["第1号", "株式会社サンプル解体", "見本", "川崎市", "令和8年4月1日", None]])
    assert main(["--db", dbp, "--raw-dir", raw, "import", "--scheme", "pref-14.demolition", "--file", str(f)]) == 0
    capsys.readouterr()
    assert main(["--db", dbp, "check", "--address", "川崎市幸区1", "--trade", "解体", "--name", "サンプル解体"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("【公認あり】神奈川県の神奈川県解体工事業登録あり（有効期限：令和13年3月31日）")
    assert main(["--db", dbp, "check", "--address", "府中市1", "--trade", "water", "--name", "x"]) == 2


def test_cli_missing_file_is_a_clear_error(tmp_path):
    import pytest

    dbp = str(tmp_path / "g.db")
    main(["--db", dbp, "init"])
    with pytest.raises(SystemExit, match="ファイルが見つかりません"):
        main(["--db", dbp, "import", "--scheme", "pref-14.demolition", "--file", str(tmp_path / "無い.xlsx")])
    assert main(["--db", dbp, "import", "--scheme", "pref-99.none", "--file", str(tmp_path)]) == 2


def test_cli_refuses_to_import_zero_rows(tmp_path):
    import openpyxl
    import pytest

    dbp, raw = str(tmp_path / "g.db"), str(tmp_path / "raw")
    main(["--db", dbp, "init"])
    good = make_xlsx(tmp_path / "k.xlsx", [["第1号", "株式会社サンプル解体", "見本", "川崎市", "令和8年4月1日", None]])
    main(["--db", dbp, "--raw-dir", raw, "import", "--scheme", "pref-14.demolition", "--file", str(good)])
    wb = openpyxl.Workbook()
    wb.active.append(["見出しの無い表", "x"])
    bad = tmp_path / "bad.xlsx"
    wb.save(bad)
    with pytest.raises(SystemExit, match="1件も読み取れません"):
        main(["--db", dbp, "--raw-dir", raw, "import", "--scheme", "pref-14.demolition", "--file", str(bad)])
    from gyosya_map import db
    assert db.connect(dbp).execute("SELECT status FROM licensee").fetchone()[0] == "active"


def test_cli_file_url_downloads_and_rejects_web_page(tmp_path, monkeypatch, capsys):
    import pytest

    from gyosya_map.ingest import fetch

    dbp, raw = str(tmp_path / "g.db"), str(tmp_path / "raw")
    main(["--db", dbp, "init"])
    xlsx = make_xlsx(tmp_path / "k.xlsx", [["第1号", "株式会社サンプル解体", "見本", "川崎市", "令和8年4月1日", None]])
    page = tmp_path / "p449157.html"
    page.write_text("<!DOCTYPE html><html></html>")
    monkeypatch.setattr(fetch, "download", lambda url: xlsx if url.endswith(".xlsx") else page)
    with pytest.raises(SystemExit, match="Webページ"):
        main(["--db", dbp, "import", "--scheme", "pref-14.demolition",
              "--file", "https://www.pref.kanagawa.jp/docs/u2h/cnt/f531856/p449157.html"])
    assert main(["--db", dbp, "--raw-dir", raw, "import", "--scheme", "pref-14.demolition",
                 "--file", "https://example.invalid/meibo.xlsx"]) == 0
    from gyosya_map import db
    assert db.connect(dbp).execute("SELECT url FROM source_document").fetchone()[0] == "https://example.invalid/meibo.xlsx"
