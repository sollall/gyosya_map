"""名簿ファイルのダウンロード。

制度の掲載ページ（scheme.source_url）を取得し、ページ内の Excel・PDF・CSV へのリンクから名簿を探す。
source_url がファイルそのものなら、それを取る。どのリンクが名簿かは制度ごとの設定で絞る:

  "download": {
    "page": "https://...",            # 掲載ページ（省略時は source_url）
    "text_pattern": "登録業者名簿",    # リンクの文字（正規表現）
    "href_pattern": "\\\\.xlsx?$",       # リンク先URL（正規表現）
    "pick": "first"                   # first（既定）/ all
  }
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from . import fetch as _fetch

FILE_EXTS = (".xlsx", ".xlsm", ".xls", ".pdf", ".csv")


@dataclass
class DownloadConfig:
    page: str | None = None
    text_pattern: str | None = None
    href_pattern: str | None = None
    pick: str = "first"

    def __post_init__(self):
        if self.pick not in ("first", "all"):
            raise ValueError(f"pick は first か all: {self.pick}")
        for p in (self.text_pattern, self.href_pattern):
            if p:
                re.compile(p)

    @classmethod
    def from_json(cls, text: str | None) -> "DownloadConfig":
        return cls(**json.loads(text)) if text else cls()


@dataclass
class Link:
    url: str
    text: str


@dataclass
class Downloaded:
    scheme_id: str
    url: str            # ファイルのURL
    page: str | None    # 見つけた掲載ページ
    path: Path
    text: str = ""


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []
        elif tag == "img" and self._href is not None:
            self._text.append(dict(attrs).get("alt") or "")

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._text)).strip()))
            self._href = None


def decode_html(body: bytes) -> str:
    """自治体サイトは Shift_JIS のページも多い。meta の charset を見て、だめなら順に試す。"""
    m = re.search(rb"""charset=["']?([A-Za-z0-9_\-]+)""", body[:4096])
    candidates = ([m.group(1).decode("ascii")] if m else []) + ["utf-8", "cp932", "euc_jp"]
    for enc in candidates:
        try:
            return body.decode("cp932" if enc.lower() in ("shift_jis", "sjis", "x-sjis") else enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def is_file_url(url: str) -> bool:
    return unquote(urlparse(url).path).lower().endswith(FILE_EXTS)


def find_links(html: str, base_url: str, config: DownloadConfig | None = None) -> list[Link]:
    """ページ内の名簿ファイルへのリンク（重複なし、ページ上の順）。"""
    config = config or DownloadConfig()
    p = _LinkParser()
    p.feed(html)
    seen, out = set(), []
    for href, text in p.links:
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        if not is_file_url(url) or url in seen:
            continue
        if config.text_pattern and not re.search(config.text_pattern, text):
            continue
        if config.href_pattern and not re.search(config.href_pattern, unquote(url)):
            continue
        seen.add(url)
        out.append(Link(url, text))
    return out


def scheme_targets(conn: sqlite3.Connection, scheme_id: str) -> tuple[sqlite3.Row, DownloadConfig]:
    row = conn.execute("SELECT * FROM scheme WHERE scheme_id = ?", (scheme_id,)).fetchone()
    if row is None:
        raise KeyError(f"未登録の制度です: {scheme_id}")
    return row, DownloadConfig.from_json(row["download_config"])


def list_links(conn: sqlite3.Connection, scheme_id: str, fetcher=None) -> list[Link]:
    """設定を書く前に、掲載ページにどんなファイルのリンクがあるかを見る（絞り込みなし）。"""
    fetcher = fetcher or _fetch.fetch
    scheme, config = scheme_targets(conn, scheme_id)
    page = config.page or scheme["source_url"]
    if not page:
        raise ValueError(f"{scheme_id} には掲載ページ（source_url）がありません")
    if is_file_url(page):
        return [Link(page, "(source_url がファイルそのもの)")]
    return find_links(decode_html(fetcher(page)), page)


def download_scheme(conn: sqlite3.Connection, scheme_id: str, dest_dir: str | Path = "data/downloads",
                    fetcher=None, today: dt.date | None = None) -> list[Downloaded]:
    fetcher = fetcher or _fetch.fetch
    today = today or dt.date.today()
    scheme, config = scheme_targets(conn, scheme_id)
    page = config.page or scheme["source_url"]
    if not page:
        raise ValueError(f"{scheme_id} には掲載ページ（source_url）がありません。seed の source_url か download.page を書いてください")
    if is_file_url(page):
        links, found_on = [Link(page, "")], None
    else:
        links, found_on = find_links(decode_html(fetcher(page)), page, config), page
        if not links:
            raise ValueError(f"{page} に条件に合う名簿ファイルのリンクがありません。"
                             f"`fetch --scheme {scheme_id} --list` でリンクを確かめ、seed の download を直してください")
        if len(links) > 1 and not (config.text_pattern or config.href_pattern):
            # どれが名簿か分からないまま最初のリンクを取ると、手引きや要綱を名簿として取り込んでしまう
            listing = "\n".join(f"  {l.text or '—'}\t{l.url}" for l in links)
            raise ValueError(f"{page} にファイルのリンクが {len(links)} 個あり、どれが名簿か決められません。\n{listing}\n"
                             f"seed の制度 {scheme_id} に download.text_pattern か href_pattern を書いて絞ってください")
        if config.pick == "first":
            links = links[:1]
    out = []
    for link in links:
        name = unquote(Path(urlparse(link.url).path).name)
        dest = Path(dest_dir) / scheme_id / today.isoformat() / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fetcher(link.url))
        out.append(Downloaded(scheme_id, link.url, found_on, dest, link.text))
    return out


def downloadable_schemes(conn: sqlite3.Connection) -> list[str]:
    """全件名簿型・告示型で、掲載ページが分かっている制度（--all の対象）。

    full_list_html はダウンロードできるファイルが無い（掲載ページのHTML表だけ）ので入れない。
    """
    return [r[0] for r in conn.execute(
        "SELECT scheme_id FROM scheme WHERE publication_format IN "
        "('full_list_excel', 'full_list_pdf', 'full_list_csv', 'notice_diff') "
        "AND (source_url IS NOT NULL OR download_config IS NOT NULL) ORDER BY scheme_id")]
