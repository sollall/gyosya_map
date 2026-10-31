import datetime as dt

from gyosya_map.normalize import (licensee_key, normalize_address, normalize_license_no, normalize_name, parse_date,
                                  to_wareki)


def test_name_absorbs_corporate_form_width_space_and_itaiji():
    keys = {normalize_name(n) for n in [
        "株式会社 川崎設備", "川崎設備株式会社", "（株）川崎設備", "㈱川崎設備", "川﨑設備（株）", "川崎　設備 株式会社",
    ]}
    assert keys == {"川崎設備"}
    assert normalize_name("ＡＢＣ工業") == normalize_name("abc工業")


def test_license_no():
    assert normalize_license_no("第１２３号") == normalize_license_no("123")
    assert normalize_license_no("神奈川県知事（般－５）第０１２３号") == normalize_license_no("神奈川県知事(般-5)第123号")


def test_licensee_key_falls_back_to_name_and_address():
    """原本に許可番号の欄が無い制度では、番号を採番せず商号＋所在地でキーを作る。"""
    key = licensee_key(None, "株式会社サンプル回収", "さいたま市浦和区常盤6-4-4")
    # 商号の法人格・全角半角・空白と、所在地の全角数字は吸収する
    assert key == licensee_key("", "（株）サンプル　回収", "さいたま市浦和区常盤６－４－４")
    # 所在地が違えば別の業者
    assert key != licensee_key(None, "株式会社サンプル回収", "さいたま市大宮区桜木町1-7-5")
    # 「6-4-4」と「六丁目4-4」は normalize_address でも別のキーになる。番号で追えない制度では
    # 所在地の書き方が変わると別の行になるので、抽出データは原本の表記をそのまま書く
    assert key != licensee_key(None, "株式会社サンプル回収", "さいたま市浦和区常盤六丁目4-4")
    # 番号があればそれを使う
    assert licensee_key("第1号", "株式会社サンプル回収", "さいたま市浦和区常盤6-4-4") == normalize_license_no("1")


def test_licensee_key_without_a_number_can_never_be_hit_by_a_number():
    """番号で照合したときに「番号の記載が無い行」へ当たらないこと（キーの形が重ならない）。"""
    keys = {licensee_key(None, name, address)
            for name in ("見本設備", "1", "第1号", "") for address in ("川崎市中原区1", None, "")}
    numbers = {normalize_license_no(no) for no in
               ("1", "第1号", "番号の記載なし", "番号の記載なし 見本設備/", "見本設備/川崎市中原区1", "")}
    assert not (keys & numbers)


def test_parse_date_wareki_and_ad():
    assert parse_date("令和13年9月30日") == dt.date(2031, 9, 30)
    assert parse_date("R7.3.31") == dt.date(2025, 3, 31)
    assert parse_date("令和元年5月1日") == dt.date(2019, 5, 1)
    assert parse_date("平成31年4月30日") == dt.date(2019, 4, 30)
    assert parse_date("2026/10/30") == dt.date(2026, 10, 30)
    assert parse_date(dt.datetime(2026, 1, 2, 0, 0)) == dt.date(2026, 1, 2)
    assert parse_date("不明") is None
    assert to_wareki(dt.date(2031, 9, 30)) == "令和13年9月30日"


def test_address():
    assert normalize_address("川﨑市中原区小杉町三丁目１－２") == "川崎市中原区小杉町3丁目1-2"
