"""検索システム型の制度を照合のときに問い合わせる（国交省 建設業者・宅建業者等企業情報検索システム）。

対象: https://etsuran2.mlit.go.jp/TAKKEN/kensetuKensaku.do
対応する制度: `mlit.construction`（国土交通大臣 建設業許可）と `pref-<コード>.construction`（知事 建設業許可）。

## 結果を licensee に入れない理由

この制度には名簿の一括ダウンロードがなく検索システムしかないので、**照合のときに
業者名か許可番号で1件ずつ問い合わせ、結果は `licensee` に入れない**（`check()` のときだけ参照する）。

- 再現性: コミット済みの抽出データと決定的なコードだけで、同じDBと同じ地図HTMLが再生成できる
  状態を保つのが完成条件。検索システムの応答は日によって変わり、原本ファイルも残らないので、
  DBに書くとクリーンな環境から同じDBを作れなくなる。
- 検品済みフラグ: 地図に出すのは検品済みの抽出データだけという約束がある。自動で読んだHTMLは
  その条件を満たさないため、地図では引き続き「名簿未取り込み」側に置く（`mapview.coverage`）。
- 相手のサイトに負荷をかけない: 1回の照合で数リクエストに収まる使い方だけにして、
  名簿を丸ごと落とす設計にはしない。

## 利用条件の確認（2026-10-31 時点）

- robots.txt: https://etsuran2.mlit.go.jp/robots.txt は 404（クローラへの指示なし）。
- 検索システムの画面にも「システムのご利用方法」（/TAKKEN/manual/manual.pdf）にも、機械的な
  アクセスを禁じる記載はない。「システムが混み合う等により検索できない場合は、しばらく時間を
  おいてから再度アクセスしてください」との案内のみ。
- 国土交通省のサイトポリシー（https://www.mlit.go.jp/link.html）では、コンテンツは
  公共データ利用規約（PDL1.0）に準拠した条件で利用でき、出典の記載が必要。照合結果には
  出典URL（source_url）と取得日を付けている。
- 以上から機械的アクセスは禁じられていないと判断した。負荷を避けるため、同一ホストへの
  アクセスは10秒以上あける（`ingest.fetch.request`）。1業者あたりのリクエストは
  検索1〜2回＋業者概要（`MAX_DETAIL` 件まで）に抑え、ページ送りも `MAX_PAGES` で止める。

## 画面の仕組み（2026-10-31 時点）

Struts の画面で、セッション（JSESSIONID）を取ってから POST する。文字コードは Windows-31J。

- 検索: `kensetuKensaku.do` に `CMD=search`。商号（漢字）は `comNameKanjiOnly`、
  許可番号は `licenseNoKbn`（00 大臣 / 01 知事）＋ `licenseNoFrom`・`licenseNoTo`。
  `choice=1`（本店）にすると営業所ごとの重複が出ず、1許可1行になる。
- ページ送り: `CMD=selectPage` と `pageListNo1`・`pageListNo2`。
- 業者概要: `ksGaiyo.do` に `sv_licenseNo`（一覧のリンクにある8桁＝許可行政庁コード＋許可番号）。
  許可の有効期間はこの画面にしかない。
- 許可行政庁は画面には「関東地方整備局」「神奈川県」のように出るが、8桁コードの先頭2桁が
  許可行政庁コード（00＝国土交通大臣、01〜47＝都道府県）なので、制度との対応はこれで判定する。
  許可番号で検索すると他県の同番号も返るため、この絞り込みは必須。

## 失敗の区別

`lookup()` は `SearchSystemClient` の約束どおり「見つかった行のリスト」か `None` を返し、
内訳は `log` と `last_error` に残す。

| state | 意味 |
|---|---|
| `found` | 一致する行が見つかった |
| `not_found` | 検索できたが該当なし（「該当するデータが存在しません。」） |
| `error` | 通信・HTTPの失敗（取得失敗） |
| `changed` | 画面の作りが想定と違う（システム変更の可能性） |
| `too_many` | 候補が多すぎて絞り込めない（該当なしと断定しない） |
| `unsupported` | この検索システムの対象ではない制度 |
"""

