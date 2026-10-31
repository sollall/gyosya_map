"""名簿ファイルのダウンロード。

制度の掲載ページ（scheme.source_url）を取得し、ページ内の Excel・PDF・CSV へのリンクから名簿を探す。
source_url がファイルそのものなら、それを取る。どのリンクが名簿かは制度ごとの設定で絞る:

  "download": {
    "page": "https://...",            # 掲載ページ（省略時は source_url）
    "text_pattern": "登録業者名簿",    # リンクの文字（正規表現）
    "href_pattern": "\\\\.xlsx?$",       # リンク先URL（正規表現）
    "pick": "first"                   # first（既定）/ all
  }

リンクがファイルかどうかは、まずURLの拡張子で見る。拡張子の無いURLでファイルを配るCMSもある
（東京都の新CMSは https://.../documents/d/<局>/<ID> で名簿のPDFを配る）ので、
その場合は HEAD の Content-Type / Content-Disposition で確かめる。同じホストへの取得は
10秒以上あける（ingest/fetch.py）ため、HEAD を打つのはファイル配布らしいリンクだけに絞る。
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

# Content-Type → 保存するときに付ける拡張子（パーサーの選択は拡張子に依る。ingest/parse.py）
EXT_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "application/x-pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
    "application/vnd.ms-excel": ".xls",
    "application/msexcel": ".xls",
    "application/x-msexcel": ".xls",
    "application/x-excel": ".xls",
    "application/excel": ".xls",
    "text/csv": ".csv",
    "application/csv": ".csv",
    "text/comma-separated-values": ".csv",
}
OCTET_STREAM = ("application/octet-stream", "binary/octet-stream")

# 掲載ページ・画像・スクリプトだと拡張子で分かるもの。HEAD を打たない
PAGE_EXTS = (".html", ".htm", ".shtml", ".xhtml", ".php", ".asp", ".aspx", ".jsp", ".cgi", ".do",
             ".xml", ".json", ".rss", ".css", ".js", ".txt",
             ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp", ".ico")

# 拡張子の無いリンクのうち HEAD で確かめるもの。自治体サイトはウェブアクセシビリティの指針で
# ダウンロードのリンクに形式とファイルサイズを添えるので、それを手がかりにする
DOWNLOAD_HINT = re.compile(
    r"PDF|ＰＤＦ|Excel|エクセル|ＥＸＣＥＬ|CSV|ＣＳＶ|xlsx?|ダウンロード"
    r"|[0-9０-９][0-9０-９.,．，]*\s*(?:KB|MB|GB|ＫＢ|ＭＢ|ＧＢ|キロバイト|メガバイト|バイト)", re.I)
MAX_PROBES = 20


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
    headers: dict[str, str] = field(default_factory=dict)   # HEAD で確かめたリンクだけ入る


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


def content_type(headers: dict[str, str]) -> str:
    """Content-Type の型だけ（charset などのパラメータと大小文字を落とす）。"""
    return (headers.get("content-type") or "").split(";")[0].strip().lower()


def disposition_filename(headers: dict[str, str]) -> str:
    """Content-Disposition のファイル名（RFC 5987 の filename* にも対応）。無ければ空。"""
    value = headers.get("content-disposition") or ""
    if m := re.search(r"""filename\*\s*=\s*[\w.-]*'[^']*'([^;]+)""", value, re.I):
        return unquote(m.group(1).strip())
    if m := re.search(r"""filename\s*=\s*(?:"([^"]*)"|([^;]+))""", value, re.I):
        return (m.group(1) or m.group(2)).strip()
    return ""


def is_file_content(headers: dict[str, str]) -> bool:
    """応答ヘッダが名簿ファイル（PDF・Excel・CSV）を指しているか。"""
    name = disposition_filename(headers).lower()
    if name.endswith(FILE_EXTS):
        return True
    if name.endswith(PAGE_EXTS):
        return False
    if (ctype := content_type(headers)) in EXT_BY_CONTENT_TYPE:
        return True
    # 形式を application/octet-stream で返すCMSもある。ファイル名が無く添付指定だけならファイルと見る
    return ctype in OCTET_STREAM and (headers.get("content-disposition") or "").lower().startswith("attachment")


def is_file_url(url: str, prober=None) -> bool:
    """URLが名簿ファイルを指しているか。

    パスの拡張子で分かればそれで決める。拡張子の無いURLでファイルを配るCMS
    （東京都の新CMS https://.../documents/d/<局>/<ID> など）のために、prober を渡したときは
    HEAD の Content-Type / Content-Disposition でも判定する。prober は URL から応答ヘッダの辞書
    （小文字のキー。ingest/fetch.py の head）を返す呼び出し可能オブジェクト。
    """
    parts = urlparse(url)
    path = unquote(parts.path).lower()
    if path.endswith(FILE_EXTS):
        return True
    if prober is None or parts.scheme not in ("http", "https") or path.endswith(PAGE_EXTS):
        return False
    return is_file_content(prober(url))


