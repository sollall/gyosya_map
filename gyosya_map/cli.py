"""コマンドライン。

  python -m gyosya_map init                                  # DB作成＋制度・管轄データ投入
  python -m gyosya_map import  --scheme ID --file 名簿.xlsx   # 全件名簿（xlsx / pdf / csv）
  python -m gyosya_map notice  --scheme ID --file 告示.pdf --date 2026-09-30
  python -m gyosya_map preview --scheme ID --file 名簿.xlsx   # 取り込まずに読み取り結果を確認
  python -m gyosya_map check   --address 住所 --trade water --license 123
  python -m gyosya_map expire | monitor | schemes
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import db, freshness
from .ingest import ParserConfig, apply_notice_events, import_full_list, load_for_scheme, parse_file, store_document
from .jurisdiction import AddressError
from .matcher import check
from .normalize import parse_date


def _config(conn, a) -> ParserConfig:
    """制度の読み込み設定。--parser-config で一時的に差し替えられる（seed に書く前の試行用）。"""
    if a.parser_config:
        return ParserConfig.from_json(a.parser_config)
    return load_for_scheme(conn, a.scheme)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gyosya_map", description="自治体別 業者公認確認データベース")
    p.add_argument("--db", default="gyosya.db")
    p.add_argument("--raw-dir", default="data/raw", help="原本の保存先")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="スキーマ作成と制度・管轄データの投入")

    for name, help_ in (("import", "全件名簿の取り込み"), ("notice", "告示（差分）の取り込み"),
                        ("preview", "取り込まずに読み取り結果を表示")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--scheme", required=True)
        sp.add_argument("--file", required=True, type=Path)
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

    sub.add_parser("expire", help="有効期限切れの行を expired にする")
    sub.add_parser("monitor", help="掲載ページの変化を検知して再取得キューに入れる")
    sub.add_parser("schemes", help="制度の一覧")

    a = p.parse_args(argv)
    conn = db.connect(a.db)

    if a.cmd == "init":
        db.init_schema(conn)
        db.load_seed(conn)
        n = conn.execute("SELECT count(*) FROM scheme").fetchone()[0]
        m = conn.execute("SELECT count(*) FROM municipality").fetchone()[0]
        print(f"{a.db} を初期化しました（市区町村 {m} 件、制度 {n} 件）")
    elif a.cmd == "preview":
        records = parse_file(a.file, _config(conn, a), notice=a.notice)
        print(f"{len(records)} 件")
        for r in records[:a.limit]:
            print(json.dumps(r, ensure_ascii=False, default=str))
    elif a.cmd in ("import", "notice"):
        fetched = parse_date(a.fetched_at) if a.fetched_at else dt.date.today()
        records = parse_file(a.file, _config(conn, a), notice=a.cmd == "notice")
        published = parse_date(a.date) if a.cmd == "notice" else None
        doc_id, new = store_document(conn, a.scheme, a.file, kind="notice" if published else "full_list",
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
    elif a.cmd == "check":
        try:
            r = check(conn, address=a.address, trade=a.trade, name=a.name, license_no=a.license,
                      business_address=a.business_address)
        except (AddressError, KeyError, ValueError) as e:
            print(e, file=sys.stderr)
            return 2
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