from __future__ import annotations

import datetime as dt
import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from .ingest import fetch as _fetch
from .normalize import nfkc, normalize_license_no, normalize_name, parse_date, strip_corp_form

HOST = "etsuran2.mlit.go.jp"
BASE_URL = f"https://{HOST}/TAKKEN/"
SEARCH_URL = BASE_URL + "kensetuKensaku.do"
DETAIL_URL = BASE_URL + "ksGaiyo.do"
ENCODING = "cp932"                  # 画面は Windows-31J
NO_HIT = "該当するデータが存在しません"
PAGE_SIZE = 50                      # 1ページの表示件数（画面で選べる最大）
MAX_PAGES = 4                       # 1回の問い合わせで見るページ数の上限
MAX_DETAIL = 5                      # 業者概要を開く件数の上限
LIST_HEADERS = ("許可行政庁", "許可番号", "商号又は名称", "代表者名", "営業所名", "所在地")


class SearchSystemError(RuntimeError):
    """検索システムに問い合わせられなかった。state で種類を区別する。"""

    state = "error"


class Unavailable(SearchSystemError):
    """通信・HTTPの失敗（取得失敗）。"""

    state = "error"


class Changed(SearchSystemError):
    """画面の作りが想定と違う（システム変更の可能性）。"""

    state = "changed"


class TooMany(SearchSystemError):
    """候補が多すぎて絞り込めない。該当なしとは区別する。"""

    state = "too_many"


class Unsupported(SearchSystemError):
    """この検索システムの対象ではない制度。"""

    state = "unsupported"


@dataclass
class Row:
    """検索結果一覧の1行。"""

    authority_code: str     # 00 = 国土交通大臣、01〜47 = 都道府県
    authority: str          # 画面の表示（関東地方整備局、神奈川県 など）
    license_no: str         # 「第002100号」→ 002100
    name: str
    address: str
    code: str               # 業者概要を開く8桁（許可行政庁コード＋許可番号）


@dataclass
class Outcome:
    """1回の問い合わせの結果。state は上の表のとおり。"""

    scheme_id: str
    state: str
    message: str = ""
    rows: int = 0
    requests: int = 0


def _get(scheme, key):
    """sqlite3.Row でも dict でも読めるようにする。"""
    try:
        return scheme[key]
    except (IndexError, KeyError):
        return None


def supported(scheme) -> bool:
    """この検索システムで確かめられる制度か。"""
    return (_get(scheme, "publication_format") == "search_system"
            and _get(scheme, "trade") == "construction"
            and HOST in (_get(scheme, "source_url") or ""))


def authority_code(scheme) -> str | None:
    """制度の担当機関 → 許可行政庁コード（国土交通大臣は 00、都道府県は2桁コード）。"""
    authority_id = _get(scheme, "authority_id") or ""
    if authority_id == "mlit":
        return "00"
    m = re.fullmatch(r"pref-(\d{2})", authority_id)
    return m.group(1) if m else None


def license_digits(license_no: str | None) -> str | None:
    """広告の「神奈川県知事許可（般-3）第12345号」などから許可番号の6桁を取る。"""
    numbers = re.findall(r"\d+", nfkc(license_no or ""))
    if not numbers:
        return None
    no = numbers[-1].lstrip("0") or "0"
    return no.zfill(6) if len(no) <= 6 else None


def query_name(name: str | None) -> str:
    """商号の検索語。画面の案内どおり株式会社などの法人格を外して送る。"""
    return re.sub(r"[\s　]+", "", strip_corp_form(nfkc(name or "")))