def find_links(html: str, base_url: str, config: DownloadConfig | None = None, prober=None, *,
               max_probes: int = MAX_PROBES, errors: list[str] | None = None) -> list[Link]:
    """ページ内の名簿ファイルへのリンク（重複なし、ページ上の順）。

    prober を渡すと、拡張子の無いリンクを HEAD で確かめる。同じホストへの取得は10秒以上あけるので、
    問い合わせるのは config で絞り込まれたリンクか、リンクの文字がファイル配布らしいもの
    （形式・ファイルサイズの表示）だけにし、max_probes 件で打ち切る。errors を渡すと、
    HEAD に失敗したリンクと打ち切りをそこに記録する（呼び出し側がエラーに添えて出す）。
    """
    config = config or DownloadConfig()
    p = _LinkParser()
    p.feed(html)
    narrowed = bool(config.text_pattern or config.href_pattern)
    seen, out, probes, truncated = set(), [], 0, False
    for href, text in p.links:
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        if config.text_pattern and not re.search(config.text_pattern, text):
            continue
        if config.href_pattern and not re.search(config.href_pattern, unquote(url)):
            continue
        seen.add(url)
        if is_file_url(url):
            out.append(Link(url, text))
            continue
        if prober is None or not (narrowed or DOWNLOAD_HINT.search(text)):
            continue
        if probes >= max_probes:
            if errors is not None and not truncated:
                truncated = True
                errors.append(f"HEAD で確かめるリンクが {max_probes} 件を超えたので打ち切りました"
                              "（download の text_pattern / href_pattern で絞ってください）")
            continue
        probes += 1
        try:
            headers = prober(url)
        except OSError as e:        # 1件の取得失敗でページ全体の探索を止めない
            if errors is not None:
                errors.append(f"{url}: ヘッダを取れませんでした: {e}")
            continue
        if is_file_content(headers):
            out.append(Link(url, text, headers))
    return out


def dest_name(link: Link) -> str:
    """保存するファイル名。拡張子の無いURLは Content-Disposition か Content-Type から補う
    （パーサーの選択が拡張子に依るため。ingest/parse.py）。"""
    name = unquote(Path(urlparse(link.url).path).name)
    if name.lower().endswith(FILE_EXTS):
        return name
    # ヘッダのファイル名はサイト任せなので、ディレクトリと制御文字を落としてから使う
    from_header = re.sub(r"[\x00-\x1f]", "", disposition_filename(link.headers).replace("\\", "/"))
    from_header = Path(from_header).name.strip()
    if from_header.lower().endswith(FILE_EXTS):
        return from_header
    return (name or "index") + EXT_BY_CONTENT_TYPE.get(content_type(link.headers), "")


def scheme_targets(conn: sqlite3.Connection, scheme_id: str) -> tuple[sqlite3.Row, DownloadConfig]:
    row = conn.execute("SELECT * FROM scheme WHERE scheme_id = ?", (scheme_id,)).fetchone()
    if row is None:
        raise KeyError(f"未登録の制度です: {scheme_id}")
    return row, DownloadConfig.from_json(row["download_config"])


def list_links(conn: sqlite3.Connection, scheme_id: str, fetcher=None, prober=None,
               errors: list[str] | None = None) -> list[Link]:
    """設定を書く前に、掲載ページにどんなファイルのリンクがあるかを見る（絞り込みなし）。"""
    fetcher = fetcher or _fetch.fetch
    prober = prober or _fetch.head
    scheme, config = scheme_targets(conn, scheme_id)
    page = config.page or scheme["source_url"]
    if not page:
        raise ValueError(f"{scheme_id} には掲載ページ（source_url）がありません")
    if is_file_url(page):
        return [Link(page, "(source_url がファイルそのもの)")]
    return find_links(decode_html(fetcher(page)), page, prober=prober, errors=errors)


def download_scheme(conn: sqlite3.Connection, scheme_id: str, dest_dir: str | Path = "data/downloads",
                    fetcher=None, today: dt.date | None = None, prober=None) -> list[Downloaded]:
    fetcher = fetcher or _fetch.fetch
    prober = prober or _fetch.head
    today = today or dt.date.today()
    scheme, config = scheme_targets(conn, scheme_id)
    page = config.page or scheme["source_url"]
    if not page:
        raise ValueError(f"{scheme_id} には掲載ページ（source_url）がありません。seed の source_url か download.page を書いてください")
    if is_file_url(page):
        links, found_on = [Link(page, "")], None
    else:
        errors: list[str] = []
        links = find_links(decode_html(fetcher(page)), page, config, prober=prober, errors=errors)
        found_on = page
        if not links:
            raise ValueError(f"{page} に条件に合う名簿ファイルのリンクがありません。"
                             f"`fetch --scheme {scheme_id} --list` でリンクを確かめ、seed の download を直してください"
                             + "".join(f"\n  {e}" for e in errors))
        if len(links) > 1 and not (config.text_pattern or config.href_pattern):
            # どれが名簿か分からないまま最初のリンクを取ると、手引きや要綱を名簿として取り込んでしまう
            listing = "\n".join(f"  {l.text or '—'}\t{l.url}" for l in links)
            raise ValueError(f"{page} にファイルのリンクが {len(links)} 個あり、どれが名簿か決められません。\n{listing}\n"
                             f"seed の制度 {scheme_id} に download.text_pattern か href_pattern を書いて絞ってください")
        if config.pick == "first":
            links = links[:1]
    out = []
    for link in links:
        dest = Path(dest_dir) / scheme_id / today.isoformat() / dest_name(link)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fetcher(link.url))
        out.append(Downloaded(scheme_id, link.url, found_on, dest, link.text))
    return out


def downloadable_schemes(conn: sqlite3.Connection) -> list[str]:
    """全件名簿型・告示型で、掲載ページが分かっている制度（--all の対象）。"""
    return [r[0] for r in conn.execute(
        "SELECT scheme_id FROM scheme WHERE publication_format IN ('full_list_excel', 'full_list_pdf', 'notice_diff') "
        "AND (source_url IS NOT NULL OR download_config IS NOT NULL) ORDER BY scheme_id")]
