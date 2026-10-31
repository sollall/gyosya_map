"""照合ロジック: 住所・業種・業者名または許可番号 → 5段階の判定と根拠。

返すのは「名簿に載っているか」という事実のみで、違法かどうかの断定はしない。
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .freshness import is_stale, roster_fetched_at
from .jurisdiction import resolve_location, resolve_trade, scheme_covers
from .normalize import normalize_address, normalize_license_no, normalize_name, parse_date, to_wareki
from .trades import get_trade

AUTHORIZED = "公認あり"
OTHER_ONLY = "他機関の公認のみ"
EXPIRED = "期限切れ・取消"
NOT_FOUND = "確認できず"
UNDETERMINED = "判定不能"
NO_SCHEME = "制度なし"

# 全件名簿が公開されている公開形式（取り込めば完全性が high になる）。
# full_list_html は掲載ページのHTML表だけでファイルの配布が無いもの。
FULL_LIST_FORMATS = ("full_list_excel", "full_list_pdf", "full_list_html")


class SearchSystemClient(Protocol):
    """検索システム型の制度（国交省 建設業者検索など）を照会時に問い合わせる。

    見つかった行を licensee と同じ形の dict で返す（該当なしは空リスト）。
    問い合わせられなければ None を返し、理由を `last_error` に入れる（任意）。
    該当なしと問い合わせ失敗を混ぜないこと。失敗のときは名簿を確かめられていないので、
    「名簿に見当たらない」ではなく判定不能になる。
    """

    def lookup(self, scheme: sqlite3.Row, license_no: str | None, name: str | None) -> list[dict] | None: ...


@dataclass
class Evidence:
    authority: str
    license_name: str
    license_no: str
    name: str
    address: str | None
    valid_until: str | None
    status: str
    sub_scope: str | None
    source_url: str | None
    fetched_at: str | None
    needs_review: bool = False


@dataclass
class SchemeInfo:
    authority: str
    license_name: str
    publication_format: str | None
    completeness: str          # high / low / none
    contact: str | None
    source_url: str | None
    fetched_at: str | None


@dataclass
class Result:
    verdict: str
    message: str
    trade: str
    location: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    schemes: list[SchemeInfo] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)      # important は必ず表示する
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = [f"【{self.verdict}】{self.message}", f"業種: {self.trade}"]
        if self.location:
            lines.append(f"所在地の管轄: {self.location}")
        for e in self.evidence:
            until = f" 有効期限: {e.valid_until}" if e.valid_until else ""
            scope = f" 範囲: {e.sub_scope}" if e.sub_scope else ""
            lines.append(f"  根拠: {e.authority} {e.license_name} {_license_label(e.license_no)} {e.name}"
                         f"（{e.status}{'・未検品' if e.needs_review else ''}）{until}{scope}")
            lines.append(f"        出典: {e.source_url or '—'}（取得日: {e.fetched_at or '—'}）")
        for n in self.notes:
            mark = "重要" if n["severity"] == "important" else "参考"
            lines.append(f"  [{mark}] {n['body']}")
        for w in self.warnings:
            lines.append(f"  [注意] {w}")
        if self.verdict in (NOT_FOUND, UNDETERMINED, OTHER_ONLY):
            for s in self.schemes:
                if s.contact:
                    lines.append(f"  確認先: {s.contact}（{s.license_name}）{s.source_url or ''}")
        return "\n".join(lines)


def _license_label(license_no: str) -> str:
    """番号は原文のまま持っているので、「第…号」が既に付いていれば足さない。"""
    return license_no if ("第" in license_no or "号" in license_no) else f"第{license_no}号"


def _completeness(conn: sqlite3.Connection, scheme: sqlite3.Row, live: dict[str, list[dict] | None]) -> str:
    fmt = scheme["publication_format"]
    if fmt in FULL_LIST_FORMATS:
        return "high" if roster_fetched_at(conn, scheme["scheme_id"]) else "none"
    if fmt == "search_system":
        # 問い合わせが失敗した（None）ときは確かめられていないので完全性は none
        return "high" if isinstance(live.get(scheme["scheme_id"]), list) else "none"
    if fmt == "notice_diff":
        return "low"
    return "none"   # phone_only / 未調査


def _find(conn, scheme_ids: list[str], license_no: str | None, name: str | None,
          business_address: str | None) -> list[sqlite3.Row]:
    if not scheme_ids:
        return []
    ph = ",".join("?" * len(scheme_ids))
    base = f"""SELECT l.*, s.license_name, s.required_sub_scope, a.name AS authority_name,
                      COALESCE(d.url, s.source_url) AS source_url
               FROM licensee l JOIN scheme s USING (scheme_id) JOIN authority a USING (authority_id)
               LEFT JOIN source_document d ON d.doc_id = l.source_doc
               WHERE l.scheme_id IN ({ph}) AND l.status != 'unknown'"""
    rows: list[sqlite3.Row] = []
    if license_no:
        rows = conn.execute(base + " AND l.license_no_key = ?",
                            (*scheme_ids, normalize_license_no(license_no))).fetchall()
    if not rows and name:
        rows = conn.execute(base + " AND l.name_key = ?", (*scheme_ids, normalize_name(name))).fetchall()
        if len(rows) > 1 and business_address:
            want = normalize_address(business_address)
            narrowed = [r for r in rows if r["address"] and
                        (normalize_address(r["address"]).startswith(want) or
                         want.startswith(normalize_address(r["address"])))]
            rows = narrowed or rows
    return rows


def _is_valid(row, today: dt.date) -> bool:
    if row["status"] != "active":
        return False
    vu = parse_date(row["valid_until"])
    return vu is None or vu >= today


def _scope_ok(row) -> bool:
    need = row["required_sub_scope"]
    return not need or (row["sub_scope"] is not None and need in row["sub_scope"])


def _evidence(row) -> Evidence:
    return Evidence(authority=row["authority_name"], license_name=row["license_name"],
                    license_no=row["license_no"], name=row["name"], address=row["address"],
                    valid_until=row["valid_until"], status=row["status"], sub_scope=row["sub_scope"],
                    source_url=row["source_url"], fetched_at=row["fetched_at"],
                    needs_review=bool(row["needs_review"]))


def _wareki(iso: str | None) -> str | None:
    d = parse_date(iso)
    return to_wareki(d) if d else None


def check(conn: sqlite3.Connection, *, address: str, trade: str, name: str | None = None,
          license_no: str | None = None, business_address: str | None = None,
          today: dt.date | None = None, search_client: SearchSystemClient | None = None) -> Result:
    today = today or dt.date.today()
    t = get_trade(trade)
    if not (name or license_no) and t.scheme_trades:
        raise ValueError("業者名か許可番号のどちらかが必要です")

    # 2. 制度がない業種
    if not t.scheme_trades:
        return Result(NO_SCHEME, f"{t.label}には公的な公認制度がありません。", t.label)

    # 1. 管轄解決
    loc = resolve_location(conn, address)
    res = resolve_trade(conn, loc, t)
    result = Result(UNDETERMINED, "", t.label, location=loc.municipality + (loc.town or ""))
    if all(r.out_of_area for r in res.values()):
        result.verdict = NO_SCHEME
        result.message = "制度なし（区域外）: " + "／".join(filter(None, (r.note for r in res.values())))
        return result
    local_auth = sorted({a for r in res.values() for a in r.authority_ids})
    if not local_auth:
        result.message = f"{loc.municipality}の{t.label}の制度情報はまだ整備されていません。"
        return result

    ph_t = ",".join("?" * len(t.scheme_trades))
    ph_a = ",".join("?" * len(local_auth))
    found = conn.execute(
        f"""SELECT s.*, a.name AS authority_name FROM scheme s JOIN authority a USING (authority_id)
            WHERE s.trade IN ({ph_t}) AND s.authority_id IN ({ph_a}) ORDER BY s.scheme_id""",
        (*t.scheme_trades, *local_auth)).fetchall()
    # 担当機関が合っていても、制度の側がこの市区町村を対象にしていないことがある
    local: list[sqlite3.Row] = []
    out_of_scope: list[sqlite3.Row] = []
    for s in found:
        (local if scheme_covers(loc, s["excluded_municipalities"]) else out_of_scope).append(s)
    if not local:
        result.message = f"{loc.municipality}の{t.label}の制度情報はまだ整備されていません。"
        if out_of_scope:
            names = "・".join(dict.fromkeys(s["license_name"] for s in out_of_scope))
            result.message += f"（{names}は{loc.municipality}を対象外としています）"
        return result
    for s in out_of_scope:
        result.warnings.append(f"{s['license_name']}は{loc.municipality}を対象外としているため、"
                               "候補から外しました。")

    # 3. 検索システム型は照会時に問い合わせる（該当なし＝空リスト、問い合わせ失敗＝None）
    live: dict[str, list[dict] | None] = {}
    failures: dict[str, str | None] = {}
    if search_client:
        for s in local:
            if s["publication_format"] == "search_system":
                found = search_client.lookup(s, license_no, name)
                live[s["scheme_id"]] = found
                if found is None:
                    failures[s["scheme_id"]] = getattr(search_client, "last_error", None)

    for s in local:
        fetched = roster_fetched_at(conn, s["scheme_id"])
        result.schemes.append(SchemeInfo(
            authority=s["authority_name"], license_name=s["license_name"],
            publication_format=s["publication_format"],
            completeness=_completeness(conn, s, live),
            contact=s["contact"], source_url=s["source_url"], fetched_at=fetched and fetched.isoformat()))
        if s["scheme_id"] in failures:
            reason = failures[s["scheme_id"]]
            result.warnings.append(f"{s['license_name']}は検索システムに問い合わせできませんでした"
                                   + (f"（{reason}）" if reason else "") + "。")
        if is_stale(fetched, today):
            result.warnings.append(f"{s['license_name']}の名簿は取得から90日を超えています。"
                                   f"情報が古い可能性があります（取得日: {fetched.isoformat()}）。")
        for n in conn.execute("SELECT severity, body FROM scheme_note WHERE scheme_id = ? "
                              "ORDER BY severity = 'important' DESC, note_id", (s["scheme_id"],)):
            result.notes.append({"severity": n["severity"], "body": n["body"]})

    # 番号 → 商号 の順で該当機関の名簿を探し、検索システムで見つかった行を足す
    hits = list(_find(conn, [s["scheme_id"] for s in local], license_no, name, business_address))
    for s in local:
        for f in live.get(s["scheme_id"]) or []:
            hits.append({"authority_name": s["authority_name"], "license_name": s["license_name"],
                         "required_sub_scope": s["required_sub_scope"], "source_url": s["source_url"],
                         "fetched_at": today.isoformat(), "needs_review": 0, "address": None,
                         "sub_scope": None, "valid_until": None, "status": "active", **f})
    if any(r["needs_review"] for r in hits):
        result.warnings.append("検品（目視確認）が済んでいない抽出行を含みます。"
                               "根拠の行に「未検品」と付けた行は、原本と一致しているか確かめていません。")

    # 4. 有効期間とステータス
    valid = [r for r in hits if _is_valid(r, today)]
    in_scope = [r for r in valid if _scope_ok(r)]
    if in_scope:
        r = in_scope[0]
        result.verdict = AUTHORIZED
        until = _wareki(r["valid_until"])
        result.message = (f"{r['authority_name']}の{r['license_name']}あり"
                          + (f"（有効期限：{until}）" if until else ""))
        result.evidence = [_evidence(x) for x in in_scope]
        return result
    if valid:
        r = valid[0]
        result.verdict = NOT_FOUND
        result.message = (f"{r['authority_name']}の{r['license_name']}はあるが、"
                          f"範囲に「{r['required_sub_scope']}」が確認できない。担当課に確認を推奨")
        result.evidence = [_evidence(x) for x in valid]
        return result
    if hits:
        r = max(hits, key=lambda x: x["valid_until"] or "")
        result.verdict = EXPIRED
        if r["status"] == "revoked":
            result.message = f"{r['authority_name']}の{r['license_name']}は取消・廃止されている"
        else:
            result.message = f"{r['authority_name']}の{r['license_name']}は" + \
                (f"{_wareki(r['valid_until'])}に有効期限切れ" if r["valid_until"] else "有効期間外")
        result.evidence = [_evidence(x) for x in hits]
        return result

    # 5. 広告にある別の機関・別の対象地域の番号・名前（対象外として外した制度もここに入る）
    local_ids = [s["scheme_id"] for s in local]
    ph_l = ",".join("?" * len(local_ids))
    other_ids = [x[0] for x in conn.execute(
        f"SELECT scheme_id FROM scheme WHERE trade IN ({ph_t}) AND scheme_id NOT IN ({ph_l})",
        (*t.scheme_trades, *local_ids))]
    others = [r for r in _find(conn, other_ids, license_no, name, business_address) if _is_valid(r, today)]
    local_names = "・".join(dict.fromkeys(s["authority_name"] for s in local))
    if others:
        result.verdict = OTHER_ONLY
        if {r["authority_name"] for r in others} & {s["authority_name"] for s in local}:
            # 同じ機関の、対象地域が違う制度。機関名では区別できないので制度名で出す
            result.message = ("・".join(dict.fromkeys(r["license_name"] for r in others)) + "はあるが、"
                              + "・".join(dict.fromkeys(s["license_name"] for s in local)) + "は確認できない")
        else:
            other_names = "・".join(dict.fromkeys(r["authority_name"] for r in others))
            result.message = f"{other_names}の公認はあるが、{local_names}の公認は確認できない"
        result.evidence = [_evidence(x) for x in others]
        if any(s.completeness != "high" for s in result.schemes):
            result.warnings.append(f"{local_names}の名簿は完全ではないため、未掲載でも公認がないとは限りません。")
        return result

    # 見つからない: 名簿の完全性が高いときだけ「確認できず」
    if all(s.completeness == "high" for s in result.schemes):
        result.verdict = NOT_FOUND
        result.message = "名簿に見当たらない。担当課に確認を推奨"
    else:
        result.verdict = UNDETERMINED
        failed_names = {s["license_name"] for s in local if s["scheme_id"] in failures}
        lacking = [s for s in result.schemes if s.completeness != "high"]
        reasons = []
        for s in lacking:
            if s.publication_format == "notice_diff":
                reasons.append(f"{s.license_name}は告示の差分しか公開されておらず、名簿が不完全")
            elif s.publication_format == "search_system":
                reasons.append(f"{s.license_name}は検索システムに問い合わせできなかった"
                               if s.license_name in failed_names
                               else f"{s.license_name}は検索システムでの確認が必要")
            elif s.publication_format in FULL_LIST_FORMATS:
                reasons.append(f"{s.license_name}の名簿が未取得")
            elif s.publication_format == "phone_only":
                reasons.append(f"{s.license_name}の名簿が公開されていない")
            else:
                reasons.append(f"{s.license_name}の公開形式が未調査")
        contacts = "・".join(dict.fromkeys(s.contact for s in lacking if s.contact))
        result.message = (f"判定できません（{'／'.join(reasons)}）。"
                          + (f"担当課（{contacts}）へ確認してください" if contacts else ""))
    return result
