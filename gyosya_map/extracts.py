"""エージェントが作った正規化済み抽出データ（`data_extracts/`）の検証と取り込み。

このモジュールには自治体ごとの書式解釈をいっさい書かない。原本（PDF / Excel / CSV）の読み取りは
収集担当のエージェントが行い、正規化済みの `rows.jsonl` と `manifest.json` をリポジトリに
コミットする。ここでやるのは、その形式を検証して licensee に入れるだけの決定的な処理。

形式の説明と具体例は `doc/extract-format.md`。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .ingest.roster import apply_notice_events, import_full_list, register_document
from .normalize import normalize_license_no

ROWS_FILE = "rows.jsonl"
MANIFEST_FILE = "manifest.json"
EXTRACTS_DIR = "data_extracts"

KINDS = ("full_list", "notice")
STATUSES = ("active", "expired", "revoked", "unknown")
EVENTS = ("new", "renew", "change", "revoke")
REVIEW_RESULTS = ("passed", "failed")

# rows.jsonl の項目（licensee テーブルに対応する）。これ以外のキーがあればエラーにする。
ROW_TEXT_FIELDS = ("license_no", "name", "address", "representative", "sub_scope")
ROW_DATE_FIELDS = ("valid_from", "valid_until")
ROW_REQUIRED_FIELDS = ("license_no", "name")
# 照合キーと検品状態はインポータが決定的に作る。抽出側に書かせない。
ROW_GENERATED_FIELDS = ("license_no_key", "name_key", "needs_review", "licensee_id", "source_doc", "fetched_at")

MANIFEST_FIELDS = ("scheme_id", "source_url", "source_path", "source_sha256", "fetched_at", "kind", "published_on",
                   "extracted_by", "extracted_at", "source_row_count", "terms_of_use", "notes", "review")
# null を許さない項目（他は「原本に記載がない・分からない」を null で表せる）
MANIFEST_REQUIRED_FIELDS = ("scheme_id", "source_path", "source_sha256", "fetched_at", "kind", "extracted_by",
                            "extracted_at", "source_row_count")
REVIEW_FIELDS = ("reviewed_by", "reviewed_at", "result", "method", "sampled_rows")


class ExtractError(ValueError):
    """抽出データが形式に合っていない。problems に「どこの何が不正か」を1件1行で持つ。"""

    def __init__(self, where: str | Path, problems: list[str]):
        self.problems = problems
        shown = problems[:30]
        rest = "" if len(problems) == len(shown) else f"\n  …ほか {len(problems) - len(shown)} 件"
        super().__init__(f"{where} の抽出データは取り込めません（{len(problems)} 件の問題）:\n"
                         + "\n".join(f"  {p}" for p in shown) + rest
                         + f"\n形式は doc/extract-format.md を参照してください。")


@dataclass
class Extract:
    """検証済みの抽出データ1件（1ディレクトリ）。"""

    dir: Path
    manifest: dict
    rows: list[dict]                                  # needs_review を付けた取り込み用の行
    warnings: list[str] = field(default_factory=list)

    @property
    def scheme_id(self) -> str:
        return self.manifest["scheme_id"]

    @property
    def kind(self) -> str:
        return self.manifest["kind"]

    @property
    def reviewed(self) -> bool:
        """検品に通っているか（review.result == "passed" のときだけ True）。"""
        review = self.manifest.get("review")
        return bool(review) and review.get("result") == "passed"


# ---- 値の検証 -------------------------------------------------------------------

def _date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _check_text(problems: list[str], where: str, obj: dict, name: str, *, required: bool = False) -> None:
    if name not in obj:
        if required:
            problems.append(f"{where}: {name} がありません（必須）")
        return
    value = obj[name]
    if value is None:
        if required:
            problems.append(f"{where}: {name} が null です（{name} は原本から読めた値が必須）")
        return
    if not isinstance(value, str):
        problems.append(f"{where}: {name} は文字列にしてください（{type(value).__name__}: {value!r}）")
    elif not value.strip():
        problems.append(f"{where}: {name} が空文字です（記載がない・読めなかった項目は null にする）")


def _check_date(problems: list[str], where: str, obj: dict, name: str, *, required: bool = False,
                with_time: bool = False) -> None:
    if name not in obj:
        if required:
            problems.append(f"{where}: {name} がありません（必須）")
        return
    value = obj[name]
    if value is None:
        if required:
            problems.append(f"{where}: {name} が null です（{name} は必須）")
        return
    if not isinstance(value, str):
        problems.append(f"{where}: {name} は文字列（ISO 8601）にしてください（{type(value).__name__}: {value!r}）")
        return
    if with_time:
        try:
            dt.datetime.fromisoformat(value)
            return
        except ValueError:
            pass
        problems.append(f"{where}: {name} は 2026-11-05 または 2026-11-05T10:00:00 の形にしてください"
                        f"（和暦・日本語の日付は抽出側で変換する）: {value!r}")
        return
    if _date(value) is None:
        problems.append(f"{where}: {name} は 2026-11-05 の形（ISO 8601）にしてください"
                        f"（和暦・日本語の日付は抽出側で変換する）: {value!r}")


def _check_choice(problems: list[str], where: str, obj: dict, name: str, choices: tuple[str, ...], *,
                  required: bool = False) -> None:
    value = obj.get(name)
    if value is None:
        if required:
            problems.append(f"{where}: {name} がありません（{' / '.join(choices)} のどれか）")
        return
    if value not in choices:
        problems.append(f"{where}: {name} は {' / '.join(choices)} のどれかにしてください: {value!r}")


# ---- manifest.json --------------------------------------------------------------

def _check_manifest(problems: list[str], manifest: dict) -> None:
    where = MANIFEST_FILE
    if unknown := sorted(set(manifest) - set(MANIFEST_FIELDS)):
        problems.append(f"{where}: 知らない項目があります: {', '.join(unknown)}"
                        f"（使えるのは {', '.join(MANIFEST_FIELDS)}）")
    for name in MANIFEST_FIELDS:
        if name not in manifest:
            problems.append(f"{where}: {name} がありません（分からない項目も null で必ず書く）")

    _check_text(problems, where, manifest, "scheme_id", required=True)
    _check_text(problems, where, manifest, "source_path", required=True)
    _check_text(problems, where, manifest, "source_url")
    _check_text(problems, where, manifest, "extracted_by", required=True)
    _check_text(problems, where, manifest, "terms_of_use")
    _check_text(problems, where, manifest, "notes")
    _check_date(problems, where, manifest, "fetched_at", required=True)
    _check_date(problems, where, manifest, "extracted_at", required=True, with_time=True)
    _check_date(problems, where, manifest, "published_on", required=manifest.get("kind") == "notice")
    _check_choice(problems, where, manifest, "kind", KINDS, required=True)

    sha = manifest.get("source_sha256")
    if isinstance(sha, str) and len(sha) == 64 and not set(sha.lower()) - set("0123456789abcdef"):
        pass
    elif sha is None:
        problems.append(f"{where}: source_sha256 が null です（原本の sha256 は必須）")
    else:
        problems.append(f"{where}: source_sha256 は sha256 の16進64桁にしてください: {sha!r}")

    count = manifest.get("source_row_count")
    if count is None:
        problems.append(f"{where}: source_row_count が null です"
                        f"（原本側の総件数。数えられないときは抽出せず収集担当で解決する）")
    elif not isinstance(count, int) or isinstance(count, bool) or count < 0:
        problems.append(f"{where}: source_row_count は0以上の整数にしてください: {count!r}")


def _check_review(problems: list[str], manifest: dict, row_count: int) -> None:
    review = manifest.get("review")
    if review is None:
        return
    where = f"{MANIFEST_FILE} review"
    if not isinstance(review, dict):
        problems.append(f"{where}: review はオブジェクトか null にしてください: {review!r}")
        return
    if unknown := sorted(set(review) - set(REVIEW_FIELDS)):
        problems.append(f"{where}: 知らない項目があります: {', '.join(unknown)}")
    _check_text(problems, where, review, "reviewed_by", required=True)
    _check_text(problems, where, review, "method", required=True)
    _check_date(problems, where, review, "reviewed_at", required=True, with_time=True)
    _check_choice(problems, where, review, "result", REVIEW_RESULTS, required=True)
    sampled = review.get("sampled_rows")
    if not isinstance(sampled, list) or not sampled:
        problems.append(f"{where}: sampled_rows に目視で確かめた行（rows.jsonl の行番号）を書いてください: "
                        f"{sampled!r}")
        return
    for n in sampled:
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= max(row_count, 1):
            problems.append(f"{where}: sampled_rows は rows.jsonl の行番号（1〜{row_count}）にしてください: {n!r}")


# ---- rows.jsonl -----------------------------------------------------------------

def _check_row(problems: list[str], where: str, row: dict, kind: str) -> None:
    allowed = set(ROW_TEXT_FIELDS) | set(ROW_DATE_FIELDS) | {"status" if kind == "full_list" else "event"}
    for name in sorted(set(row) & set(ROW_GENERATED_FIELDS)):
        problems.append(f"{where}: {name} は書かないでください（インポータが normalize.py で決定的に作ります）")
    if unknown := sorted(set(row) - allowed - set(ROW_GENERATED_FIELDS)):
        problems.append(f"{where}: 知らない項目があります: {', '.join(unknown)}"
                        f"（{kind} で使えるのは {', '.join(sorted(allowed))}）")
    for name in ROW_TEXT_FIELDS:
        _check_text(problems, where, row, name, required=name in ROW_REQUIRED_FIELDS)
    for name in ROW_DATE_FIELDS:
        _check_date(problems, where, row, name)
    if kind == "full_list":
        _check_choice(problems, where, row, "status", STATUSES)
    else:
        _check_choice(problems, where, row, "event", EVENTS, required=True)
    vf, vu = _date(row.get("valid_from") or ""), _date(row.get("valid_until") or "")
    if vf and vu and vu < vf:
        problems.append(f"{where}: valid_until（{row['valid_until']}）が valid_from（{row['valid_from']}）"
                        "より前になっています")


def _read_rows(problems: list[str], path: Path, kind: str) -> list[dict]:
    rows: list[dict] = []
    first_line: dict[str, int] = {}
    for lineno, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"{ROWS_FILE}:{lineno}"
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"{where}: JSONとして読めません（1行に1件のJSONオブジェクト）: {e.msg}")
            continue
        if not isinstance(row, dict):
            problems.append(f"{where}: 1行に1件のJSONオブジェクトを書いてください: {row!r}")
            continue
        _check_row(problems, where, row, kind)
        rows.append(row)
        no = row.get("license_no")
        if isinstance(no, str) and no.strip():
            key = normalize_license_no(no)
            if key in first_line:
                problems.append(f"{where}: license_no が {first_line[key]} 行目と同じ業者になります"
                                f"（照合キー {key}）。どちらが正しいか収集担当で決めてください")
            else:
                first_line[key] = lineno
    return rows


# ---- 読み込み -------------------------------------------------------------------

def load(directory: str | Path, *, scheme_id: str | None = None, allow_count_mismatch: bool = False) -> Extract:
    """抽出データを1件読んで検証する。1つでも問題があれば ExtractError（取り込まない）。"""
    directory = Path(directory)
    problems: list[str] = []
    warnings: list[str] = []
    manifest_path, rows_path = directory / MANIFEST_FILE, directory / ROWS_FILE
    for path in (manifest_path, rows_path):
        if not path.exists():
            problems.append(f"{path.name} がありません（{directory}）")
    if problems:
        raise ExtractError(directory, problems)

    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except json.JSONDecodeError as e:
        raise ExtractError(directory, [f"{MANIFEST_FILE}: JSONとして読めません: {e}"]) from e
    if not isinstance(manifest, dict):
        raise ExtractError(directory, [f"{MANIFEST_FILE}: JSONオブジェクトを書いてください"])

    _check_manifest(problems, manifest)
    kind = manifest.get("kind") if manifest.get("kind") in KINDS else "full_list"
    rows = _read_rows(problems, rows_path, kind)
    _check_review(problems, manifest, len(rows))
    if not rows:
        # 0件の全件名簿を取り込むと、登録済みの行が全部「名簿から消えた」扱いになる
        problems.append(f"{ROWS_FILE} に行がありません。原本を読めていない可能性があるので取り込みません")

    # ディレクトリの置き場所（data_extracts/<scheme_id>/<YYYY-MM-DD>/）も形式の一部
    if _date(directory.name) is None:
        problems.append(f"ディレクトリ名は抽出した日（2026-11-05）にしてください: {directory.name}")
    if isinstance(manifest.get("scheme_id"), str) and directory.parent.name != manifest["scheme_id"]:
        problems.append(f"親ディレクトリ名（{directory.parent.name}）が manifest の scheme_id "
                        f"（{manifest['scheme_id']}）と違います")
    if scheme_id and manifest.get("scheme_id") != scheme_id:
        problems.append(f"--scheme {scheme_id} と manifest の scheme_id（{manifest.get('scheme_id')}）が違います")

    count = manifest.get("source_row_count")
    if isinstance(count, int) and not isinstance(count, bool) and count != len(rows):
        message = (f"原本の件数（source_row_count {count}）と {ROWS_FILE} の行数（{len(rows)}）が違います。"
                   "読み落とした行がないか確かめてください")
        (warnings if allow_count_mismatch else problems).append(message)

    _check_source_file(problems, warnings, directory, manifest)
    if problems:
        raise ExtractError(directory, problems)

    needs_review = 0 if (manifest.get("review") or {}).get("result") == "passed" else 1
    return Extract(dir=directory, manifest=manifest, warnings=warnings,
                   rows=[{**r, "needs_review": needs_review} for r in rows])


def _check_source_file(problems: list[str], warnings: list[str], directory: Path, manifest: dict) -> None:
    """原本が手元にあれば sha256 を確かめる。原本はリポジトリに入れないので、無いのは普通。"""
    path, sha = manifest.get("source_path"), manifest.get("source_sha256")
    if not isinstance(path, str) or not isinstance(sha, str):
        return
    for candidate in (Path(path), directory / path):
        if candidate.is_file():
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual != sha.lower():
                problems.append(f"{MANIFEST_FILE}: source_path（{candidate}）の sha256 が source_sha256 と"
                                f"違います（実際は {actual}）。原本が差し替わっているので抽出し直してください")
            return
    warnings.append(f"原本（{path}）が手元にないので sha256 は確かめていません")


def find_all(root: str | Path = EXTRACTS_DIR) -> list[Path]:
    """`<root>/<scheme_id>/<YYYY-MM-DD>/` を古い順に返す（告示は古い順に積む必要がある）。"""
    return sorted(p.parent for p in Path(root).glob(f"*/*/{MANIFEST_FILE}"))


# ---- 取り込み -------------------------------------------------------------------

def import_extract(conn, extract: Extract, *, today: dt.date | None = None) -> dict:
    """検証済みの抽出データを licensee に入れる。source_document と roster_fetch も更新する。"""
    manifest = extract.manifest
    scheme_id = extract.scheme_id
    if conn.execute("SELECT 1 FROM scheme WHERE scheme_id = ?", (scheme_id,)).fetchone() is None:
        raise KeyError(f"未登録の制度です: {scheme_id}。先に seed に制度を足してください")
    fetched_at = dt.date.fromisoformat(manifest["fetched_at"])
    published_on = _date(manifest.get("published_on") or "")
    doc_id, new_doc = register_document(conn, scheme_id, path=manifest["source_path"],
                                        sha256=manifest["source_sha256"].lower(), kind=extract.kind,
                                        url=manifest.get("source_url"), published_on=published_on,
                                        fetched_at=fetched_at)
    if extract.kind == "notice":
        stats = apply_notice_events(conn, scheme_id, extract.rows, doc_id=doc_id, notice_date=published_on,
                                    fetched_at=fetched_at, today=today)
    else:
        stats = import_full_list(conn, scheme_id, extract.rows, doc_id=doc_id, fetched_at=fetched_at, today=today)
    conn.commit()
    return {"scheme": scheme_id, "kind": extract.kind, "rows": len(extract.rows),
            "reviewed": extract.reviewed, "new_document": new_doc, **stats}
