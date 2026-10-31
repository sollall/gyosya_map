"""市区町村マスター gyosya_map/seed/municipalities.csv を作る。

どの市区町村が存在するか、その正式な表記、政令市とその行政区の対応は、総務省の
「都道府県コード及び市区町村コード」に従う。郡の名前だけはそのコード表に入っていないので
Geolonia 住所データから補う。

  curl -sSLO https://www.soumu.go.jp/main_content/000925835.xlsx
  curl -sSLO https://raw.githubusercontent.com/geolonia/japanese-addresses/master/data/latest.csv
  uv run python scripts/build_municipalities.py 000925835.xlsx latest.csv

出典:
  総務省「都道府県コード及び市区町村コード」（令和6年1月1日更新）https://www.soumu.go.jp/denshijiti/code.html
  Geolonia 住所データ（CC BY 4.0）https://github.com/geolonia/japanese-addresses

郡の出典（Geolonia）はコード表より遅れることがある（令和6年の浜松市の区の再編、利島村、湯前町が
入っていない）。存在する市区町村はコード表だけで決め、Geolonia に無い町村の郡は GUN に書く。
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import openpyxl

OUT = Path(__file__).resolve().parent.parent / "gyosya_map" / "seed" / "municipalities.csv"
_GUN = re.compile(r"^(.+?郡)(.+[町村])$")

# コード表には北方領土の6村も載っているが、自治体として機能しておらず名簿も照合する住所も無いので入れない。
# 総務省が公表する市町村数（1,718）にもこの6村は含まれない。
NORTHERN_TERRITORIES = {"01695", "01696", "01697", "01698", "01699", "01700"}

# 郡の出典に無い町村の郡（空文字は郡に属さない町村）。コード表が先に動いたときはここに足す
GUN = {
    "13362": "",        # 東京都利島村。伊豆諸島・小笠原諸島の町村は郡に属さない
    "43506": "球磨郡",  # 熊本県球磨郡湯前町
}


def read_register(src: str | Path) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str, str]]]:
    """コード表を読む。市区町村（コード → 都道府県名・名前）と行政区（コード → 都道府県名・名前・市コード）。

    シート名には更新日が入る（「R6.1.1現在の団体」）ので末尾で選ぶ。行政区のシートは政令市の行の後に
    その市の区の行が続く形で、市の行は市区町村のシートにも載っている。
    """
    wb = openpyxl.load_workbook(src, read_only=True)

    def rows(suffix: str):
        sheet = next((ws for ws in wb if ws.title.endswith(suffix)), None)
        if sheet is None:
            raise ValueError(f"{src} に「{suffix}」のシートがありません")
        for code, pref_name, name, *_ in sheet.iter_rows(min_row=2, values_only=True):
            if code and name:       # 都道府県そのものの行は市区町村名が空
                yield str(code)[:5], pref_name, name     # コード表は検査数字つきの6桁

    muni = {code: (pref_name, name) for code, pref_name, name in rows("現在の団体")
            if code not in NORTHERN_TERRITORIES}
    wards, city = {}, ""
    for code, pref_name, name in rows("政令指定都市"):
        if code in muni:
            city = code
        elif not city:
            raise ValueError(f"行政区 {code} {name} の政令市が分かりません")
        else:
            wards[code] = (pref_name, name, city)
    return muni, wards


def read_gun(src: str | Path) -> dict[str, str]:
    """住所データから コード → 郡名（郡に属さない市区町村は空）を作る。"""
    out: dict[str, str] = {}
    with open(src, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if (code := row["市区町村コード"]) not in out:
                m = _GUN.match(row["市区町村名"])
                out[code] = m.group(1) if m else ""
    return out


def build(register: str | Path, gun_src: str | Path) -> list[dict]:
    muni, wards = read_register(register)
    gun_of = {**read_gun(gun_src), **GUN}
    designated = {city for _, _, city in wards.values()}

    out, unknown_gun = [], []
    for code, (pref_name, name) in muni.items():
        if code in designated:
            kind = "designated_city"
        elif code.startswith("13") and name.endswith("区"):
            kind = "special_ward"
        elif name.endswith("市"):
            kind = "city"
        elif name.endswith(("町", "村")):
            kind = "town" if name.endswith("町") else "village"
        else:
            raise ValueError(f"種類が分からない市区町村: {code} {pref_name}{name}")
        gun = ""
        if kind in ("town", "village"):
            if code not in gun_of:
                unknown_gun.append(f"{code} {pref_name}{name}")
            gun = gun_of.get(code, "")
        out.append({"code": code, "pref_code": code[:2], "pref_name": pref_name, "name": name,
                    "gun": gun, "city_code": "", "kind": kind})
    if unknown_gun:
        # 郡が分からないまま出すと住所の照合（jurisdiction.py）で「球磨郡湯前町」の形が引けなくなる
        raise ValueError("郡が分からない町村があります。郡に属さないなら空文字で "
                         f"scripts/build_municipalities.py の GUN に足してください:\n  "
                         + "\n  ".join(unknown_gun))

    for code, (pref_name, name, city) in wards.items():
        out.append({"code": code, "pref_code": code[:2], "pref_name": pref_name, "name": name,
                    "gun": "", "city_code": city, "kind": "ward"})
    return sorted(out, key=lambda m: m["code"])


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"使い方: {sys.argv[0]} <都道府県コード及び市区町村コード.xlsx> <住所データ latest.csv>")
    rows = build(sys.argv[1], sys.argv[2])
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "pref_code", "pref_name", "name", "gun", "city_code", "kind"])
        w.writeheader()
        w.writerows(rows)
    print(f"{OUT}: {len(rows)} 行")


if __name__ == "__main__":
    main()
