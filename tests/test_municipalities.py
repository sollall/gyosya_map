"""市区町村マスターが総務省の「都道府県コード及び市区町村コード」と合っているか。

出典が追いつかずに町村が落ちたことがある（利島村・湯前町が欠け、浜松市は再編前の7区のままだった）。
件数と都道府県ごとの数を固定して、作り直したときに気づけるようにする。
"""

import collections
import csv
from pathlib import Path

import gyosya_map
from gyosya_map.jurisdiction import resolve_location

MASTER = Path(gyosya_map.__file__).parent / "seed" / "municipalities.csv"

# 総務省「都道府県コード及び市区町村コード」（令和6年1月1日）の内訳。
# 北方領土の6村はコード表には載っているが入れていない（総務省の公表する市町村数1,718にも含まれない）
KINDS = {"city": 772, "designated_city": 20, "town": 743, "village": 183, "special_ward": 23, "ward": 171}
TOTAL = 1912

# 都道府県ごとの市区町村数（行政区を除く。東京都は特別区23を含む62）
PER_PREF = {
    "01": 179, "02": 40, "03": 33, "04": 35, "05": 25, "06": 35, "07": 59, "08": 44, "09": 25, "10": 35,
    "11": 63, "12": 54, "13": 62, "14": 33, "15": 30, "16": 15, "17": 19, "18": 17, "19": 27, "20": 77,
    "21": 42, "22": 35, "23": 54, "24": 29, "25": 19, "26": 26, "27": 43, "28": 41, "29": 39, "30": 30,
    "31": 19, "32": 19, "33": 27, "34": 23, "35": 19, "36": 24, "37": 17, "38": 20, "39": 34, "40": 60,
    "41": 20, "42": 21, "43": 45, "44": 18, "45": 26, "46": 43, "47": 41,
}


def _rows():
    return list(csv.DictReader(MASTER.read_text("utf-8").splitlines()))


def test_counts_match_the_official_register():
    rows = _rows()
    assert len(rows) == TOTAL
    assert collections.Counter(r["kind"] for r in rows) == KINDS
    # 市792・町743・村183・特別区23 で市区町村1,741。これに行政区171を足した数がマスターの件数
    assert sum(KINDS[k] for k in KINDS if k != "ward") == 1741
    assert KINDS["city"] + KINDS["designated_city"] == 792


def test_count_per_prefecture():
    per = collections.Counter(r["pref_code"] for r in _rows() if r["kind"] != "ward")
    assert dict(sorted(per.items())) == PER_PREF


def test_codes_are_unique_and_wards_point_at_their_city():
    rows = _rows()
    codes = {r["code"] for r in rows}
    assert len(codes) == len(rows)
    cities = {r["code"] for r in rows if r["kind"] == "designated_city"}
    wards = [r for r in rows if r["kind"] == "ward"]
    assert {r["city_code"] for r in wards} == cities                 # 政令市20市すべてに区がある
    assert all(r["name"].startswith("浜松市") for r in wards if r["city_code"] == "22130")
    assert all(not r["city_code"] for r in rows if r["kind"] != "ward")
    # 郡は町村だけに付く（住所の照合で「球磨郡湯前町」の形を引くのに使う）
    assert all(r["kind"] in ("town", "village") for r in rows if r["gun"])


def test_rows_that_were_missing_resolve_by_address(conn):
    assert resolve_location(conn, "東京都利島村248").code == "133621"
    assert resolve_location(conn, "熊本県球磨郡湯前町1988-1").code == "435066"
    assert resolve_location(conn, "湯前町1988-1").code == "435066"            # 郡は省ける
    # 浜松市は令和6年1月1日に7区から3区になった。旧区名では引けない
    assert resolve_location(conn, "静岡県浜松市中央区元城町103-2").code == "221384"
    assert resolve_location(conn, "浜松市浜名区貴平町193").code == "221392"


def test_official_notation_of_names_with_ke():
    names = {r["code"]: r["name"] for r in _rows()}
    # コード表の表記に合わせる（照合は normalize_address で ケ/ヶ を吸収するが、表示はこの表記で出る）
    assert names["08208"] == "龍ケ崎市"
    assert names["12224"] == "鎌ケ谷市"
    assert names["12229"] == "袖ケ浦市"
    assert names["02307"] == "外ヶ浜町"
    assert names["39405"] == "梼原町"


def test_northern_territories_are_not_included():
    assert not {r["code"] for r in _rows()} & {"01695", "01696", "01697", "01698", "01699", "01700"}
