"""Geolonia 住所データ（latest.csv）から市区町村マスター gyosya_map/seed/municipalities.csv を作る。

  curl -sSLO https://raw.githubusercontent.com/geolonia/japanese-addresses/master/data/latest.csv
  uv run python scripts/build_municipalities.py latest.csv

出典: Geolonia 住所データ（CC BY 4.0） https://github.com/geolonia/japanese-addresses
政令市そのものの行は元データに無いため、行政区から作る（市のコード = 最小の区コードの1の位を0にしたもの）。
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "gyosya_map" / "seed" / "municipalities.csv"
_WARD = re.compile(r"^(.+?市)(.+区)$")
_GUN = re.compile(r"^(.+?郡)(.+[町村])$")


def build(src: str | Path) -> list[dict]:
    seen: dict[str, dict] = {}
    with open(src, encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            code = row["市区町村コード"]
            if code in seen:
                continue
            seen[code] = {"code": code, "pref_code": row["都道府県コード"], "pref_name": row["都道府県名"],
                          "name": row["市区町村名"]}
    out: list[dict] = []
    cities: dict[tuple[str, str], list[str]] = {}
    for m in seen.values():
        name, gun, city_code, kind = m["name"], "", "", ""
        if (w := _WARD.match(name)) and m["pref_code"] != "13":
            kind = "ward"
            cities.setdefault((m["pref_code"], w.group(1)), []).append(m["code"])
        elif m["pref_code"] == "13" and name.endswith("区"):
            kind = "special_ward"
        elif (g := _GUN.match(name)):
            gun, name = g.group(1), g.group(2)
            kind = "town" if name.endswith("町") else "village"
        elif name.endswith("市"):
            kind = "city"
        elif name.endswith("町"):
            kind = "town"
        elif name.endswith("村"):
            kind = "village"
        else:
            raise ValueError(f"種類が分からない市区町村: {m}")
        out.append({**m, "name": name, "gun": gun, "city_code": city_code, "kind": kind})
    for (pref, city), wards in cities.items():
        city_code = min(wards)[:4] + "0"
        pref_name = next(m["pref_name"] for m in out if m["pref_code"] == pref)
        out.append({"code": city_code, "pref_code": pref, "pref_name": pref_name, "name": city, "gun": "",
                    "city_code": "", "kind": "designated_city"})
        for m in out:
            if m["code"] in wards:
                m["city_code"] = city_code
    return sorted(out, key=lambda m: m["code"])


def main() -> None:
    rows = build(sys.argv[1])
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "pref_code", "pref_name", "name", "gun", "city_code", "kind"])
        w.writeheader()
        w.writerows(rows)
    print(f"{OUT}: {len(rows)} 行")


if __name__ == "__main__":
    main()
