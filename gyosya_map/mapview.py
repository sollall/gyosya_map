"""名簿の業者を地図にプロットしたHTMLを書き出す（Leaflet＋国土地理院の地図）。

業者の所在地を町丁目の代表点に置く。代表者名は出さない。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .geo import geocode
from .normalize import parse_date
from .trades import TRADES

STATUS_LABEL = {"active": "有効", "expired": "期限切れ", "revoked": "取消・廃止", "unknown": "名簿から消えた"}


@dataclass
class MapStats:
    plotted: int = 0
    no_address: int = 0
    not_found: list[str] = field(default_factory=list)   # 座標にできなかった住所


def _trade_label(scheme_trade: str) -> str:
    for t in TRADES.values():
        if t.scheme_trades == (scheme_trade,):
            return t.label
    return {"construction": "建設業許可"}.get(scheme_trade, scheme_trade)


def collect(conn: sqlite3.Connection, *, schemes: list[str] | None = None, trade: str | None = None,
            status: str | None = None, today: dt.date | None = None) -> tuple[list[dict], MapStats]:
    today = today or dt.date.today()
    where, args = ["1 = 1"], []
    if schemes:
        where.append(f"l.scheme_id IN ({','.join('?' * len(schemes))})")
        args += schemes
    if trade:
        from .trades import get_trade
        st = get_trade(trade).scheme_trades
        where.append(f"s.trade IN ({','.join('?' * len(st))})")
        args += list(st)
    rows = conn.execute(
        f"""SELECT l.license_no, l.name, l.address, l.valid_until, l.status, l.fetched_at, l.scheme_id,
                   s.license_name, s.trade, a.name AS authority, COALESCE(d.url, s.source_url) AS source_url
            FROM licensee l JOIN scheme s USING (scheme_id) JOIN authority a USING (authority_id)
            LEFT JOIN source_document d ON d.doc_id = l.source_doc
            WHERE {' AND '.join(where)} ORDER BY l.scheme_id, l.license_no""", args).fetchall()
    stats = MapStats()
    cache: dict[str, object] = {}
    points = []
    for r in rows:
        st = r["status"]
        vu = parse_date(r["valid_until"])
        if st == "active" and vu and vu < today:
            st = "expired"
        if status and st != status:
            continue
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
                       "scheme": r["scheme_id"], "layer": f"{r['license_name']}",
                       "trade": _trade_label(r["trade"]), "authority": r["authority"],
                       "license_no": r["license_no"], "name": r["name"], "address": r["address"],
                       "valid_until": r["valid_until"], "status": st, "fetched_at": r["fetched_at"],
                       "source_url": r["source_url"]})
    stats.plotted = len(points)
    return points, stats


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
           font-size: 13px; line-height: 1.6; max-width: 260px; }
  .panel h1 { font-size: 14px; margin: 0 0 4px; }
  .key { display: inline-block; white-space: nowrap; margin-right: 8px; }
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
const COLORS = {active: "#2e7d32", expired: "#9e9e9e", revoked: "#c62828", unknown: "#ef6c00"};
const LABELS = __LABELS__;
const PRECISION = {town: "町丁目の代表点", area: "町名の中心（丁目不明）", municipality: "市区町村の中心（町名不明）"};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

const map = L.map("map", {preferCanvas: true});
L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", {
  maxZoom: 18, attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html">国土地理院</a>'
}).addTo(map);
map.attributionControl.addAttribution(
  '住所座標: <a href="https://github.com/geolonia/japanese-addresses">Geolonia 住所データ</a> (CC BY 4.0)');

const layers = {};
for (const p of POINTS) {
  const g = layers[p.layer] ??= L.markerClusterGroup({maxClusterRadius: 40});
  const m = L.circleMarker([p.lat, p.lng], {radius: 7, color: "#fff", weight: 1.5,
                                             fillColor: COLORS[p.status], fillOpacity: .9});
  m.bindPopup(`<dl class="popup">
    <dt>${esc(p.authority)} ${esc(p.layer)}</dt><dd><b>${esc(p.name)}</b></dd>
    <dt>番号</dt><dd>${esc(p.license_no)}</dd>
    <dt>状態</dt><dd><span class="dot" style="background:${COLORS[p.status]}"></span>${esc(LABELS[p.status])}
      ${p.valid_until ? "（有効期限 " + esc(p.valid_until) + "）" : ""}</dd>
    <dt>所在地</dt><dd>${esc(p.address)}<br><small>位置は${esc(PRECISION[p.precision])}</small></dd>
    <dt>出典</dt><dd>${p.source_url ? `<a href="${esc(p.source_url)}" target="_blank" rel="noopener">名簿</a>` : "—"}
      （取得日 ${esc(p.fetched_at ?? "—")}）</dd></dl>`);
  g.addLayer(m);
}
const all = Object.values(layers);
all.forEach(g => g.addTo(map));
if (POINTS.length) map.fitBounds(L.latLngBounds(POINTS.map(p => [p.lat, p.lng])).pad(0.1));
else map.setView([35.53, 139.63], 11);

L.control.layers(null, Object.fromEntries(Object.entries(layers).map(
  ([k, g]) => [`${esc(k)}（${g.getLayers().length}）`, g])), {collapsed: false}).addTo(map);

const info = L.control({position: "bottomleft"});
info.onAdd = () => {
  const d = L.DomUtil.create("div", "panel");
  d.innerHTML = `<h1>${esc(META.title)}</h1>` +
    Object.entries(COLORS).map(([k, c]) =>
      `<span class="key"><span class="dot" style="background:${c}"></span>${esc(LABELS[k])}</span>`).join("") +
    `<br><small>${POINTS.length} 件を表示 · 作成 ${esc(META.generated)}` +
    (META.not_found ? `<br>位置が分からず表示できなかった住所 ${META.not_found} 件` : "") + `</small>`;
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


def write_map(points: list[dict], stats: MapStats, out: str | Path, title: str = "業者公認マップ") -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"title": title, "generated": dt.date.today().isoformat(), "not_found": len(stats.not_found)}
    out.write_text(_TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__POINTS__", _js(points))
                   .replace("__META__", _js(meta)).replace("__LABELS__", _js(STATUS_LABEL)), "utf-8")
    return out
