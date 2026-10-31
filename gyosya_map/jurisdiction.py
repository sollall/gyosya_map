"""管轄解決: 住所 → 市区町村コード → 業種ごとの担当機関。

担当機関は「既定ルール」（法律で決まる全国共通の構造）で決め、seed の jurisdiction には
既定と違うもの（上下水道局・企業団・県営水道など）だけを書く。優先順位は
  町丁目の上書き > その市区町村の例外 > 政令市の例外（行政区に効く） > 既定ルール
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from .normalize import normalize_address
from .trades import Trade

_PREFS = ("北海道", "東京都", "京都府", "大阪府") + tuple(
    p + "県" for p in "青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 神奈川 新潟 富山 石川 福井 山梨 長野 "
    "岐阜 静岡 愛知 三重 滋賀 兵庫 奈良 和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 "
    "大分 宮崎 鹿児島 沖縄".split())
PREF_CODES = {name: f"{i:02d}" for i, name in enumerate(
    ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県", "埼玉県",
     "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県",
     "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県",
     "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県",
     "宮崎県", "鹿児島県", "沖縄県"], 1)}


class AddressError(ValueError):
    pass


@dataclass
class Location:
    code: str
    municipality: str
    town: str                       # 町丁目（例: 小杉町3丁目）
    public_sewer: bool
    health_center_city: bool
    pref_code: str = ""
    city_code: str | None = None    # 行政区の場合の市のコード
    kind: str = ""
    rest: str = ""                  # 市区町村名より後ろ（正規化済み。例: 小杉町3丁目1-2）


@dataclass
class Resolution:
    location: Location
    # 担当機関ID（空 = 制度なし／未整備）
    authority_ids: list[str] = field(default_factory=list)
    out_of_area: bool = False       # 公共下水道の区域外など「制度なし（区域外）」
    note: str | None = None


# ---- 住所 → 市区町村 -------------------------------------------------------

class _AddressIndex:
    """正規化した市区町村名（郡あり・郡なし）→ 市区町村の行。名前とコードだけを使い、設定は都度読む。"""

    def __init__(self, conn: sqlite3.Connection):
        self.names: dict[str, list[sqlite3.Row]] = {}
        self.cities: dict[str, list[sqlite3.Row]] = {}
        for r in conn.execute("SELECT * FROM municipality"):
            target = self.cities if r["kind"] == "designated_city" else self.names
            forms = {normalize_address(r["name"])}
            if r["gun"]:
                forms.add(normalize_address(r["gun"] + r["name"]))
            for f in forms:
                target.setdefault(f, []).append(r)
        self.max_len = max((len(k) for k in self.names), default=0)

    def match(self, s: str, pref_code: str | None, table: dict) -> tuple[str, list[sqlite3.Row]] | None:
        for n in range(min(len(s), self.max_len), 0, -1):
            rows = [r for r in table.get(s[:n], []) if pref_code is None or r["pref_code"] == pref_code]
            if rows:
                return s[:n], rows
        return None


# 接続ごとに索引を持つ（sqlite3.Connection は弱参照できないので、接続そのものを一緒に持って取り違えを防ぐ）
_cache: list[tuple[sqlite3.Connection, _AddressIndex]] = []
_CACHE_SIZE = 4


def clear_cache() -> None:
    _cache.clear()


def _index(conn: sqlite3.Connection) -> _AddressIndex:
    for c, idx in _cache:
        if c is conn:
            return idx
    idx = _AddressIndex(conn)
    _cache.insert(0, (conn, idx))
    del _cache[_CACHE_SIZE:]
    return idx


def resolve_location(conn: sqlite3.Connection, address: str) -> Location:
    s = normalize_address(address)
    pref_code = None
    for p in _PREFS:
        if s.startswith(p):
            s, pref_code = s[len(p):], PREF_CODES[p]
            break
    idx = _index(conn)
    hit = idx.match(s, pref_code, idx.names)
    if hit is None:
        city = idx.match(s, pref_code, idx.cities)
        if city:
            raise AddressError(f"{city[1][0]['name']}の区まで入力してください: {address}")
        raise AddressError(f"収録されていない市区町村です: {address}")
    matched, rows = hit
    if len(rows) > 1:
        prefs = "・".join(f"{r['pref_name']}{r['gun'] or ''}{r['name']}" for r in rows)
        raise AddressError(f"同じ名前の市区町村があります（{prefs}）。都道府県名や郡名から入力してください: {address}")
    r = conn.execute("SELECT * FROM municipality WHERE code = ?", (rows[0]["code"],)).fetchone()
    # 「小杉町3丁目1-2」→「小杉町3丁目」、「小杉町1-2」→「小杉町」
    town = re.match(r"[^0-9\-]*?[0-9]+丁目|[^0-9\-]*", s[len(matched):]).group(0)
    return Location(code=r["code"], municipality=r["name"], town=town, public_sewer=bool(r["public_sewer"]),
                    health_center_city=bool(r["health_center_city"]), pref_code=r["pref_code"],
                    city_code=r["city_code"], kind=r["kind"], rest=s[len(matched):])


# ---- 市区町村 → 担当機関 ---------------------------------------------------

def _municipal_authority(loc: Location) -> str:
    """一般廃棄物・下水道などを担う市区町村。行政区なら市。"""
    return f"muni-{loc.city_code or loc.code}"


def default_authorities(loc: Location, scheme_trade: str) -> list[str]:
    """法律で決まる担当機関。水道は事業体が市区町村と一致しないため既定を持たない。"""
    pref = f"pref-{loc.pref_code}"
    muni = _municipal_authority(loc)
    rules = {
        "water": [],
        "sewer": [muni],                                    # 下水道法・各市条例
        "waste": [muni],                                    # 廃棄物処理法（一般廃棄物）
        # 浄化槽法: 清掃業は市町村、保守点検業は都道府県（保健所設置市は市）、工事業は都道府県
        "septic": [muni, muni if loc.health_center_city else pref, pref],
        "electrical": [pref, "meti"],                       # 電気工事業法（複数県は経産大臣）
        "demolition": [pref],                               # 建設リサイクル法
        "construction": [pref, "mlit"],                     # 建設業法（複数県は国交大臣）
    }
    return list(dict.fromkeys(rules.get(scheme_trade, [])))


def resolve(conn: sqlite3.Connection, location: Location, scheme_trade: str) -> Resolution:
    """1つの制度種類（scheme.trade）について担当機関を引く。"""
    overrides = conn.execute(
        "SELECT * FROM jurisdiction_override WHERE code IN (?, ?) AND trade = ? "
        "ORDER BY code = ? DESC, length(town_prefix) DESC",
        (location.code, location.city_code, scheme_trade, location.code)).fetchall()
    for o in overrides:
        if normalize_address(location.town).startswith(normalize_address(o["town_prefix"])):
            if o["authority_id"] is None:
                return Resolution(location, [], out_of_area=True, note=o["note"])
            return Resolution(location, [o["authority_id"]], note=o["note"])
    if scheme_trade == "sewer" and not location.public_sewer:
        return Resolution(location, [], out_of_area=True, note="公共下水道の区域外")
    for code in (location.code, location.city_code):
        if not code:
            continue
        ids = [r[0] for r in conn.execute(
            "SELECT authority_id FROM jurisdiction WHERE code = ? AND trade = ? ORDER BY authority_id",
            (code, scheme_trade))]
        if ids:
            return Resolution(location, ids)
    return Resolution(location, default_authorities(location, scheme_trade))


def resolve_trade(conn: sqlite3.Connection, location: Location, trade: Trade) -> dict[str, Resolution]:
    return {st: resolve(conn, location, st) for st in trade.scheme_trades}


def scheme_covers(location: Location, excluded_municipalities: str | None) -> bool:
    """その制度がこの所在地を対象にしているか（scheme.excluded_municipalities を見る）。

    担当機関は業種（trade）単位で決まるので、同じ機関の制度でも対象の範囲が違うことがある。
    例えば埼玉県浄化槽保守点検業者登録の対象は「さいたま市、川越市、川口市及び越谷市を除く」県内で、
    除かれた市の住所でこの制度を候補にすると、県の名簿に載っていないことを根拠に
    「未登録」と誤判定してしまう。行政区の住所は市のコードで除外できる。
    """
    excluded = set(json.loads(excluded_municipalities)) if excluded_municipalities else set()
    return not ({location.code, location.city_code} & excluded)
