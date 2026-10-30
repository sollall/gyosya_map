"""管轄解決: 住所 → 市区町村コード → 業種ごとの担当機関。"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .normalize import normalize_address
from .trades import Trade

_PREFS = ("北海道", "東京都", "京都府", "大阪府") + tuple(
    p + "県" for p in "青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 神奈川 新潟 富山 石川 福井 山梨 長野 "
    "岐阜 静岡 愛知 三重 滋賀 兵庫 奈良 和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 "
    "大分 宮崎 鹿児島 沖縄".split())


class AddressError(ValueError):
    pass


@dataclass
class Location:
    code: str
    municipality: str
    town: str                       # 町丁目（例: 小杉町3丁目）
    public_sewer: bool
    health_center_city: bool


@dataclass
class Resolution:
    location: Location
    # 担当機関ID（空 = 制度なし／未整備）
    authority_ids: list[str] = field(default_factory=list)
    out_of_area: bool = False       # 公共下水道の区域外など「制度なし（区域外）」
    note: str | None = None


def resolve_location(conn: sqlite3.Connection, address: str) -> Location:
    s = normalize_address(address)
    for p in _PREFS:
        if s.startswith(p):
            s = s[len(p):]
            break
    rows = conn.execute(
        "SELECT * FROM municipality WHERE kind NOT IN ('prefecture', 'designated_city')").fetchall()
    best = None
    for r in rows:
        name = normalize_address(r["name"])
        if s.startswith(name) and (best is None or len(name) > len(normalize_address(best["name"]))):
            best = r
    if best is None:
        # 区を書かずに政令市名だけの住所は、区が決まらなければ解決できない
        city = conn.execute(
            "SELECT * FROM municipality WHERE kind = 'designated_city'").fetchall()
        for r in city:
            if s.startswith(normalize_address(r["name"])):
                raise AddressError(f"{r['name']}の区まで入力してください: {address}")
        raise AddressError(f"収録されていない市区町村です: {address}")
    town = s[len(normalize_address(best["name"])):]
    # 「小杉町3丁目1-2」→「小杉町3丁目」、「小杉町1-2」→「小杉町」
    town = re.match(r"[^0-9\-]*?[0-9]+丁目|[^0-9\-]*", town).group(0)
    return Location(code=best["code"], municipality=best["name"], town=town,
                    public_sewer=bool(best["public_sewer"]), health_center_city=bool(best["health_center_city"]))


def resolve(conn: sqlite3.Connection, location: Location, scheme_trade: str) -> Resolution:
    """1つの制度種類（scheme.trade）について担当機関を引く。町丁目の上書きを優先する。"""
    overrides = conn.execute(
        "SELECT * FROM jurisdiction_override WHERE code = ? AND trade = ? ORDER BY length(town_prefix) DESC",
        (location.code, scheme_trade)).fetchall()
    for o in overrides:
        if normalize_address(location.town).startswith(normalize_address(o["town_prefix"])):
            if o["authority_id"] is None:
                return Resolution(location, [], out_of_area=True, note=o["note"])
            return Resolution(location, [o["authority_id"]], note=o["note"])
    if scheme_trade == "sewer" and not location.public_sewer:
        return Resolution(location, [], out_of_area=True, note="公共下水道の区域外")
    ids = [r[0] for r in conn.execute(
        "SELECT authority_id FROM jurisdiction WHERE code = ? AND trade = ? ORDER BY authority_id",
        (location.code, scheme_trade))]
    return Resolution(location, ids)


def resolve_trade(conn: sqlite3.Connection, location: Location, trade: Trade) -> dict[str, Resolution]:
    return {st: resolve(conn, location, st) for st in trade.scheme_trades}
