"""自治体サイトからの取得と、掲載ページの変化検知。

1サイトあたりの負荷を抑えるため、同じホストへの取得は MIN_INTERVAL 秒以上あける。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

USER_AGENT = "gyosya_map/0.1 (+https://github.com/sollall/gyosya_map)"
MIN_INTERVAL = 10.0
# HEAD を受け付けないサーバーの応答。1バイトだけの GET で代わりにヘッダを見る
_HEAD_REFUSED = (400, 403, 405, 501)
_last_access: dict[str, float] = {}


def _open(req: urllib.request.Request, timeout: float, opener):
    """同じホストへの間隔を守って1回だけ開く。"""
    host = urlparse(req.full_url).netloc
    wait = _last_access.get(host, 0) + MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    open_url = opener.open if opener is not None else urllib.request.urlopen
    try:
        return open_url(req, timeout=timeout)
    finally:
        _last_access[host] = time.monotonic()


def request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
            timeout: float = 60.0, opener=None) -> bytes:
    """同じホストへの間隔を守って1回だけ取得する。

    data を渡すと POST になる。opener には Cookie を持つ opener を渡せる
    （検索システムはセッションを張ってから検索する）。
    """
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with _open(req, timeout, opener) as res:
        return res.read()


def fetch(url: str, *, timeout: float = 60.0) -> bytes:
    return request(url, timeout=timeout)


def head(url: str, *, timeout: float = 30.0, opener=None) -> dict[str, str]:
    """応答ヘッダだけを取る（小文字のキー）。

    拡張子の無いURLでファイルを配るCMSがあるので、Content-Type でファイルかどうかを見分けるために使う。
    HEAD を受け付けないサーバーには `Range: bytes=0-0` を付けた GET で代替する。
    取得はどちらも request() と同じ間隔の制限に従う。
    """
    def once(method: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(extra or {})}, method=method)
        with _open(req, timeout, opener) as res:
            return {k.lower(): v for k, v in res.headers.items()}

    try:
        return once("HEAD")
    except urllib.error.HTTPError as e:
        if e.code not in _HEAD_REFUSED:
            raise
    return once("GET", {"Range": "bytes=0-0"})


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
