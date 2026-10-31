"""抽出データ（data_extracts/）の検証と取り込み。不正なデータを確実に弾くことを確かめる。"""

import hashlib
import json

import pytest
from conftest import TODAY
from fixtures import DROP, extract_row, make_extract, passed_review

from gyosya_map import db, extracts
from gyosya_map.cli import main

SCHEME = "pref-14.demolition"


def _problems(directory, **kw):
    with pytest.raises(extracts.ExtractError) as e:
        extracts.load(directory, **kw)
    return e.value.problems


def test_load_valid_extract(tmp_path):
    d = make_extract(tmp_path, [extract_row(), extract_row(license_no="第2号", name="有限会社みなと建設")])
    ex = extracts.load(d)
    assert ex.scheme_id == SCHEME and ex.kind == "full_list"
    assert not ex.reviewed                                  # review が null → 未検品
    assert [r["needs_review"] for r in ex.rows] == [1, 1]
    assert [r["license_no"] for r in ex.rows] == ["第1号", "第2号"]
    # 照合キーは抽出側に書かせない（インポータが normalize.py で作る）
    assert "license_no_key" not in ex.rows[0]
    assert any("原本" in w and "sha256" in w for w in ex.warnings)


def test_review_passed_marks_rows_reviewed(tmp_path):
    d = make_extract(tmp_path, [extract_row()], review=passed_review())
    ex = extracts.load(d)
    assert ex.reviewed and ex.rows[0]["needs_review"] == 0
    # 検品したが合わなかった（failed）行は検品済みにしない
    d2 = make_extract(tmp_path, [extract_row()], date="2026-11-06", review=passed_review(result="failed"))
    ex2 = extracts.load(d2)
    assert not ex2.reviewed and ex2.rows[0]["needs_review"] == 1


def test_missing_files(tmp_path):
    (tmp_path / SCHEME / "2026-11-05").mkdir(parents=True)
    problems = _problems(tmp_path / SCHEME / "2026-11-05")
    assert [p.split(" ")[0] for p in problems] == ["manifest.json", "rows.jsonl"]


def test_rejects_bad_rows(tmp_path):
    rows = [
        extract_row(),
        extract_row(license_no=DROP),                        # 必須項目が無い
        extract_row(license_no="第3号", name=None),           # 必須項目が null
        extract_row(license_no="第4号", address=""),          # 空文字は null にする
        extract_row(license_no="第5号", valid_until="令和13年3月31日"),   # 和暦は抽出側で変換する
        extract_row(license_no="第6号", representative=123),  # 文字列でない
        extract_row(license_no="第7号", status="有効"),        # 知らない値
        extract_row(license_no="第8号", 商号="サンプル"),       # 知らない項目
        extract_row(license_no="第9号", license_no_key="9"),  # 照合キーを書いている
        extract_row(license_no="0001号", name="重複した業者"),  # 1行目と同じ照合キー（1号）
        extract_row(license_no="第10号", valid_from="2026-04-01", valid_until="2026-03-31"),
    ]
    problems = _problems(make_extract(tmp_path, rows))
    assert len(problems) == 10, problems
    assert "rows.jsonl:2: license_no がありません（必須）" in problems
    assert any(p.startswith("rows.jsonl:3: name が null") for p in problems)
    assert any(p.startswith("rows.jsonl:4: address が空文字") for p in problems)
    assert any("rows.jsonl:5: valid_until は 2026-11-05 の形" in p for p in problems)
    assert any("rows.jsonl:6: representative は文字列" in p for p in problems)
    assert any("rows.jsonl:7: status は active / expired / revoked / unknown" in p for p in problems)
    assert any("rows.jsonl:8: 知らない項目があります: 商号" in p for p in problems)
    assert any("rows.jsonl:9: license_no_key は書かないでください" in p for p in problems)
    assert any("rows.jsonl:10: license_no が 1 行目と同じ業者になります" in p for p in problems)
    assert any("rows.jsonl:11: valid_until（2026-03-31）" in p for p in problems)


def test_rejects_broken_jsonl(tmp_path):
    d = make_extract(tmp_path, [], rows_text='{"license_no": "第1号"\n[1, 2]\n\n{"license_no": "第2号"}\n',
                     source_row_count=1)
    problems = _problems(d)
    assert any("rows.jsonl:1: JSONとして読めません" in p for p in problems)
    assert any("rows.jsonl:2: 1行に1件のJSONオブジェクト" in p for p in problems)
    # 空行は飛ばすが、行番号はファイルの行番号のまま出す
    assert any("rows.jsonl:4: name がありません" in p for p in problems)


