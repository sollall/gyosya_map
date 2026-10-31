import csv
import json
import re

from conftest import TODAY

from gyosya_map import geo, mapview
from gyosya_map.ingest import import_full_list

HEADER = ["都道府県コード", "都道府県名", "都道府県名カナ", "都道府県名ローマ字", "市区町村コード", "市区町村名",
          "市区町村名カナ", "市区町村名ローマ字", "大字町丁目名", "大字町丁目名カナ", "大字町丁目名ローマ字",
          "小字・通称名", "緯度", "経度"]


def _towns(conn, tmp_path):
    rows = [("14133", "川崎市中原区", "上小田中一丁目", "", 35.58, 139.63),
            ("14133", "川崎市中原区", "上小田中三丁目", "", 35.59, 139.64),
            ("14133", "川崎市中原区", "井田", "字A", 35.56, 139.64),
            ("14133", "川崎市中原区", "井田", "字B", 35.58, 139.66),     # 小字が分かれている → 平均
            ("14401", "愛甲郡愛川町", "中津", "", 35.51, 139.33),
            ("14133", "川崎市中原区", "座標なし", "", "", "")]
    f = tmp_path / "latest.csv"
    with open(f, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for code, city, town, koaza, lat, lng in rows:
            w.writerow(["14", "神奈川県", "", "", code, city, "", "", town, "", "", koaza, lat, lng])
    assert geo.load_towns(conn, f) == 4
    return f


def test_geocode_precision(conn, tmp_path):
    _towns(conn, tmp_path)
    p = geo.geocode(conn, "神奈川県川崎市中原区上小田中三丁目1-1")
    assert (p.lat, p.lng, p.precision) == (35.59, 139.64, "town")
    assert geo.geocode(conn, "川崎市中原区上小田中3-1-1").precision == "town"       # 丁目を省いた書き方
    p = geo.geocode(conn, "川崎市中原区井田1000")
    assert (round(p.lat, 2), round(p.lng, 2), p.precision) == (35.57, 139.65, "town")
    p = geo.geocode(conn, "川崎市中原区上小田中")                                  # 丁目が無い → 町名の中心
    assert p.precision == "area" and round(p.lat, 3) == 35.585
    assert geo.geocode(conn, "川崎市中原区知らない町1").precision == "municipality"
    assert geo.geocode(conn, "愛川町中津1").precision == "town"
    assert geo.geocode(conn, "どこにもない市1") is None
    assert geo.geocode(conn, "藤沢市鵠沼1") is None                                # 座標が無い市


def test_map_html(conn, tmp_path):
    _towns(conn, tmp_path)
    conn.execute("INSERT INTO source_document (scheme_id, url, path, sha256, kind, fetched_at) "
                 "VALUES ('pref-14.demolition', 'https://example.invalid/m', 'x', 's', 'full_list', '2026-10-30')")
    import_full_list(conn, "pref-14.demolition", [
        {"license_no": "1", "name": "サンプル</script><b>解体", "address": "川崎市中原区上小田中3-1-1",
         "representative": "見本 太郎", "valid_until": "2030-03-31"},
        {"license_no": "2", "name": "期限切れ解体", "address": "愛川町中津1", "valid_until": "2026-10-01"},
        {"license_no": "3", "name": "住所なし解体"},
        {"license_no": "4", "name": "不明解体", "address": "どこにもない市1"},
    ], doc_id=1, today=TODAY)
    import_full_list(conn, "muni-141305.septic-maintenance", [
        {"license_no": "S1", "name": "サンプル浄化槽", "address": "川崎市中原区井田1"}], doc_id=1, today=TODAY)
    conn.execute("UPDATE licensee SET status = 'revoked' WHERE license_no = '4'")
    points, stats = mapview.collect(conn, today=TODAY)
    # 期限切れ（2）・取消（4）は出さない。業種の違う制度も一緒に出す
    assert [(p["trade"], p["license_no"]) for p in points] == [("demolition", "1"), ("septic", "S1")]
    assert (stats.plotted, stats.no_address, stats.not_found) == (2, 1, [])
    assert [p["license_no"] for p in mapview.collect(conn, trade="septic", today=TODAY)[0]] == ["S1"]
    assert mapview.collect(conn, trade="water", today=TODAY)[0] == []
    # 名簿をまだ入れていない制度も分かる
    cov = {c["scheme_id"]: c for c in mapview.coverage(conn, today=TODAY)}
    assert cov["pref-14.demolition"]["active"] == 2 and cov["kawasaki-suido.water"]["fetched_at"] is None

    out = mapview.write_map(points, stats, tmp_path / "out" / "map.html", coverage_rows=list(cov.values()))
    text = out.read_text("utf-8")
    assert "見本 太郎" not in text                     # 代表者名は出さない
    assert text.count("</script>") == 3               # 名前の </script> でスクリプトが切れない
    data = json.loads(re.search(r"const POINTS = (.*);\n", text).group(1).replace("<\\/", "</"))
    meta = json.loads(re.search(r"const META = (.*);\n", text).group(1))
    assert {"trade": "water", "license_name": "川崎市指定給水装置工事事業者", "active": 0, "fetched_at": None} \
        in meta["coverage"]
    assert data[0]["name"] == "サンプル</script><b>解体" and data[0]["source_url"] == "https://example.invalid/m"


def test_cli_map_requires_geo_update(tmp_path, capsys):
    import pytest

    from gyosya_map.cli import main
    dbp = str(tmp_path / "g.db")
    main(["--db", dbp, "init"])
    with pytest.raises(SystemExit, match="geo-update"):
        main(["--db", dbp, "map", "--out", str(tmp_path / "m.html")])
