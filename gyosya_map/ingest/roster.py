"""名簿を licensee に反映する。原本を保存し、各行から出典文書と取得日をたどれるようにする。"""

from __future__ import annotations

import datetime as dt
import hashlib
import shutil
import sqlite3
from pathlib import Path

from ..normalize import licensee_key, normalize_license_no, normalize_name, parse_date


def _today() -> dt.date:
    return dt.date.today()


def _find_document(conn: sqlite3.Connection, scheme_id: str, sha256: str) -> int | None:
    row = conn.execute("SELECT doc_id FROM source_document WHERE scheme_id = ? AND sha256 = ?",
                       (scheme_id, sha256)).fetchone()
    return row[0] if row else None


def register_document(conn: sqlite3.Connection, scheme_id: str, *, path: str, sha256: str, kind: str,
                      url: str | None = None, published_on: dt.date | None = None,
                      fetched_at: dt.date | None = None) -> tuple[int, bool]:
    """原本の記録だけを source_document に入れる。(doc_id, 新規か) を返す。

    原本ファイルの中身は見ない（抽出データの取り込みでは、原本が手元にないこともある）。
    """
    doc_id = _find_document(conn, scheme_id, sha256)
    if doc_id is not None:
        return doc_id, False
    cur = conn.execute(
        """INSERT INTO source_document (scheme_id, url, path, sha256, kind, published_on, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (scheme_id, url, path, sha256, kind, published_on and published_on.isoformat(),
         (fetched_at or _today()).isoformat()))
    return cur.lastrowid, True


def store_document(conn: sqlite3.Connection, scheme_id: str, file_path: str | Path, *, kind: str,
                   url: str | None = None, published_on: dt.date | None = None,
                   fetched_at: dt.date | None = None, raw_dir: str | Path = "data/raw") -> tuple[int, bool]:
    """原本を raw_dir に保存して source_document に記録する。(doc_id, 新規か) を返す。"""
    file_path = Path(file_path)
    sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
    if (doc_id := _find_document(conn, scheme_id, sha)) is not None:
        return doc_id, False
    dest = Path(raw_dir) / scheme_id / f"{sha}{file_path.suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if file_path.resolve() != dest.resolve():
        shutil.copyfile(file_path, dest)
    return register_document(conn, scheme_id, path=str(dest), sha256=sha, kind=kind, url=url,
                             published_on=published_on, fetched_at=fetched_at)


def _validity_years(conn: sqlite3.Connection, scheme_id: str) -> int | None:
    row = conn.execute("SELECT validity_years FROM scheme WHERE scheme_id = ?", (scheme_id,)).fetchone()
    if row is None:
        raise KeyError(f"未登録の制度です: {scheme_id}")
    return row[0]


def _until(valid_from: dt.date | None, valid_until: dt.date | None, years: int | None) -> dt.date | None:
    if valid_until or not (valid_from and years):
        return valid_until
    try:
        end = valid_from.replace(year=valid_from.year + years)
    except ValueError:  # 2月29日
        end = valid_from.replace(year=valid_from.year + years, day=28)
    return end - dt.timedelta(days=1)


def _status(valid_until: dt.date | None, today: dt.date, given: str | None = None) -> str:
    """名簿に書かれた状態（given）を尊重する。有効なはずの行だけ、有効期限で見直す。"""
    if given in ("expired", "revoked", "unknown"):
        return given
    return "expired" if valid_until and valid_until < today else "active"


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def _touch_roster(conn, scheme_id, doc_id, fetched_at):
    conn.execute(
        """INSERT INTO roster_fetch (scheme_id, fetched_at, doc_id) VALUES (?, ?, ?)
           ON CONFLICT(scheme_id) DO UPDATE SET fetched_at = excluded.fetched_at, doc_id = excluded.doc_id""",
        (scheme_id, fetched_at.isoformat(), doc_id))


def import_full_list(conn: sqlite3.Connection, scheme_id: str, records: list[dict], *, doc_id: int,
                     fetched_at: dt.date | None = None, today: dt.date | None = None) -> dict:
    """全件名簿で置き換える。新しい名簿に無い行は有効でないもの（unknown / expired）にする。"""
    fetched_at = fetched_at or _today()
    today = today or _today()
    years = _validity_years(conn, scheme_id)
    seen: set[str] = set()
    stats = {"inserted": 0, "updated": 0, "removed": 0}
    with conn:
        for rec in records:
            key = licensee_key(rec.get("license_no"), rec["name"], rec.get("address"))
            if key in seen:
                continue
            seen.add(key)
            vf = parse_date(rec.get("valid_from"))
            vu = _until(vf, parse_date(rec.get("valid_until")), years)
            existed = conn.execute("SELECT 1 FROM licensee WHERE scheme_id = ? AND license_no_key = ?",
                                   (scheme_id, key)).fetchone()
            conn.execute(
                """INSERT INTO licensee (scheme_id, license_no, license_no_key, name, name_key, address,
                       representative, valid_from, valid_until, sub_scope, source_doc, fetched_at, status, needs_review)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scheme_id, license_no_key) DO UPDATE SET
                       license_no = excluded.license_no, name = excluded.name, name_key = excluded.name_key,
                       address = excluded.address, representative = excluded.representative,
                       valid_from = excluded.valid_from, valid_until = excluded.valid_until,
                       sub_scope = excluded.sub_scope, source_doc = excluded.source_doc,
                       fetched_at = excluded.fetched_at, status = excluded.status,
                       needs_review = excluded.needs_review""",
                (scheme_id, rec.get("license_no"), key, rec["name"], normalize_name(rec["name"]), rec.get("address"),
                 rec.get("representative"), _iso(vf), _iso(vu), rec.get("sub_scope"), doc_id,
                 fetched_at.isoformat(), _status(vu, today, rec.get("status")),
                 int(bool(rec.get("needs_review")))))
            stats["updated" if existed else "inserted"] += 1
        for row in conn.execute("SELECT licensee_id, license_no_key, valid_until, status FROM licensee "
                                "WHERE scheme_id = ? AND status = 'active'", (scheme_id,)).fetchall():
            if row["license_no_key"] not in seen:
                vu = parse_date(row["valid_until"])
                conn.execute("UPDATE licensee SET status = ? WHERE licensee_id = ?",
                             ("expired" if vu and vu < today else "unknown", row["licensee_id"]))
                stats["removed"] += 1
        _touch_roster(conn, scheme_id, doc_id, fetched_at)
    return stats


def apply_notice_events(conn: sqlite3.Connection, scheme_id: str, events: list[dict], *, doc_id: int,
                        notice_date: dt.date, fetched_at: dt.date | None = None,
                        today: dt.date | None = None) -> dict:
    """告示の差分（新規・更新・変更・廃止）を積み上げる。告示は古い順に適用すること。

    差分を同じ業者に積むには番号で追う必要があるので、告示の行は license_no が必須
    （商号＋所在地のキーは、商号変更・移転の告示で別の業者になってしまう）。
    """
    fetched_at = fetched_at or _today()
    today = today or _today()
    years = _validity_years(conn, scheme_id)
    stats = {"new": 0, "renew": 0, "change": 0, "revoke": 0, "skipped": 0}
    with conn:
        for ev in events:
            kind = ev.get("event")
            key = normalize_license_no(ev["license_no"])
            cur = conn.execute("SELECT * FROM licensee WHERE scheme_id = ? AND license_no_key = ?",
                               (scheme_id, key)).fetchone()
            if kind == "revoke":
                if cur:
                    conn.execute("UPDATE licensee SET status = 'revoked', valid_until = COALESCE(?, valid_until), "
                                 "source_doc = ?, fetched_at = ? WHERE licensee_id = ?",
                                 (_iso(parse_date(ev.get("valid_until"))), doc_id, fetched_at.isoformat(),
                                  cur["licensee_id"]))
                else:
                    # 指定の記録がないまま廃止の告示だけがある：過去分の欠けを示す行として残す
                    conn.execute(
                        """INSERT INTO licensee (scheme_id, license_no, license_no_key, name, name_key, address,
                               source_doc, fetched_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'revoked')""",
                        (scheme_id, ev["license_no"], key, ev["name"], normalize_name(ev["name"]),
                         ev.get("address"), doc_id, fetched_at.isoformat()))
                stats["revoke"] += 1
                continue
            if kind not in ("new", "renew", "change"):
                stats["skipped"] += 1
                continue
            if kind == "change" and cur:
                vf = parse_date(cur["valid_from"])
                vu = parse_date(ev.get("valid_until")) or parse_date(cur["valid_until"])
            else:
                vf = parse_date(ev.get("valid_from")) or notice_date
                vu = _until(vf, parse_date(ev.get("valid_until")), years)
            name = ev.get("name") or (cur and cur["name"])
            conn.execute(
                """INSERT INTO licensee (scheme_id, license_no, license_no_key, name, name_key, address,
                       representative, valid_from, valid_until, sub_scope, source_doc, fetched_at, status, needs_review)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scheme_id, license_no_key) DO UPDATE SET
                       license_no = excluded.license_no, name = excluded.name, name_key = excluded.name_key,
                       address = COALESCE(excluded.address, address),
                       representative = COALESCE(excluded.representative, representative),
                       valid_from = excluded.valid_from, valid_until = excluded.valid_until,
                       sub_scope = COALESCE(excluded.sub_scope, sub_scope), source_doc = excluded.source_doc,
                       fetched_at = excluded.fetched_at, status = excluded.status,
                       needs_review = excluded.needs_review""",
                (scheme_id, ev["license_no"], key, name, normalize_name(name), ev.get("address"),
                 ev.get("representative"), _iso(vf), _iso(vu), ev.get("sub_scope"), doc_id,
                 fetched_at.isoformat(), _status(vu, today), int(bool(ev.get("needs_review")))))
            stats[kind] += 1
        _touch_roster(conn, scheme_id, doc_id, fetched_at)
    return stats