def test_rejects_an_empty_extract(tmp_path):
    # 0件の全件名簿を取り込むと、登録済みの行が全部「名簿から消えた」扱いになる
    d = make_extract(tmp_path, [], source_row_count=0)
    assert any("rows.jsonl に行がありません" in p for p in _problems(d))


def test_rejects_bad_manifest(tmp_path):
    d = make_extract(tmp_path, [extract_row()], extracted_by=DROP, source_sha256="abc",
                     source_row_count=None, kind="pdf", fetched_at="2026/11/05", notes=5,
                     published_on="2026-11-05T00:00:00")
    problems = _problems(d)
    assert any("manifest.json: extracted_by がありません" in p for p in problems)
    assert any("manifest.json: source_sha256 は sha256 の16進64桁" in p for p in problems)
    assert any("manifest.json: source_row_count が null" in p for p in problems)
    assert any("manifest.json: kind は full_list / notice" in p for p in problems)
    assert any("manifest.json: fetched_at は 2026-11-05 の形" in p for p in problems)
    assert any("manifest.json: notes は文字列" in p for p in problems)
    assert any("manifest.json: published_on は 2026-11-05 の形" in p for p in problems)


def test_rejects_unknown_manifest_field_and_bad_review(tmp_path):
    d = make_extract(tmp_path, [extract_row()], source_sha="0" * 64,
                     review={"result": "ok", "sampled_rows": [1, 5]})
    problems = _problems(d)
    assert any("manifest.json: 知らない項目があります: source_sha" in p for p in problems)
    assert any("review: reviewed_by がありません" in p for p in problems)
    assert any("review: method がありません" in p for p in problems)
    assert any("review: reviewed_at がありません" in p for p in problems)
    assert any("review: result は passed / failed" in p for p in problems)
    assert any("review: sampled_rows は rows.jsonl の行番号（1〜1）" in p for p in problems)
    # 検品したのに目視した行が書いていない
    d2 = make_extract(tmp_path, [extract_row()], date="2026-11-06", review=passed_review(sampled_rows=[]))
    assert any("review: sampled_rows に目視で確かめた行" in p for p in _problems(d2))


def test_row_count_mismatch(tmp_path):
    d = make_extract(tmp_path, [extract_row()], source_row_count=3)
    assert any("原本の件数（source_row_count 3）と rows.jsonl の行数（1）が違います" in p for p in _problems(d))
    ex = extracts.load(d, allow_count_mismatch=True)
    assert any("source_row_count 3" in w for w in ex.warnings) and len(ex.rows) == 1


def test_source_sha256_is_checked_when_the_original_is_at_hand(tmp_path, monkeypatch):
    original = tmp_path / "meibo.xlsx"
    original.write_bytes(b"original bytes")
    sha = hashlib.sha256(original.read_bytes()).hexdigest()
    d = make_extract(tmp_path, [extract_row()], source_path=str(original), source_sha256=sha)
    assert extracts.load(d).warnings == []                  # 原本と一致 → 警告なし
    original.write_bytes(b"different bytes")
    assert any("sha256 が source_sha256 と違います" in p for p in _problems(d))


def test_layout_is_part_of_the_format(tmp_path):
    d = make_extract(tmp_path, [extract_row()], date="2026-11", scheme_id="pref-14.demolition")
    assert any("ディレクトリ名は抽出した日" in p for p in _problems(d))
    d2 = make_extract(tmp_path, [extract_row()], scheme_id="pref-14.demolition", date="2026-11-05")
    (tmp_path / "別の制度").mkdir()
    moved = (tmp_path / "別の制度" / "2026-11-05")
    d2.rename(moved)
    problems = _problems(moved, scheme_id="pref-14.septic")
    assert any("親ディレクトリ名（別の制度）" in p for p in problems)
    assert any("--scheme pref-14.septic" in p for p in problems)


