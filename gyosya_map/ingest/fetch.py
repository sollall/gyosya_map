"""自治体サイトからの取得と、掲載ページの変化検知。

1サイトあたりの負荷を抑えるため、同じホストへの取得は MIN_INTERVAL 秒以上あける。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

USER_AGENT = "gyosya_map/0.1 (+https://github.com/sollall/gyosya_map)"
MIN_INTERVAL = 10.0
_last_access: dict[str, float] = {}


def fetch(url: str, *, timeout: float = 60.0) -> bytes:
    host = urlparse(url).netloc
    wait = _last_access.get(host, 0) + MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read()
    finally:
        _last_access[host] = time.monotonic()


def download(url: str, dest_dir: str | Path = "data/downloads") -> Path:
    body = fetch(url)
    name = unquote(Path(urlparse(url).path).name) or "index.html"
    dest = Path(dest_dir) / dt.date.today().isoformat() / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return dest


def check_pages(conn: sqlite3.Connection, fetcher=fetch, now: dt.datetime | None = None) -> list[dict]:
    """掲載ページのハッシュを比べ、変化・取得失敗を再取得キューに入れる。"""
    now_s = (now or dt.datetime.now()).isoformat(timespec="seconds")
    results = []
    for w in conn.execute("SELECT * FROM page_watch").fetchall():
        try:
            sha = hashlib.sha256(fetcher(w["url"])).hexdigest()
        except Exception as e:  # noqa: BLE001 - 取得失敗はすべて記録してアラートにする
            with conn:
                conn.execute("UPDATE page_watch SET checked_at = ?, last_error = ? WHERE url = ?",
                             (now_s, str(e), w["url"]))
                conn.execute("INSERT INTO refetch_queue (url, scheme_id, reason, queued_at) VALUES (?, ?, ?, ?)",
                             (w["url"], w["scheme_id"], f"取得失敗: {e}", now_s))
            results.append({"url": w["url"], "result": "error", "error": str(e)})
            continue
        changed = w["last_sha256"] is not None and w["last_sha256"] != sha
        with conn:
            conn.execute("UPDATE page_watch SET last_sha256 = ?, checked_at = ?, last_error = NULL WHERE url = ?",
                         (sha, now_s, w["url"]))
            if changed:
                conn.execute("INSERT INTO refetch_queue (url, scheme_id, reason, queued_at) VALUES (?, ?, ?, ?)",
                             (w["url"], w["scheme_id"], "掲載ページが変化", now_s))
        results.append({"url": w["url"], "result": "changed" if changed else
                        ("first" if w["last_sha256"] is None else "unchanged")})
    return results
