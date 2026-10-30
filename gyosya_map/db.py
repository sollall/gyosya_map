"""SQLite への接続・初期化・シード投入。"""

from __future__ import annotations

import json
import sqlite3
from importlib import resources
from pathlib import Path


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


def load_seed(conn: sqlite3.Connection, name: str = "kanagawa.json") -> None:
    data = json.loads(resources.files("gyosya_map.seed").joinpath(name).read_text("utf-8"))
    load_seed_data(conn, data)


def load_seed_data(conn: sqlite3.Connection, data: dict) -> None:
    with conn:
        for a in data["authorities"]:
            conn.execute(
                "INSERT OR REPLACE INTO authority (authority_id, name, level, parent_id) VALUES (?, ?, ?, ?)",
                (a["authority_id"], a["name"], a["level"], a.get("parent_id")))
        for s in data["schemes"]:
            conn.execute(
                """INSERT OR REPLACE INTO scheme (scheme_id, authority_id, trade, license_name, validity_years,
                   publication_format, source_url, contact, terms_of_use, required_sub_scope, verified_at)
                   VALUES (:scheme_id, :authority_id, :trade, :license_name, :validity_years,
                   :publication_format, :source_url, :contact, :terms_of_use, :required_sub_scope, :verified_at)""",
                {k: s.get(k) for k in ("scheme_id", "authority_id", "trade", "license_name", "validity_years",
                                       "publication_format", "source_url", "contact", "terms_of_use",
                                       "required_sub_scope", "verified_at")})
            conn.execute("DELETE FROM scheme_note WHERE scheme_id = ?", (s["scheme_id"],))
            for n in s.get("notes", []):
                conn.execute("INSERT INTO scheme_note (scheme_id, severity, body) VALUES (?, ?, ?)",
                             (s["scheme_id"], n["severity"], n["body"]))
            if s.get("source_url"):
                conn.execute("INSERT OR IGNORE INTO page_watch (url, scheme_id) VALUES (?, ?)",
                             (s["source_url"], s["scheme_id"]))
        for m in data["municipalities"]:
            code = m["code"] if len(m["code"]) == 6 else check_digit(m["code"])
            conn.execute(
                """INSERT OR REPLACE INTO municipality
                   (code, pref_code, name, city_name, kind, health_center_city, public_sewer)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (code, code[:2], m["name"], m.get("city_name"), m["kind"],
                 int(m.get("health_center_city", False)), int(m.get("public_sewer", True))))
            conn.execute("DELETE FROM jurisdiction WHERE code = ?", (code,))
            for trade, authorities in m.get("jurisdiction", {}).items():
                for authority_id in authorities:
                    conn.execute("INSERT INTO jurisdiction (code, trade, authority_id) VALUES (?, ?, ?)",
                                 (code, trade, authority_id))
        for o in data.get("overrides", []):
            code = o["code"] if len(o["code"]) == 6 else check_digit(o["code"])
            conn.execute(
                """INSERT OR REPLACE INTO jurisdiction_override (code, town_prefix, trade, authority_id, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (code, o["town_prefix"], o["trade"], o.get("authority_id"), o.get("note")))
