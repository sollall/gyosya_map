import json

import pytest

from gyosya_map.jurisdiction import AddressError, resolve, resolve_location, scheme_covers


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
        resolve_location(conn, "神奈川県どこか市1")


def test_authorities_per_trade(conn):
    loc = resolve_location(conn, "川崎市麻生区万福寺1")
    assert resolve(conn, loc, "water").authority_ids == ["kawasaki-suido"]
    assert resolve(conn, loc, "electrical").authority_ids == ["pref-14", "meti"]
    assert resolve(conn, loc, "waste").authority_ids == ["muni-141305"]      # 行政区は市が担当


def test_town_override_and_sewer_out_of_area(conn):
    conn.execute("INSERT INTO jurisdiction_override VALUES ('141372', '黒川', 'sewer', NULL, '公共下水道の区域外')")
    conn.execute("INSERT INTO jurisdiction_override VALUES ('141372', '万福寺', 'water', 'yokohama-suido', '給水区域の上書き')")
    r = resolve(conn, resolve_location(conn, "川崎市麻生区黒川100"), "sewer")
    assert r.out_of_area and r.authority_ids == []
    assert resolve(conn, resolve_location(conn, "川崎市麻生区万福寺2丁目"), "water").authority_ids == ["yokohama-suido"]
    conn.execute("UPDATE municipality SET public_sewer = 0 WHERE code = '141356'")
    assert resolve(conn, resolve_location(conn, "川崎市多摩区登戸1"), "sewer").out_of_area


def test_same_name_needs_prefecture(conn):
    with pytest.raises(AddressError, match="同じ名前"):
        resolve_location(conn, "府中市宮西町1")
    assert resolve_location(conn, "東京都府中市宮西町1").code == "132063"
    assert resolve_location(conn, "広島県府中市府川町1").pref_code == "34"
    # 都道府県名があれば、他県の同名の町には当たらない
    with pytest.raises(AddressError, match="収録されていない"):
        resolve_location(conn, "神奈川県府中市1")


def test_gun_is_optional(conn):
    a = resolve_location(conn, "神奈川県愛甲郡愛川町中津1")
    b = resolve_location(conn, "愛川町中津1")
    assert a.code == b.code == "144011" and a.town == "中津"


def test_name_containing_gun_is_not_split(conn):
    assert resolve_location(conn, "福島県郡山市朝日1").municipality == "郡山市"
    assert resolve_location(conn, "奈良県大和郡山市北郡山町1").municipality == "大和郡山市"


def test_whole_country_is_loaded(conn):
    kinds = dict(conn.execute("SELECT kind, count(*) FROM municipality GROUP BY kind").fetchall())
    assert kinds["designated_city"] == 20 and kinds["special_ward"] == 23
    assert sum(kinds[k] for k in ("city", "town", "village", "special_ward", "designated_city")) > 1700
    assert resolve_location(conn, "東京都千代田区丸の内1").kind == "special_ward"
    assert resolve_location(conn, "北海道札幌市中央区北1条西2").city_code == "011002"


def test_default_rules_for_unconfigured_municipality(conn):
    # seed に何も書いていない町でも、法律どおりの担当機関が引ける
    loc = resolve_location(conn, "愛川町中津1")
    assert resolve(conn, loc, "electrical").authority_ids == ["pref-14", "meti"]
    assert resolve(conn, loc, "construction").authority_ids == ["pref-14", "mlit"]
    assert resolve(conn, loc, "waste").authority_ids == ["muni-144011"]
    assert resolve(conn, loc, "septic").authority_ids == ["muni-144011", "pref-14"]
    assert resolve(conn, loc, "water").authority_ids == []          # 水道は既定を持たない
    # 保健所設置市（藤沢市）は浄化槽の保守点検も市
    fujisawa = resolve_location(conn, "藤沢市鵠沼1")
    assert fujisawa.health_center_city
    assert resolve(conn, fujisawa, "septic").authority_ids == ["muni-142051", "pref-14"]
    # 特別区は区が一般廃棄物を担当する
    assert resolve(conn, resolve_location(conn, "東京都港区芝1"), "waste").authority_ids == ["muni-131032"]


def test_seed_excluded_municipalities_become_codes(conn):
    """seed には市区町村名を書き、コードに直して持つ（書き順によらず同じ値になる）。"""
    value = conn.execute("SELECT excluded_municipalities FROM scheme "
                         "WHERE scheme_id = 'pref-11.septic-maintenance'").fetchone()[0]
    # 埼玉県浄化槽保守点検業者登録の対象は「さいたま市、川越市、川口市及び越谷市を除く」県内
    assert json.loads(value) == ["111007", "112011", "112038", "112224"]
    # 東京都浄化槽保守点検業者登録は23区・八王子市・町田市が対象外
    tokyo = conn.execute("SELECT excluded_municipalities FROM scheme "
                         "WHERE scheme_id = 'pref-13.septic-maintenance'").fetchone()[0]
    assert len(json.loads(tokyo)) == 25 and "131016" in json.loads(tokyo)


def test_unknown_excluded_municipality_is_an_error(conn):
    """書き間違いを黙って無視すると「対象外のはずの市で候補に出る」に戻ってしまう。"""
    from gyosya_map import db

    def load(key):
        db.load_seed_data(conn, {"prefecture": "11", "schemes": [{
            "scheme_id": "pref-11.x", "authority_id": "pref-11", "trade": "septic",
            "license_name": "見本", "excluded_municipalities": [key]}]})

    with pytest.raises(ValueError, match="特定できません"):
        load("どこか市")
    with pytest.raises(ValueError, match="excluded_municipalities"):
        load("999999")


def test_scheme_covers_excludes_city_and_its_wards(conn):
    excluded = json.dumps(["111007", "112011"])           # さいたま市・川越市
    assert not scheme_covers(resolve_location(conn, "埼玉県川越市元町1"), excluded)
    # 行政区の住所は市のコードで外れる
    assert not scheme_covers(resolve_location(conn, "埼玉県さいたま市西区西大宮1"), excluded)
    assert scheme_covers(resolve_location(conn, "埼玉県所沢市並木1"), excluded)
    assert scheme_covers(resolve_location(conn, "埼玉県川越市元町1"), None)


def test_city_exception_applies_to_wards(conn):
    loc = resolve_location(conn, "川崎市川崎区駅前本町1")
    assert resolve(conn, loc, "sewer").authority_ids == ["kawasaki-suido"]
    assert resolve(conn, resolve_location(conn, "横浜市中区本町1"), "sewer").authority_ids == ["muni-141003"]