_BASE_FIELDS = {
    "CMD": "search", "caller": "KS",
    "rdoSelect": "2",                   # 1: 全角カナ検索 / 2: 漢字検索
    "rdoSelectJoken": "1", "rdoSelectSort": "1",
    "comNameKanaOnly": "", "comNameKanjiOnly": "",
    "licenseNoKbn": "", "licenseNoFrom": "", "licenseNoTo": "",
    "choice": "1",                      # 本店（営業所ごとの重複を出さない）
    "kenCode": "", "gyosyu": "", "gyosyuType": "", "keyWord": "",
    "sortValue": "1", "dispCount": str(PAGE_SIZE), "dispPage": "1",
    "pageListNo1": "1", "pageListNo2": "1",
    "resultCount": "0", "pageCount": "0", "sv_licenseNo": "",
}
# 画面は入力を sv_ 付きの hidden に持ち回す。ページ送りのときに検索条件を保つため同じ値を入れる。
_MIRROR = ("rdoSelect", "rdoSelectJoken", "rdoSelectSort", "kenCode", "choice", "gyosyu", "gyosyuType",
           "keyWord", "sortValue", "comNameKanaOnly", "comNameKanjiOnly", "licenseNoKbn", "licenseNoFrom",
           "licenseNoTo", "dispCount", "dispPage", "pageListNo1", "pageListNo2")


def _fields(**query) -> dict[str, str]:
    f = dict(_BASE_FIELDS)
    f.update(query)
    f.update({f"sv_{k}": f[k] for k in _MIRROR})
    return f


class _Cells(HTMLParser):
    """class が prefix で始まる表の行を、セルの文字列とリンクの onclick にして取り出す。"""

    def __init__(self, prefix: str):
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[str], list[str]]] = []
        self._prefix = prefix
        self._depth = 0
        self._table = 0
        self._row: list[str] | None = None
        self._links: list[str] = []
        self._cell: list[str] | None = None
        self._skip: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "table":
            self._depth += 1
            if not self._table and any(c.startswith(self._prefix) for c in classes):
                self._table = self._depth
            return
        if not self._table:
            return
        if "phonetic" in classes:       # 読み（カナ）は商号・金額の前に入るので飛ばす
            self._skip.append(tag)
        elif tag == "tr":
            self._row, self._links = [], []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "a" and self._row is not None and a.get("onclick"):
            self._links.append(a["onclick"])
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag == "table":
            if self._table == self._depth:
                self._table = 0
            self._depth = max(0, self._depth - 1)
            return
        if not self._table:
            return
        if self._skip and self._skip[-1] == tag:
            self._skip.pop()
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append((self._row, self._links))
            self._row = None


def _text(s: str) -> str:
    return "\n".join(line.strip() for line in s.split("\n") if line.strip())


def _flat(s: str) -> str:
    return re.sub(r"\s+", "", s)


def parse_results(html_text: str) -> tuple[list[Row], int]:
    """検索結果の一覧 → 行と総件数。該当なしは ([], 0)。読めなければ Changed。"""
    table = _Cells("re_disp")
    table.feed(html_text)
    if not table.rows:
        message = _message(html_text)
        if NO_HIT in message:
            return [], 0
        raise Changed(f"検索結果の一覧が読み取れません（画面のメッセージ: {message or '—'}）")
    header = [_flat(c) for c in table.rows[0][0]]
    missing = [h for h in LIST_HEADERS if h not in header]
    if missing:
        raise Changed(f"検索結果の見出しが変わりました（見つからない列: {', '.join(missing)}）")
    index = {h: header.index(h) for h in LIST_HEADERS}
    rows = []
    for cells, links in table.rows[1:]:
        if len(cells) < len(header):
            continue
        code = next((m.group(1) for link in links
                     if (m := re.search(r"js_ShowDetail\('(\d+)'\)", link))), None)
        if code is None or len(code) < 3:
            raise Changed("検索結果から業者概要へのリンク（8桁の許可番号）が読み取れません")
        rows.append(Row(authority_code=code[:2], authority=cells[index["許可行政庁"]],
                        license_no=_flat(cells[index["許可番号"]]).strip("第号"),
                        name=cells[index["商号又は名称"]], address=cells[index["所在地"]], code=code))
    total = _total(html_text)
    return rows, total if total is not None else len(rows)