def test_notice_rows_need_an_event(tmp_path):
    d = make_extract(tmp_path, [extract_row(status=DROP)], kind="notice", published_on="2026-10-01")
    assert any("rows.jsonl:1: event がありません" in p for p in _problems(d))
    d2 = make_extract(tmp_path, [extract_row(event="new")], date="2026-11-06", kind="notice", published_on=None)
    assert any("manifest.json: published_on が null" in p for p in _problems(d2))
    # 告示では status は使わない（黙って無視しない）
    d3 = make_extract(tmp_path, [extract_row(event="new")], date="2026-11-07", kind="notice",
                      published_on="2026-10-01")
    assert any("知らない項目があります: status" in p for p in _problems(d3))
    d4 = make_extract(tmp_path, [extract_row(status=DROP, event="new")], date="2026-11-08", kind="notice",
                      published_on="2026-10-01")
    assert extracts.load(d4).kind == "notice"


def test_import_extract_registers_document_and_rows(conn, tmp_path):
    d = make_extract(tmp_path, [extract_row(), extract_row(license_no="第2号", name="有限会社みなと建設",
                                                           address=None, valid_until=None)],
                     review=passed_review(sampled_rows=[1, 2]))
    stats = extracts.import_extract(conn, extracts.load(d), today=TODAY)
    assert stats == {"scheme": SCHEME, "kind": "full_list", "rows": 2, "reviewed": True, "new_document": True,
                     "inserted": 2, "updated": 0, "removed": 0}
    rows = conn.execute("SELECT * FROM licensee ORDER BY license_no").fetchall()
    assert [(r["license_no"], r["license_no_key"], r["name_key"], r["needs_review"]) for r in rows] == \
        [("第1号", "1", "サンプル解体", 0), ("第2号", "2", "みなと建設", 0)]
    # 有効期間満了日が無い行は制度の validity_years（5年）から補う
    assert [(r["valid_from"], r["valid_until"], r["status"]) for r in rows] == \
        [("2026-04-01", "2031-03-31", "active"), ("2026-04-01", "2031-03-31", "active")]
    doc = conn.execute("SELECT * FROM source_document").fetchone()
    assert (doc["path"], doc["sha256"], doc["kind"], doc["fetched_at"], doc["url"]) == \
        (f"data/raw/{SCHEME}/meibo-2026-11-05.xlsx", "0" * 64, "full_list", "2026-11-05",
         "https://example.invalid/meibo.xlsx")
    assert conn.execute("SELECT fetched_at FROM roster_fetch WHERE scheme_id = ?", (SCHEME,)).fetchone()[0] \
        == "2026-11-05"


def test_import_extract_honours_status_and_updates_existing_rows(conn, tmp_path):
    d = make_extract(tmp_path, [extract_row(), extract_row(license_no="第2号", name="取消された業者",
                                                           status="revoked")])
    extracts.import_extract(conn, extracts.load(d), today=TODAY)
    # 同じ番号は上書き更新、名簿から消えた行は unknown
    d2 = make_extract(tmp_path, [extract_row(name="株式会社サンプル解体（商号変更）")], date="2026-12-01",
                      source_sha256="1" * 64, review=passed_review())
    stats = extracts.import_extract(conn, extracts.load(d2), today=TODAY)
    assert (stats["inserted"], stats["updated"], stats["removed"]) == (0, 1, 0)
    rows = {r["license_no"]: r for r in conn.execute("SELECT * FROM licensee")}
    assert rows["第1号"]["name"] == "株式会社サンプル解体（商号変更）" and rows["第1号"]["needs_review"] == 0
    assert rows["第2号"]["status"] == "revoked"          # 名簿に書かれた状態は尊重する
    assert conn.execute("SELECT count(*) FROM source_document").fetchone()[0] == 2


def test_import_extract_applies_notice_events(conn, tmp_path):
    d = make_extract(tmp_path, [extract_row(status=DROP, event="new"),
                                extract_row(license_no="第9号", name="廃止された業者", status=DROP,
                                            event="revoke")],
                     kind="notice", published_on="2026-10-01", review=passed_review(sampled_rows=[1, 2]))
    stats = extracts.import_extract(conn, extracts.load(d), today=TODAY)
    assert (stats["kind"], stats["new"], stats["revoke"]) == ("notice", 1, 1)
    assert conn.execute("SELECT kind, published_on FROM source_document").fetchone()[:] == ("notice", "2026-10-01")
    assert dict(conn.execute("SELECT license_no, status FROM licensee ORDER BY license_no").fetchall()[1]) == \
        {"license_no": "第9号", "status": "revoked"}


