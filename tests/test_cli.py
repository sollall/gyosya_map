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


def test_cli_check_search_queries_the_search_system(tmp_path, monkeypatch, capsys):
    """--search で検索システムに問い合わせ、結果の内訳を stderr に出す。"""
    from test_search_system import KANAGAWA, FakeSystem

    from gyosya_map import search_system

    monkeypatch.setattr(search_system, "HttpTransport", lambda: FakeSystem([KANAGAWA]))
    dbp = str(tmp_path / "g.db")
    main(["--db", dbp, "init"])
    capsys.readouterr()
    assert main(["--db", dbp, "check", "--address", "川崎市幸区1", "--trade", "reform",
                 "--name", "株式会社見本建設"]) == 0
    assert capsys.readouterr().out.startswith("【判定不能】")     # --search なしでは問い合わせない
    assert main(["--db", dbp, "check", "--address", "川崎市幸区1", "--trade", "reform",
                 "--name", "株式会社見本建設", "--search"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("【公認あり】神奈川県の神奈川県知事 建設業許可あり")
    assert "pref-14.construction found（1 件" in captured.err
    assert "mlit.construction not_found" in captured.err


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


def test_init_upgrades_old_db_and_reset(tmp_path, capsys):
    import sqlite3

    dbp = str(tmp_path / "old.db")
    main(["--db", dbp, "init"])
    # 列が足りない古いDBを作る（download_config が無い版の scheme）
    c = sqlite3.connect(dbp)
    c.execute("ALTER TABLE scheme DROP COLUMN download_config")
    c.commit()
    c.close()
    capsys.readouterr()
    assert main(["--db", dbp, "init"]) == 0
    assert "scheme.download_config" in capsys.readouterr().out

    # 列の追加では直せない（NOT NULL の列が無い）ときは作り直しを案内する
    c = sqlite3.connect(dbp)
    c.executescript("DROP TABLE municipality; CREATE TABLE municipality (code TEXT PRIMARY KEY, name TEXT);")
    c.close()
    assert main(["--db", dbp, "init"]) == 2
    assert "--reset" in capsys.readouterr().err
    assert main(["--db", dbp, "init", "--reset"]) == 0


def test_init_asks_for_a_reset_when_constraints_changed(tmp_path, capsys):
    """CHECK 制約と NOT NULL の変更は列の追加では直せないので、作り直しを案内する。

    このタスクで licensee.license_no を NULL 可にし、publication_format に full_list_csv を足した。
    """
    import sqlite3

    dbp = str(tmp_path / "old.db")
    assert main(["--db", dbp, "init"]) == 0
    c = sqlite3.connect(dbp)
    licensee = c.execute("SELECT sql FROM sqlite_master WHERE name = 'licensee'").fetchone()[0]
    scheme = c.execute("SELECT sql FROM sqlite_master WHERE name = 'scheme'").fetchone()[0]
    old_licensee = licensee.replace("license_no     TEXT,", "license_no     TEXT NOT NULL,")
    old_scheme = scheme.replace("'full_list_csv', ", "")
    assert old_licensee != licensee and old_scheme != scheme     # 置換できていること
    c.executescript(f"DROP TABLE licensee; {old_licensee}; DROP TABLE scheme; {old_scheme};")
    c.close()
    capsys.readouterr()
    assert main(["--db", dbp, "init"]) == 2
    err = capsys.readouterr().err
    assert "licensee.license_no（NOT NULL のまま）" in err
    assert "scheme（CHECK 制約が今のスキーマと違います）" in err
    assert "init --reset" in err
    assert main(["--db", dbp, "init", "--reset"]) == 0
    assert "制度 27 件" in capsys.readouterr().out


def test_inspect_and_purge(tmp_path, capsys):
    from fixtures import make_pdf

    dbp, raw = str(tmp_path / "g.db"), str(tmp_path / "raw")
    main(["--db", dbp, "init"])
    f = make_xlsx(tmp_path / "k.xlsx", [["第1号", "株式会社サンプル解体", "見本", "川崎市", "令和8年4月1日", None]])
    pdf = make_pdf(tmp_path / "p.pdf", [("見出しの文", [["指定番号", "名称"], ["1", "サンプル"]])])
    capsys.readouterr()
    assert main(["--db", dbp, "inspect", "--file", str(f)]) == 0
    assert "登録番号" in capsys.readouterr().out
    assert main(["--db", dbp, "inspect", "--file", str(pdf)]) == 0
    out = capsys.readouterr().out
    assert "見出しの文" in out and "[表1]" in out and "指定番号 | 名称" in out

    main(["--db", dbp, "--raw-dir", raw, "import", "--scheme", "pref-14.demolition", "--file", str(f)])
    capsys.readouterr()
    assert main(["--db", dbp, "purge", "--scheme", "pref-14.demolition", "--yes"]) == 0
    assert "1 件" in capsys.readouterr().out
    from gyosya_map import db
    c = db.connect(dbp)
    assert c.execute("SELECT count(*) FROM licensee").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM source_document").fetchone()[0] == 0
    # 消したあとは同じファイルをもう一度取り込める
    assert main(["--db", dbp, "--raw-dir", raw, "import", "--scheme", "pref-14.demolition", "--file", str(f)]) == 0
