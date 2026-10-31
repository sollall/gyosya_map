"""今日の時点で有効な登録業者を、業種ごとに色分けして地図にプロットしたHTMLを書き出す
（Leaflet＋国土地理院の地図）。

業者の所在地は町丁目の代表点に置く。期限切れ・取消・名簿から消えた業者は出さない。代表者名も出さない。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .geo import geocode
from .trades import TRADES, get_trade

# 業種（scheme.trade）ごとの表示名と色。色覚の差があっても区別しやすい組み合わせにする
TRADE_STYLE: dict[str, tuple[str, str]] = {
    "water": ("水道（給水）", "#1f77b4"),
    "sewer": ("下水・排水", "#17becf"),
    "waste": ("不用品回収・遺品整理", "#8c564b"),
    "septic": ("浄化槽", "#2ca02c"),
    "electrical": ("電気工事", "#ff7f0e"),
    "demolition": ("解体", "#d62728"),
    "construction": ("建設業許可（大きなリフォーム・解体）", "#9467bd"),
}


@dataclass
class MapStats:
    plotted: int = 0
    no_address: int = 0
    not_found: list[str] = field(default_factory=list)   # 座標にできなかった住所


def _scheme_trades(trade: str | None) -> tuple[str, ...]:
    return get_trade(trade).scheme_trades if trade else tuple(TRADE_STYLE)


def collect(conn: sqlite3.Connection, *, schemes: list[str] | None = None, trade: str | None = None,
            today: dt.date | None = None) -> tuple[list[dict], MapStats]:
    """今日の時点で有効な行（status が active で有効期限内）だけを集める。"""
    today = today or dt.date.today()
    trades = _scheme_trades(trade)
    where = [f"s.trade IN ({','.join('?' * len(trades))})", "l.status = 'active'",
             "(l.valid_until IS NULL OR l.valid_until >= ?)"]
    args: list = [*trades, today.isoformat()]
    if schemes:
        where.append(f"l.scheme_id IN ({','.join('?' * len(schemes))})")
        args += schemes
    rows = conn.execute(
        f"""SELECT l.license_no, l.name, l.address, l.valid_until, l.fetched_at, l.scheme_id,
                   s.license_name, s.trade, a.name AS authority, COALESCE(d.url, s.source_url) AS source_url
            FROM licensee l JOIN scheme s USING (scheme_id) JOIN authority a USING (authority_id)
            LEFT JOIN source_document d ON d.doc_id = l.source_doc
            WHERE {' AND '.join(where)} ORDER BY s.trade, l.scheme_id, l.license_no""", args).fetchall()
    stats = MapStats()
    cache: dict[str, object] = {}
    points = []
    for r in rows:
        if not r["address"]:
            stats.no_address += 1
            continue
        if r["address"] not in cache:
            cache[r["address"]] = geocode(conn, r["address"])
        p = cache[r["address"]]
        if p is None:
            stats.not_found.append(r["address"])
            continue
        points.append({"lat": round(p.lat, 6), "lng": round(p.lng, 6), "precision": p.precision,
                       "trade": r["trade"], "license_name": r["license_name"], "authority": r["authority"],
                       "license_no": r["license_no"], "name": r["name"], "address": r["address"],
                       "valid_until": r["valid_until"], "fetched_at": r["fetched_at"],
                       "source_url": r["source_url"]})
    stats.plotted = len(points)
    return points, stats


def coverage(conn: sqlite3.Connection, trade: str | None = None, today: dt.date | None = None) -> list[dict]:
    """制度ごとの有効な業者の数と、名簿を取り込んだか。地図の「名簿がまだ無い業種」の表示に使う。"""
    today = today or dt.date.today()
    trades = _scheme_trades(trade)
    rows = conn.execute(
        f"""SELECT s.scheme_id, s.trade, s.license_name, s.publication_format, r.fetched_at,
                   (SELECT count(*) FROM licensee l WHERE l.scheme_id = s.scheme_id AND l.status = 'active'
                      AND (l.valid_until IS NULL OR l.valid_until >= ?)) AS active
            FROM scheme s LEFT JOIN roster_fetch r USING (scheme_id)
            WHERE s.trade IN ({','.join('?' * len(trades))}) ORDER BY s.trade, s.scheme_id""",
        (today.isoformat(), *trades)).fetchall()
    return [dict(r) for r in rows]


_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.Default.min.css">
<style>
  html, body { margin: 0; height: 100%; font-family: system-ui, sans-serif; }
  #map { position: absolute; inset: 0; }
  .panel { background: #fff; padding: 8px 10px; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,.3);
           font-size: 13px; line-height: 1.6; max-width: 300px; }
  .panel h1 { font-size: 14px; margin: 0 0 4px; }
  .panel .missing { color: #666; font-size: 12px; margin-top: 4px; max-height: 40vh; overflow: auto; }
  .panel .missing summary { cursor: pointer; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
  .popup dt { color: #666; font-size: 11px; } .popup dd { margin: 0 0 4px; }
</style>
</head>
<body>
<div id="map"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.min.js"></script>
<script>
const POINTS = __POINTS__;
const META = __META__;
const PRECISION = {town: "町丁目の代表点", area: "町名の中心（丁目不明）", municipality: "市区町村の中心（町名不明）"};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const style = t => META.trades[t] ?? [t, "#555"];

const map = L.map("map", {preferCanvas: true});
L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", {
  maxZoom: 18, attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html">国土地理院</a>'
}).addTo(map);
map.attributionControl.addAttribution(
  '住所座標: <a href="https://github.com/geolonia/japanese-addresses">Geolonia 住所データ</a> (CC BY 4.0)');

// 業種ごとのレイヤー。近い点はまとめ、まとまりの色はその業種の色
const layers = {};
for (const p of POINTS) {
  const [label, color] = style(p.trade);
  const g = layers[p.trade] ??= L.markerClusterGroup({maxClusterRadius: 40, iconCreateFunction: c =>
    L.divIcon({html: `<div style="background:${color};color:#fff;border-radius:50%;width:30px;height:30px;
      line-height:30px;text-align:center;font-weight:600;box-shadow:0 0 0 3px ${color}55">${c.getChildCount()}</div>`,
      className: "", iconSize: [30, 30]})});
  const m = L.circleMarker([p.lat, p.lng], {radius: 7, color: "#fff", weight: 1.5, fillColor: color, fillOpacity: .9});
  m.bindPopup(`<dl class="popup">
    <dt>${esc(p.authority)} ${esc(p.license_name)}</dt><dd><b>${esc(p.name)}</b></dd>
    <dt>番号</dt><dd>${esc(p.license_no)}</dd>
    <dt>有効期限</dt><dd>${esc(p.valid_until ?? "名簿に記載なし")}</dd>
    <dt>所在地</dt><dd>${esc(p.address)}<br><small>位置は${esc(PRECISION[p.precision])}</small></dd>
    <dt>出典</dt><dd>${p.source_url ? `<a href="${esc(p.source_url)}" target="_blank" rel="noopener">名簿</a>` : "—"}
      （取得日 ${esc(p.fetched_at ?? "—")}）</dd></dl>`);
  g.addLayer(m);
}
Object.values(layers).forEach(g => g.addTo(map));
if (POINTS.length) map.fitBounds(L.latLngBounds(POINTS.map(p => [p.lat, p.lng])).pad(0.1));
else map.setView([35.45, 139.45], 10);

L.control.layers(null, Object.fromEntries(Object.entries(layers).map(([t, g]) => {
  const [label, color] = style(t);
  return [`<span class="dot" style="background:${color}"></span>${esc(label)}（${g.getLayers().length}）`, g];
})), {collapsed: false}).addTo(map);

const info = L.control({position: "bottomleft"});
info.onAdd = () => {
  const d = L.DomUtil.create("div", "panel");
  const missing = META.coverage.filter(c => !c.active).map(c => `${esc(style(c.trade)[0])}：${esc(c.license_name)}` +
    (c.fetched_at ? "（有効な業者なし）" : "（名簿未取り込み）"));
  d.innerHTML = `<h1>${esc(META.title)}</h1>
    <small>${esc(META.today)} 時点で有効な登録業者 ${POINTS.length} 件` +
    (META.not_found ? ` · 位置が分からず表示できない ${META.not_found} 件` : "") + `</small>` +
    (missing.length ? `<details class="missing"><summary>地図に出ていない制度（${missing.length}）</summary>` +
      `${missing.join("<br>")}</details>` : "");
  return d;
};
info.addTo(map);
</script>
</body>
</html>
"""


def _js(value) -> str:
    # </script> を閉じさせない
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def write_map(points: list[dict], stats: MapStats, out: str | Path, title: str = "業者公認マップ",
              coverage_rows: list[dict] | None = None, today: dt.date | None = None) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"title": title, "today": (today or dt.date.today()).isoformat(), "not_found": len(stats.not_found),
            "trades": {k: list(v) for k, v in TRADE_STYLE.items()},
            "coverage": [{k: c[k] for k in ("trade", "license_name", "active", "fetched_at")}
                         for c in (coverage_rows or [])]}
    out.write_text(_TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__POINTS__", _js(points))
                   .replace("__META__", _js(meta)), "utf-8")
    return out


assert set(TRADE_STYLE) >= {st for t in TRADES.values() for st in t.scheme_trades}