def parse_detail(html_text: str) -> dict[str, str]:
    """業者概要の画面 → 見出し（空白を詰めたもの）から値への対応。"""
    table = _Cells("re_summ")
    table.feed(html_text)
    out: dict[str, str] = {}
    for cells, _ in table.rows:
        if len(cells) >= 2 and cells[0]:
            out.setdefault(_flat(cells[0]), cells[1])
    for need in ("許可番号", "許可の有効期間"):
        if not out.get(need):
            raise Changed(f"業者概要の画面から「{need}」が読み取れません")
    return out


def _message(html_text: str) -> str:
    m = re.search(r'id="messageText"[^>]*>(.*?)</label>', html_text, re.S)
    return _flat(re.sub(r"<[^>]+>", "", m.group(1))) if m else ""


def _total(html_text: str) -> int | None:
    m = re.search(r'id="resultCount"[^>]*value="(\d+)"', html_text)
    return int(m.group(1)) if m else None


def _period(text: str) -> tuple[dt.date | None, dt.date | None]:
    """「R04年11月10日からR09年11月09日まで」→ 開始日と満了日。"""
    m = re.search(r"(.+?)から(.+?)まで", text or "")
    if not m:
        raise Changed(f"許可の有効期間が読み取れません: {text!r}")
    start, end = parse_date(m.group(1)), parse_date(m.group(2))
    if end is None:
        raise Changed(f"許可の有効期間の満了日が読み取れません: {text!r}")
    return start, end


def _address(text: str) -> str:
    """「〒107-8388／東京都港区／元赤坂１－３－１」→ 郵便番号を外して1行にする。"""
    lines = [line for line in (text or "").split("\n") if not line.startswith("〒")]
    return "".join(lines)