def test_import_extract_needs_a_registered_scheme(conn, tmp_path):
    d = make_extract(tmp_path, [extract_row()], scheme_id="pref-99.unknown")
    with pytest.raises(KeyError, match="未登録の制度です"):
        extracts.import_extract(conn, extracts.load(d))
    assert conn.execute("SELECT count(*) FROM source_document").fetchone()[0] == 0


def test_cli_import_extract(tmp_path, capsys):
    dbp = str(tmp_path / "g.db")
    main(["--db", dbp, "init"])
    d = make_extract(tmp_path / "data_extracts", [extract_row()])
    capsys.readouterr()
    assert main(["--db", dbp, "import-extract", "--scheme", SCHEME, "--dir", str(d)]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out.split(": ", 1)[1])["inserted"] == 1
    assert "未検品 なので、この 1 件は地図に出ません" in err
    # 不正なデータは取り込まずに、どの行の何が不正かを出して落ちる
    bad = make_extract(tmp_path / "data_extracts", [extract_row(license_no=DROP)], date="2026-11-06")
    assert main(["--db", dbp, "import-extract", "--dir", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "rows.jsonl:1: license_no がありません" in err and "doc/extract-format.md" in err
    assert db.connect(dbp).execute("SELECT count(*) FROM licensee").fetchone()[0] == 1
    # --scheme が manifest と違うときも取り込まない
    assert main(["--db", dbp, "import-extract", "--scheme", "pref-14.septic-construction", "--dir", str(d)]) == 2
    assert main(["--db", dbp, "import-extract", "--dir", str(tmp_path / "ない")]) == 2
    assert "manifest.json がありません" in capsys.readouterr().err


def test_cli_import_extract_all(tmp_path, capsys):
    dbp = str(tmp_path / "g.db")
    root = tmp_path / "data_extracts"
    main(["--db", dbp, "init"])
    make_extract(root, [extract_row()], review=passed_review())
    make_extract(root, [extract_row(license_no="第5号", name="株式会社サンプル浄化槽", status=DROP, event="new")],
                 scheme_id="muni-141305.septic-maintenance", date="2026-11-06", kind="notice",
                 published_on="2026-11-01", source_sha256="2" * 64)
    assert extracts.find_all(root) == [root / "muni-141305.septic-maintenance" / "2026-11-06",
                                       root / SCHEME / "2026-11-05"]
    capsys.readouterr()
    assert main(["--db", dbp, "import-extract", "--all", "--extracts-dir", str(root)]) == 0
    assert capsys.readouterr().out.count("\n") == 2
    c = db.connect(dbp)
    assert c.execute("SELECT count(*) FROM licensee").fetchone()[0] == 2
    assert c.execute("SELECT count(*) FROM roster_fetch").fetchone()[0] == 2
    # 何も無いところで --all は「ありません」で止まる（0件で成功にしない）
    assert main(["--db", dbp, "import-extract", "--all", "--extracts-dir", str(tmp_path / "空")]) == 1
    # 1件でも形式に合っていなければ、先に検証で止まり、どれも取り込まない
    make_extract(root, [extract_row(license_no="第9号", valid_from="令和8年4月1日")], date="2026-11-07")
    capsys.readouterr()
    assert main(["--db", dbp, "import-extract", "--all", "--extracts-dir", str(root)]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "rows.jsonl:1: valid_from は 2026-11-05 の形" in err
    assert c.execute("SELECT count(*) FROM licensee WHERE license_no = '第9号'").fetchone()[0] == 0


def test_import_all_is_reproducible(tmp_path, capsys):
    """同じ抽出データからは、クリーンなDBでも同じDBと同じ地図HTMLができる。"""
    from fixtures import make_towns

    from gyosya_map import geo, mapview

    root = tmp_path / "data_extracts"
    make_extract(root, [extract_row(), extract_row(license_no="第2号", name="有限会社みなと建設",
                                                   address="愛川町中津1")],
                 review=passed_review(sampled_rows=[1, 2]))
    make_extract(root, [extract_row(license_no="第3号", name="株式会社あとから告示", status=DROP, event="new",
                                    address="川崎市中原区井田1")],
                 date="2026-11-20", kind="notice", published_on="2026-11-18", source_sha256="3" * 64,
                 review=passed_review())
    towns = make_towns(tmp_path / "latest.csv", [("14133", "川崎市中原区", "上小田中三丁目", "", 35.59, 139.64),
                                                 ("14133", "川崎市中原区", "井田", "", 35.57, 139.65),
                                                 ("14401", "愛甲郡愛川町", "中津", "", 35.51, 139.33)])

    def build(name):
        dbp = str(tmp_path / f"{name}.db")
        assert main(["--db", dbp, "init"]) == 0
        assert main(["--db", dbp, "import-extract", "--all", "--extracts-dir", str(root)]) == 0
        conn = db.connect(dbp)
        geo.load_towns(conn, towns)
        points, stats = mapview.collect(conn, today=TODAY)
        out = mapview.write_map(points, stats, tmp_path / f"{name}.html",
                                coverage_rows=mapview.coverage(conn, today=TODAY), today=TODAY)
        dump = "\n".join(l for l in conn.iterdump() if "town" not in l)
        conn.close()
        return dump, out.read_text("utf-8")

    first, second = build("one"), build("two")
    capsys.readouterr()
    assert first == second
    assert first[1].count('"license_no"') == 3        # 3件とも検品済みなので地図に出る


def test_map_leaves_out_unreviewed_rows(conn, tmp_path, capsys):
    from test_map import _towns

    from gyosya_map import mapview

    _towns(conn, tmp_path)
    extracts.import_extract(conn, extracts.load(
        make_extract(tmp_path, [extract_row()], review=passed_review())), today=TODAY)
    extracts.import_extract(conn, extracts.load(
        make_extract(tmp_path, [extract_row(license_no="第2号", name="未検品の業者")], date="2026-11-06",
                     scheme_id="muni-141305.septic-maintenance", source_sha256="4" * 64)), today=TODAY)
    points, stats = mapview.collect(conn, today=TODAY)
    assert [p["license_no"] for p in points] == ["第1号"] and stats.unreviewed == 1
    points, stats = mapview.collect(conn, today=TODAY, include_unreviewed=True)
    assert [(p["license_no"], p["needs_review"]) for p in points] == [("第1号", False), ("第2号", True)]
    assert stats.unreviewed == 1 and stats.include_unreviewed

    cov = {c["scheme_id"]: c for c in mapview.coverage(conn, today=TODAY)}
    assert (cov["muni-141305.septic-maintenance"]["active"], cov["muni-141305.septic-maintenance"]["unreviewed"]) \
        == (0, 1)
    with_unreviewed = {c["scheme_id"]: c for c in mapview.coverage(conn, today=TODAY, include_unreviewed=True)}
    assert with_unreviewed["muni-141305.septic-maintenance"]["active"] == 1
    html = mapview.write_map(points, stats, tmp_path / "m.html", today=TODAY).read_text("utf-8")
    assert "未検品" in html


def test_cli_map_reports_left_out_rows(tmp_path, capsys):
    from test_map import _towns

    dbp = str(tmp_path / "g.db")
    main(["--db", dbp, "init"])
    conn = db.connect(dbp)
    _towns(conn, tmp_path)
    conn.commit()
    conn.close()
    d = make_extract(tmp_path / "data_extracts", [extract_row()])
    main(["--db", dbp, "import-extract", "--dir", str(d)])
    capsys.readouterr()
    assert main(["--db", dbp, "map", "--out", str(tmp_path / "m.html")]) == 0
    out = capsys.readouterr().out
    assert "有効な登録業者 0 件" in out and "検品前の行 1 件を除きました" in out
    assert "（検品待ち 1 件を除く）" in out
    assert main(["--db", dbp, "map", "--include-unreviewed", "--out", str(tmp_path / "m2.html")]) == 0
    out = capsys.readouterr().out
    assert "有効な登録業者 1 件" in out and "検品前の行 1 件を含めました" in out


def test_check_says_when_a_row_is_unreviewed(conn, tmp_path):
    from gyosya_map.matcher import check

    extracts.import_extract(conn, extracts.load(make_extract(tmp_path, [extract_row()])), today=TODAY)
    r = check(conn, address="川崎市幸区1", trade="解体", name="サンプル解体", today=TODAY)
    assert r.verdict == "公認あり" and r.evidence[0].needs_review
    assert any("検品（目視確認）が済んでいない" in w for w in r.warnings)
    rendered = r.render()
    assert "・未検品）" in rendered
    assert "第1号" in rendered and "第第1号号" not in rendered   # 番号は原文のまま（「第…号」を重ねない）
