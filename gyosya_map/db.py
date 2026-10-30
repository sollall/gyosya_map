"""SQLite への接続・初期化・シード投入。

シードは3層:
  seed/municipalities.csv       全国の市区町村マスター（Geolonia 住所データ, CC BY 4.0 から生成）
  seed/health_center_cities.csv 保健所設置市（中核市など。政令市・特別区はコードの種類から自動）
  seed/national.json            国の機関と制度
  seed/prefectures/*.json       都道府県ごとの機関・制度・既定ルールと違う管轄
都道府県と市区町村の担当機関（pref-XX / muni-XXXXXX）はマスターから自動で作る。
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from importlib import resources
from pathlib import Path

from . import jurisdiction

_SEED = resources.files("gyosya_map.seed")
_SCHEME_FIELDS = ("scheme_id", "authority_id", "trade", "license_name", "validity_years", "publication_format",
                  "source_url", "contact", "terms_of_use", "required_sub_scope", "verified_at")


def connect(path: str | Path = "gyosya.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(resources.files("gyosya_map").joinpath("schema.sql").read_text("utf-8"))


def check_digit(code5: str) -> str:
    """全国地方公共団体コードの検査数字を付けて6桁にする。"""
    total = sum(int(d) * w for d, w in zip(code5, (6, 5, 4, 3, 2)))
    return code5 + str((11 - total % 11) % 10)


def pref_authority(pref_code: str) -> str:
    return f"pref-{pref_code}"


def muni_authority(code6: str) -> str:
    return f"muni-{code6}"


def load_seed(conn: sqlite3.Connection) -> None:
    load_municipalities(conn)
    load_seed_data(conn, json.loads(_SEED.joinpath("national.json").read_text("utf-8")))
    for f in sorted(_SEED.joinpath("prefectures").iterdir(), key=lambda p: p.name):
        if f.name.endswith(".json"):
            load_seed_data(conn, json.loads(f.read_text("utf-8")))


def load_municipalities(conn: sqlite3.Connection, csv_text: str | None = None,
                        health_center_text: str | None = None) -> int:
    rows = list(csv.DictReader(io.StringIO(csv_text or _SEED.joinpath("municipalities.csv").read_text("utf-8"))))
    hc = {(r["pref_name"], r["name"]) for r in csv.DictReader(io.StringIO(
        health_center_text or _SEED.joinpath("health_center_cities.csv").read_text("utf-8")))}
    with conn:
        for r in rows:
            code = check_digit(r["code"])
            city_code = check_digit(r["city_code"]) if r["city_code"] else None
            is_hc = r["kind"] in ("designated_city", "ward", "special_ward") or (r["pref_name"], r["name"]) in hc
            conn.execute(
                "INSERT OR IGNORE INTO authority (authority_id, name, level) VALUES (?, ?, 'prefecture')",
                (pref_authority(r["pref_code"]), r["pref_name"]))
            # 既存の行の設定（public_sewer など）は残して、マスター由来の項目だけ更新する
            conn.execute(
                """INSERT INTO municipality (code, pref_code, pref_name, name, gun, city_code, kind, health_center_city)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(code) DO UPDATE SET pref_code = excluded.pref_code, pref_name = excluded.pref_name,
                       name = excluded.name, gun = excluded.gun, city_code = excluded.city_code,
                       kind = excluded.kind, health_center_city = excluded.health_center_city""",
                (code, r["pref_code"], r["pref_name"], r["name"], r["gun"] or None, city_code, r["kind"], int(is_hc)))
            if r["kind"] != "ward":
                conn.execute(
                    """INSERT INTO authority (authority_id, name, level, parent_id) VALUES (?, ?, 'municipality', ?)
                       ON CONFLICT(authority_id) DO UPDATE SET name = excluded.name, parent_id = excluded.parent_id""",
                    (muni_authority(code), r["name"], pref_authority(r["pref_code"])))
    jurisdiction.clear_cache()
    return len(rows)


def _municipality_code(conn: sqlite3.Connection, key: str, pref_code: str | None) -> str:
    """seed の市区町村キー（6桁/5桁コード、または都道府県内の名前）をコードにする。"""
    if key.isdigit():
        return key if len(key) == 6 else check_digit(key)
    rows = conn.execute("SELECT code FROM municipality WHERE name = ? AND (? IS NULL OR pref_code = ?)",
                        (key, pref_code, pref_code)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"市区町村を特定できません: {key}（{len(rows)} 件）")
    return rows[0][0]


def load_seed_data(conn: sqlite3.Connection, data: dict) -> None:
    pref = data.get("prefecture")
    with conn:
        for a in data.get("authorities", []):
            conn.execute(
                """INSERT INTO authority (authority_id, name, level, parent_id) VALUES (?, ?, ?, ?)
                   ON CONFLICT(authority_id) DO UPDATE SET name = excluded.name, level = excluded.level,
                       parent_id = excluded.parent_id""",
                (a["authority_id"], a["name"], a["level"], a.get("parent_id")))
        for s in data.get("schemes", []):
            values = {k: s.get(k) for k in _SCHEME_FIELDS}
            values["parser_config"] = json.dumps(s["parser"], ensure_ascii=False) if s.get("parser") else None
            conn.execute(
                f"""INSERT INTO scheme ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})
                    ON CONFLICT(scheme_id) DO UPDATE SET
                    {', '.join(f'{k} = excluded.{k}' for k in values if k != 'scheme_id')}""", values)
            conn.execute("DELETE FROM scheme_note WHERE scheme_id = ?", (s["scheme_id"],))
            for n in s.get("notes", []):
                conn.execute("INSERT INTO scheme_note (scheme_id, severity, body) VALUES (?, ?, ?)",
                             (s["scheme_id"], n["severity"], n["body"]))
            if s.get("source_url"):
                conn.execute("INSERT OR IGNORE INTO page_watch (url, scheme_id) VALUES (?, ?)",
                             (s["source_url"], s["scheme_id"]))
        for key, m in data.get("municipalities", {}).items():
            code = _municipality_code(conn, key, pref)
            for col in ("public_sewer", "health_center_city"):
                if col in m:
                    conn.execute(f"UPDATE municipality SET {col} = ? WHERE code = ?", (int(m[col]), code))
            for trade, authorities in m.get("jurisdiction", {}).items():
                conn.execute("DELETE FROM jurisdiction WHERE code = ? AND trade = ?", (code, trade))
                for authority_id in authorities:
                    conn.execute("INSERT INTO jurisdiction (code, trade, authority_id) VALUES (?, ?, ?)",
                                 (code, trade, authority_id))
        for o in data.get("overrides", []):
            conn.execute(
                """INSERT OR REPLACE INTO jurisdiction_override (code, town_prefix, trade, authority_id, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (_municipality_code(conn, o["municipality"], pref), o["town_prefix"], o["trade"],
                 o.get("authority_id"), o.get("note")))
    jurisdiction.clear_cache()
