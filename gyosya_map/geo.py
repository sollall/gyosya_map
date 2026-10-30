"""住所 → 緯度経度（町丁目の代表点）。地図の表示用で、番地までの精度はない。

座標は Geolonia 住所データ（CC BY 4.0, https://github.com/geolonia/japanese-addresses）の
町丁目ごとの代表点。`geo-update` で取り込む。
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .db import check_digit
from .jurisdiction import AddressError, resolve_location
from .normalize import normalize_address

LATEST_CSV = "https://raw.githubusercontent.com/geolonia/japanese-addresses/master/data/latest.csv"


@dataclass
class Point:
    lat: float
    lng: float
    precision: str      # town（町丁目）/ area（町名まで）/ municipality（市区町村の中心）
    municipality: str


def load_towns(conn: sqlite3.Connection, csv_path: str | Path) -> int:
    """latest.csv から町丁目の代表点を取り込む。小字が分かれている町丁目は平均をとる。"""
    acc: dict[tuple[str, str], list[float]] = {}
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            lat, lng = row["緯度"], row["経度"]
            if not lat or not lng:
                continue
            key = (row["市区町村コード"], row["大字町丁目名"])
            a = acc.setdefault(key, [0.0, 0.0, 0])
            a[0] += float(lat)
            a[1] += float(lng)
            a[2] += 1
    with conn:
        conn.execute("DELETE FROM town")
        conn.executemany(
            "INSERT INTO town (code, name, name_key, lat, lng) VALUES (?, ?, ?, ?, ?)",
            ((check_digit(code), name, normalize_address(name), a[0] / a[2], a[1] / a[2])
             for (code, name), a in acc.items()))
    return len(acc)


def has_towns(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM town LIMIT 1").fetchone() is not None


def _avg(rows) -> tuple[float, float] | None:
    rows = list(rows)
    if not rows:
        return None
    return sum(r[0] for r in rows) / len(rows), sum(r[1] for r in rows) / len(rows)


def geocode(conn: sqlite3.Connection, address: str) -> Point | None:
    try:
        loc = resolve_location(conn, address)
    except AddressError:
        return None
    towns = conn.execute("SELECT name_key, lat, lng FROM town WHERE code = ?", (loc.code,)).fetchall()
    if not towns:
        return None
    rest = loc.rest
    # 1. 町丁目名が住所の先頭に一致（長い名前を優先）
    best = max((t for t in towns if rest.startswith(t["name_key"])), key=lambda t: len(t["name_key"]), default=None)
    if best:
        return Point(best["lat"], best["lng"], "town", loc.municipality)
    # 2. 「上小田中3-1-1」のように丁目を省いた書き方
    m = re.match(r"([^0-9\-]+)([0-9]+)(?:-|番|$)", rest)
    if m:
        key = f"{m.group(1)}{m.group(2)}丁目"
        for t in towns:
            if t["name_key"] == key:
                return Point(t["lat"], t["lng"], "town", loc.municipality)
    # 3. 町名だけ一致（「小杉町」→「小杉町1丁目」「小杉町2丁目」…の平均）
    head = re.match(r"[^0-9\-]+", rest)
    if head:
        name = head.group(0)
        area = _avg((t["lat"], t["lng"]) for t in towns if t["name_key"].startswith(name))
        if area:
            return Point(*area, "area", loc.municipality)
    center = _avg((t["lat"], t["lng"]) for t in towns)
    return Point(*center, "municipality", loc.municipality)
