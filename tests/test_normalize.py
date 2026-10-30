import datetime as dt

from gyosya_map.normalize import normalize_address, normalize_license_no, normalize_name, parse_date, to_wareki


def test_name_absorbs_corporate_form_width_space_and_itaiji():
    keys = {normalize_name(n) for n in [
        "株式会社 川崎設備", "川崎設備株式会社", "（株）川崎設備", "㈱川崎設備", "川﨑設備（株）", "川崎　設備 株式会社",
    ]}
    assert keys == {"川崎設備"}
    assert normalize_name("ＡＢＣ工業") == normalize_name("abc工業")


def test_license_no():
    assert normalize_license_no("第１２３号") == normalize_license_no("123")
    assert normalize_license_no("神奈川県知事（般－５）第０１２３号") == normalize_license_no("神奈川県知事(般-5)第123号")


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
