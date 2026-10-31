"""更新・鮮度管理: 期限の自動処理と、古い名簿の警告。"""

from __future__ import annotations

import datetime as dt
import sqlite3

STALE_DAYS = 90


def expire(conn: sqlite3.Connection, today: dt.date | None = None) -> int:
    """valid_until を過ぎた行を expired に切り替える。"""
    today = today or dt.date.today()
    with conn:
        cur = conn.execute("UPDATE licensee SET status = 'expired' "
                           "WHERE status = 'active' AND valid_until IS NOT NULL AND valid_until < ?",
                           (today.isoformat(),))
    return cur.rowcount


def roster_fetched_at(conn: sqlite3.Connection, scheme_id: str) -> dt.date | None:
    row = conn.execute("SELECT fetched_at FROM roster_fetch WHERE scheme_id = ?", (scheme_id,)).fetchone()
    return dt.date.fromisoformat(row[0]) if row else None


def is_stale(fetched_at: dt.date | None, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    return fetched_at is not None and (today - fetched_at).days > STALE_DAYS