class HttpTransport:
    """セッションを張ってから POST する。同一ホストへの間隔は ingest.fetch が守る。"""

    def __init__(self, *, timeout: float = 60.0):
        self.timeout = timeout
        self.requests = 0
        self._opener = None

    def __call__(self, url: str, fields: dict[str, str]) -> str:
        opener = self._session()
        try:
            body = urllib.parse.urlencode(fields, encoding=ENCODING).encode("ascii")
        except UnicodeEncodeError as e:
            raise Unavailable("検索システムは Shift_JIS のため、送れない文字があります: "
                              f"{e.object[e.start:e.end]!r}") from e
        return self._open(url, data=body, opener=opener,
                          headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": SEARCH_URL})

    def _session(self):
        if self._opener is None:
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            self._open(SEARCH_URL, opener=opener)      # JSESSIONID を受け取る
            self._opener = opener
        return self._opener

    def _open(self, url: str, **kwargs) -> str:
        self.requests += 1
        try:
            raw = _fetch.request(url, timeout=self.timeout, **kwargs)
        except (urllib.error.URLError, OSError) as e:
            raise Unavailable(f"検索システムに接続できません（{url}）: {e}") from e
        return raw.decode(ENCODING, errors="replace")


class MlitConstructionSearch:
    """建設業許可を検索システムで1件ずつ確かめる `matcher.SearchSystemClient`。

    `transport(url, fields) -> str` を差し替えればネットワークなしで試せる。
    """

    def __init__(self, transport=None, *, max_pages: int = MAX_PAGES, max_detail: int = MAX_DETAIL):
        self.transport = transport if transport is not None else HttpTransport()
        self.max_pages = max_pages
        self.max_detail = max_detail
        self.log: list[Outcome] = []
        self.last_error: str | None = None
        self._pages: dict[tuple, str] = {}      # 同じ問い合わせは使い回す（大臣と知事で同じ検索になる）

    def lookup(self, scheme, license_no: str | None, name: str | None) -> list[dict] | None:
        scheme_id = _get(scheme, "scheme_id") or "—"
        before = getattr(self.transport, "requests", 0)
        try:
            rows = self._find(scheme, license_no, name)
            records = [self._record(row) for row in rows]
        except SearchSystemError as e:
            self.last_error = str(e)
            self.log.append(Outcome(scheme_id, e.state, str(e),
                                    requests=getattr(self.transport, "requests", 0) - before))
            return None
        self.last_error = None
        self.log.append(Outcome(scheme_id, "found" if records else "not_found", rows=len(records),
                                requests=getattr(self.transport, "requests", 0) - before))
        return records

    def _find(self, scheme, license_no: str | None, name: str | None) -> list[Row]:
        code = authority_code(scheme)
        if not supported(scheme) or code is None:
            raise Unsupported(f"{_get(scheme, 'scheme_id')} はこの検索システム"
                              f"（{SEARCH_URL}）で確かめられる制度ではありません")
        # 名簿を探す順番は matcher と同じで、番号 → 商号
        if license_no and (no := license_digits(license_no)):
            rows = self._filter(self._search(licenseNoKbn="00" if code == "00" else "01",
                                             licenseNoFrom=no, licenseNoTo=no), code,
                                license_no=no)
            if rows:
                return rows
        if name and (q := query_name(name)):
            return self._filter(self._search(comNameKanjiOnly=q), code, name=name)
        return []

    def _filter(self, rows: list[Row], code: str, *, license_no: str | None = None,
                name: str | None = None) -> list[Row]:
        """許可行政庁が制度と一致し、番号か商号が完全に一致する行だけ。重複は8桁コードで1つにする。"""
        out: dict[str, Row] = {}
        for row in rows:
            if row.authority_code != code:
                continue
            if license_no and normalize_license_no(row.license_no) != normalize_license_no(license_no):
                continue
            if name and normalize_name(row.name) != normalize_name(name):
                continue
            out.setdefault(row.code, row)
        if len(out) > self.max_detail:
            raise TooMany(f"一致する候補が {len(out)} 件あり、絞り込めません"
                          "（正式な商号か許可番号で照会してください）")
        return list(out.values())

    def _search(self, **query) -> list[Row]:
        fields = _fields(**query)
        rows, total = parse_results(self._post(SEARCH_URL, fields))
        pages = max(1, -(-total // PAGE_SIZE))
        if pages > self.max_pages:
            raise TooMany(f"検索結果が {total} 件あり、{self.max_pages} ページまでしか確かめられません"
                          "（正式な商号か許可番号で照会してください）")
        for page in range(2, pages + 1):
            more, _ = parse_results(self._post(SEARCH_URL, _fields(
                **query, CMD="selectPage", pageListNo1=str(page), pageListNo2=str(page),
                resultCount=str(total), pageCount=str(pages))))
            rows += more
        return rows

    def _record(self, row: Row) -> dict:
        """licensee と同じ形の dict。代表者名は照合結果に出さないので入れない。"""
        detail = parse_detail(self._post(DETAIL_URL, _fields(CMD="", sv_licenseNo=row.code)))
        valid_from, valid_until = _period(detail["許可の有効期間"])
        address = _address(detail.get("主たる営業所の所在地", "")) or row.address
        return {"license_no": row.license_no, "name": detail.get("商号又は名称") or row.name,
                "address": address, "valid_from": valid_from and valid_from.isoformat(),
                "valid_until": valid_until.isoformat(), "status": "active", "sub_scope": None,
                "needs_review": 0}

    def _post(self, url: str, fields: dict[str, str]) -> str:
        key = (url, tuple(sorted(fields.items())))
        if key not in self._pages:
            self._pages[key] = self.transport(url, fields)
        return self._pages[key]
