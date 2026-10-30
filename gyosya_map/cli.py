"""コマンドライン。

  uv run gyosya-map init                                  # DB作成＋制度・管轄データ投入
  uv run gyosya-map import  --scheme ID --file 名簿.xlsx   # 全件名簿（xlsx / pdf / csv）
  uv run gyosya-map notice  --scheme ID --file 告示.pdf --date 2026-09-30
  uv run gyosya-map preview --scheme ID --file 名簿.xlsx   # 取り込まずに読み取り結果を確認
  uv run gyosya-map fetch   --scheme ID --import             # 掲載ページから名簿をダウンロードして取り込む
  uv run gyosya-map check   --address 住所 --trade water --license 123
  uv run gyosya-map geo-update                               # 地図用の町丁目の座標を取り込む（初回のみ）
  uv run gyosya-map map --out data/map.html                  # 名簿の業者を地図にプロット
  uv run gyosya-map expire | monitor | schemes
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import db, freshness
from .ingest import ParserConfig, apply_notice_events, import_full_list, load_for_scheme, parse_file, store_document
from .matcher import check
from .normalize import parse_date


def _config(conn, a) -> ParserConfig:
    """制度の読み込み設定。--parser-config で一時的に差し替えられる（seed に書く前の試行用）。"""
    if a.parser_config:
        return ParserConfig.from_json(a.parser_config)
    return load_for_scheme(conn, a.scheme)


def _prepare_file(a) -> Path:
    """--file がURLならダウンロードする。原本の取得元URL（--url）も補う。"""
    if a.file.startswith(("http://", "https://")):
        from .ingest.fetch import download
        url = a.file
        a.url = getattr(a, "url", None) or url
        path = download(url)
        if path.suffix.lower() in (".html", ".htm", "") or path.read_bytes()[:15].lstrip().lower().startswith(
                (b"<!doctype", b"<html")):
            raise SystemExit(f"{url} は名簿ファイルではなくWebページです。ページ内の名簿（Excel・PDF）のリンクを"
                             "--file に指定してください。")
        print(f"ダウンロードしました: {path}", file=sys.stderr)
        return path
    path = Path(a.file)
    if not path.exists():
        raise SystemExit(f"ファイルが見つかりません: {path}\n"
                         "名簿の掲載ページから Excel・PDF をダウンロードし、そのパスを --file に指定してください"
                         "（ファイルのURLを直接指定することもできます）。")
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gyosya-map", description="自治体別 業者公認確認データベース")
    p.add_argument("--db", default="gyosya.db")
    p.add_argument("--raw-dir", default="data/raw", help="原本の保存先")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="スキーマ作成と制度・管轄データの投入（古いDBは列を追加して更新）")
    sp.add_argument("--reset", action="store_true", help="DBを削除して作り直す（取り込んだ名簿も消える）")

    for name, help_ in (("import", "全件名簿の取り込み"), ("notice", "告示（差分）の取り込み"),
                        ("preview", "取り込まずに読み取り結果を表示")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--scheme", required=True)
        sp.add_argument("--file", required=True,
                        help="名簿ファイルのパス、またはファイルそのもののURL（ダウンロードして取り込む）")
        sp.add_argument("--parser-config", help="読み込み設定のJSON（制度の設定を上書き）")
        if name == "preview":
            sp.add_argument("--notice", action="store_true", help="告示として読む")
            sp.add_argument("--limit", type=int, default=5)
            continue
        sp.add_argument("--url", help="原本の取得元URL")
        sp.add_argument("--fetched-at", help="取得日（既定: 今日）")
        if name == "notice":
            sp.add_argument("--date", required=True, help="告示日")

    sp = sub.add_parser("check", help="照合")
    sp.add_argument("--address", required=True, help="工事・依頼をする場所の住所")
    sp.add_argument("--trade", required=True, help="業種（water, sewer, waste, septic, electrical, demolition, reform …）")
    sp.add_argument("--name", help="業者名")
    sp.add_argument("--license", help="許可番号・指定番号")
    sp.add_argument("--business-address", help="業者の所在地（同名業者の絞り込み）")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("fetch", help="掲載ページから名簿ファイルを探してダウンロードする")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--scheme", help="制度ID")
    g.add_argument("--all", action="store_true", help="掲載ページが分かっている全件名簿型の制度すべて")
    sp.add_argument("--list", action="store_true", help="ダウンロードせず、ページ内のファイルのリンクを一覧する")
    sp.add_argument("--import", dest="do_import", action="store_true", help="ダウンロード後に取り込む（全件名簿のみ）")
    sp.add_argument("--dest", default="data/downloads", help="保存先")

    sp = sub.add_parser("geo-update", help="地図用に町丁目の座標（Geolonia 住所データ）を取り込む")
    sp.add_argument("--file", help="ダウンロード済みの latest.csv（省略時はダウンロードする。約50MB）")

    sp = sub.add_parser("map", help="名簿の業者を地図にプロットしたHTMLを書き出す")
    sp.add_argument("--out", default="data/map.html")
    sp.add_argument("--scheme", action="append", help="制度ID（複数指定可）")
    sp.add_argument("--trade", help="業種で絞る")
    sp.add_argument("--status", choices=["active", "expired", "revoked", "unknown"], help="状態で絞る")
    sp.add_argument("--title", default="業者公認マップ")

    sub.add_parser("expire", help="有効期限切れの行を expired にする")
    sub.add_parser("monitor", help="掲載ページの変化を検知して再取得キューに入れる")
    sub.add_parser("schemes", help="制度の一覧")

    a = p.parse_args(argv)
    if a.cmd == "init" and a.reset:
        Path(a.db).unlink(missing_ok=True)
    conn = db.connect(a.db)
    try:
        return _run(a, conn)
    except (KeyError, ValueError, OSError, db.SchemaError) as e:
        print(e.args[0] if e.args else e, file=sys.stderr)
        return 2


def _run(a, conn) -> int:

    if a.cmd == "init":
        added = db.init_schema(conn)
        if added:
            print(f"古いDBに列を追加しました: {', '.join(added)}")
        db.load_seed(conn)
        n = conn.execute("SELECT count(*) FROM scheme").fetchone()[0]
        m = conn.execute("SELECT count(*) FROM municipality").fetchone()[0]
        print(f"{a.db} を初期化しました（市区町村 {m} 件、制度 {n} 件）")
    elif a.cmd == "preview":
        records = parse_file(_prepare_file(a), _config(conn, a), notice=a.notice)
        print(f"{len(records)} 件")
        for r in records[:a.limit]:
            print(json.dumps(r, ensure_ascii=False, default=str))
    elif a.cmd in ("import", "notice"):
        fetched = parse_date(a.fetched_at) if a.fetched_at else dt.date.today()
        path = _prepare_file(a)
        records = parse_file(path, _config(conn, a), notice=a.cmd == "notice")
        if not records:
            # 0件で全件名簿を取り込むと、登録済みの行が全部「名簿から消えた」扱いになる
            raise SystemExit("名簿の行を1件も読み取れませんでした。取り込みは中止しました。\n"
                             "preview と --parser-config で見出しの行や列名を指定して試してください。")
        published = parse_date(a.date) if a.cmd == "notice" else None
        doc_id, new = store_document(conn, a.scheme, path, kind="notice" if published else "full_list",
                                     url=a.url, published_on=published, fetched_at=fetched, raw_dir=a.raw_dir)
        if not new:
            print("同じ原本は取り込み済みです", file=sys.stderr)
        if a.cmd == "import":
            stats = import_full_list(conn, a.scheme, records, doc_id=doc_id, fetched_at=fetched)
        else:
            stats = apply_notice_events(conn, a.scheme, records, doc_id=doc_id, notice_date=published,
                                        fetched_at=fetched)
        conn.commit()
        print(json.dumps({"records": len(records), **stats}, ensure_ascii=False))
    elif a.cmd == "fetch":
        return _fetch(conn, a)
    elif a.cmd == "geo-update":
        from . import geo
        path = Path(a.file) if a.file else _download_large(geo.LATEST_CSV, Path("data/downloads/geolonia/latest.csv"))
        print(f"町丁目 {geo.load_towns(conn, path)} 件の座標を取り込みました")
    elif a.cmd == "map":
        from . import geo, mapview
        if not geo.has_towns(conn):
            raise SystemExit("町丁目の座標がまだありません。先に `uv run gyosya-map geo-update` を実行してください。")
        points, stats = mapview.collect(conn, schemes=a.scheme, trade=a.trade, status=a.status)
        out = mapview.write_map(points, stats, a.out, a.title)
        print(f"{out} に {stats.plotted} 件をプロットしました（所在地なし {stats.no_address} 件、"
              f"位置が分からない住所 {len(stats.not_found)} 件）")
        for addr in stats.not_found[:10]:
            print(f"  位置が分からない: {addr}", file=sys.stderr)
    elif a.cmd == "check":
        r = check(conn, address=a.address, trade=a.trade, name=a.name, license_no=a.license,
                  business_address=a.business_address)
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2) if a.json else r.render())
    elif a.cmd == "expire":
        print(f"{freshness.expire(conn)} 件を期限切れにしました")
    elif a.cmd == "monitor":
        from .ingest.fetch import check_pages
        for r in check_pages(conn):
            print(r["result"], r["url"], r.get("error", ""))
    elif a.cmd == "schemes":
        for s in conn.execute("SELECT s.scheme_id, s.trade, s.license_name, s.publication_format, r.fetched_at "
                              "FROM scheme s LEFT JOIN roster_fetch r USING (scheme_id) ORDER BY s.trade"):
            print("\t".join(str(x or "—") for x in s))
    return 0


def _fetch(conn, a) -> int:
    from .ingest.download import download_scheme, downloadable_schemes, list_links

    schemes = downloadable_schemes(conn) if a.all else [a.scheme]
    failed = 0
    for scheme_id in schemes:
        if a.list:
            links = list_links(conn, scheme_id)
            print(f"{scheme_id}: {len(links)} 件")
            for link in links:
                print(f"  {link.text or '—'}\t{link.url}")
            continue
        try:
            files = download_scheme(conn, scheme_id, a.dest)
        except (OSError, ValueError) as e:
            # --all では1つの失敗で止めず、残りを続ける
            print(f"{scheme_id}: 失敗: {e}", file=sys.stderr)
            failed += 1
            if not a.all:
                return 2
            continue
        fmt = conn.execute("SELECT publication_format FROM scheme WHERE scheme_id = ?", (scheme_id,)).fetchone()[0]
        for f in files:
            print(f"{scheme_id}: ダウンロードしました {f.path}（{f.url}）")
            if not a.do_import:
                continue
            if fmt not in ("full_list_excel", "full_list_pdf"):
                print(f"  告示などの差分は日付が必要なので、notice --scheme {scheme_id} --file {f.path} --date 告示日"
                      " で取り込んでください")
                continue
            records = parse_file(f.path, load_for_scheme(conn, scheme_id))
            if not records:
                print(f"  名簿の行を1件も読み取れませんでした。取り込みは中止しました"
                      f"（preview --scheme {scheme_id} --file {f.path} で確かめてください）", file=sys.stderr)
                failed += 1
                continue
            doc_id, new = store_document(conn, scheme_id, f.path, kind="full_list", url=f.url, raw_dir=a.raw_dir)
            if not new:
                print("  前回と同じファイルなので取り込みは省略しました")
                continue
            stats = import_full_list(conn, scheme_id, records, doc_id=doc_id)
            print("  " + json.dumps({"records": len(records), **stats}, ensure_ascii=False))
    return 1 if failed else 0


def _download_large(url: str, dest: Path) -> Path:
    """大きなファイルを少しずつ保存する。"""
    import urllib.request

    from .ingest.fetch import USER_AGENT
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"ダウンロード中: {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as res, open(tmp, "wb") as f:
        while chunk := res.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest
