import pytest

from gyosya_map.jurisdiction import AddressError, resolve, resolve_location


def test_resolves_ward_code_and_town(conn):
    loc = resolve_location(conn, "神奈川県川崎市中原区小杉町３丁目１－２")
    assert loc.code == "141330"
    assert loc.municipality == "川崎市中原区"
    assert loc.town == "小杉町3丁目"


def test_prefecture_can_be_omitted_and_itaiji(conn):
    assert resolve_location(conn, "川﨑市川崎区駅前本町1").code == "141313"
    assert resolve_location(conn, "横浜市保土ケ谷区星川1").code == "141062"


def test_designated_city_needs_ward(conn):
    with pytest.raises(AddressError, match="区まで"):
        resolve_location(conn, "川崎市小杉町1")
    with pytest.raises(AddressError, match="収録されていない"):
        resolve_location(conn, "東京都千代田区丸の内1")


def test_authorities_per_trade(conn):
    loc = resolve_location(conn, "川崎市麻生区万福寺1")
    assert resolve(conn, loc, "water").authority_ids == ["kawasaki-suido"]
    assert resolve(conn, loc, "electrical").authority_ids == ["meti", "pref-kanagawa"]
    assert resolve(conn, loc, "waste").authority_ids == ["city-kawasaki"]


def test_town_override_and_sewer_out_of_area(conn):
    conn.execute("INSERT INTO jurisdiction_override VALUES ('141372', '黒川', 'sewer', NULL, '公共下水道の区域外')")
    conn.execute("INSERT INTO jurisdiction_override VALUES ('141372', '万福寺', 'water', 'yokohama-suido', '給水区域の上書き')")
    r = resolve(conn, resolve_location(conn, "川崎市麻生区黒川100"), "sewer")
    assert r.out_of_area and r.authority_ids == []
    assert resolve(conn, resolve_location(conn, "川崎市麻生区万福寺2丁目"), "water").authority_ids == ["yokohama-suido"]
    conn.execute("UPDATE municipality SET public_sewer = 0 WHERE code = '141356'")
    assert resolve(conn, resolve_location(conn, "川崎市多摩区登戸1"), "sewer").out_of_area
