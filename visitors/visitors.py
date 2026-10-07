#!/usr/bin/env python3
"""Count page views of the weather site from nginx access logs and map where the
visitors are. Meant to run from cron (every ~15 min).

- Reads /var/log/nginx/wx_access.log* (host-aware format, see nginx/wx_log.conf).
  Older, host-less /var/log/nginx/access.log* entries are used only for the period
  before the host-aware log began, so that history is approximate (it may include
  other sites sharing that log).
- A "view" is a successful GET of / or /index.html from a non-bot, public IP that
  behaves like a browser: it fetches page assets (css/js/images) within ASSET_WAIT_S,
  or was already seen doing so before (browsers with a warm cache skip the assets).
  This filters out the many scrapers that only fetch the HTML. Repeat views from
  the same IP within VISIT_GAP_S count as one visit.
- IPs are located offline with the DB-IP City Lite database; no IP leaves this
  machine. Raw IPs stay in the local SQLite database; the generated map contains
  only city-level places and counts, never IPs.
- Output: index.html in an unlinked directory under WEB_ROOT (name in SECRET_FILE),
  so it is served but unlinked (and marked noindex). Anyone with the URL can view it;
  it holds city-level places and counts only, never IPs.
"""
from __future__ import annotations

import gzip
import ipaddress
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import maxminddb

DATA_DIR = Path.home() / ".local/share/wx-visitors"
DB_PATH = DATA_DIR / "visitors.db"
GEO_DB = DATA_DIR / "db.mmdb"
WEB_ROOT = Path("/var/www/html/weewx")
SECRET_FILE = DATA_DIR / "map_dir"  # directory name under WEB_ROOT
LOG_DIR = Path("/var/log/nginx")
HOSTS = {"wx.nerpy.co", "wx.mahony.me"}
VIEW_PATHS = {"/", "/index.html"}
VISIT_GAP_S = 30 * 60
ASSET_WAIT_S = 30
ASSET_RE = re.compile(r"\.(css|js|png|ico|woff2?|svg)(\?|$)")

LINE_RE = re.compile(
    r'^(?:(?P<host>\S+) )?(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+)[^"]*" (?P<status>\d{3}) \S+ "[^"]*" "(?P<ua>[^"]*)"')
BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|curl|wget|python|scan|monitor|uptime|headless|fetch|"
    r"go-http|java/|okhttp|libwww|mastodon|pleroma|misskey|lemmy|preview|facebookexternal|"
    r"embed|check|probe|ping|http-client|censys|inspect|zgrab", re.I)


def open_log(path: Path):
    return gzip.open(path, "rt", errors="replace") if path.suffix == ".gz" else open(path, errors="replace")


def read_requests(pattern: str):
    """Yield (epoch, ip, host, is_page, is_asset) for GET requests in the logs matching
    pattern that could be page views or page-asset fetches by public, non-bot clients."""
    for path in sorted(LOG_DIR.glob(pattern)):
        try:
            with open_log(path) as f:
                for line in f:
                    m = LINE_RE.match(line)
                    if not m or m["method"] != "GET" or m["status"] not in ("200", "304"):
                        continue
                    is_page = m["path"] in VIEW_PATHS
                    is_asset = bool(ASSET_RE.search(m["path"]))
                    if not (is_page or is_asset) or BOT_RE.search(m["ua"]):
                        continue
                    try:
                        ts = datetime.strptime(m["ts"], "%d/%b/%Y:%H:%M:%S %z").timestamp()
                        addr = ipaddress.ip_address(m["ip"])
                    except ValueError:
                        continue
                    if addr.is_global:
                        yield ts, m["ip"], m["host"], is_page, is_asset
        except OSError as exc:
            print(f"Cannot read {path}: {exc}", file=sys.stderr)


def collect_events():
    """Return (sorted [(epoch, ip)] of browser-like page views, newest request epoch)."""
    new = [r for r in read_requests("wx_access.log*") if r[2] in HOSTS]
    cutoff = min((r[0] for r in new), default=float("inf"))
    reqs = sorted([r for r in read_requests("access.log*") if r[0] < cutoff] + new)
    newest = reqs[-1][0] if reqs else 0.0
    by_ip: dict[str, list] = {}
    for r in reqs:
        by_ip.setdefault(r[1], []).append(r)
    events = []
    for ip, rs in by_ip.items():
        confirmed = False
        for i, (ts, _, _, is_page, _) in enumerate(rs):
            if not is_page:
                continue
            if not confirmed:
                confirmed = any(r[4] and 0 <= r[0] - ts <= ASSET_WAIT_S for r in rs[i + 1:i + 60])
            if confirmed:
                events.append((ts, ip))
    return sorted(events), newest


def locate(reader, ip: str) -> dict:
    try:
        rec = reader.get(ip) or {}
    except ValueError:
        rec = {}
    loc = rec.get("location") or {}
    names = lambda k: ((rec.get(k) or {}).get("names") or {}).get("en", "")
    subs = rec.get("subdivisions") or []
    return {
        "city": names("city"),
        "region": ((subs[0].get("names") or {}).get("en", "") if subs else ""),
        "country": names("country"),
        "lat": loc.get("latitude"), "lon": loc.get("longitude"),
    }


def update_db(db: sqlite3.Connection) -> int:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS visits (
            ts REAL, ip TEXT, city TEXT, region TEXT, country TEXT, lat REAL, lon REAL);
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v REAL);
        CREATE INDEX IF NOT EXISTS visits_ip ON visits(ip, ts);""")
    row = db.execute("SELECT v FROM meta WHERE k='last_ts'").fetchone()
    last_ts = row[0] if row else 0.0
    last_seen: dict[str, float] = dict(db.execute("SELECT ip, MAX(ts) FROM visits GROUP BY ip"))
    added = 0
    events, newest = collect_events()
    with maxminddb.open_database(str(GEO_DB)) as reader:
        for ts, ip in events:
            if ts <= last_ts:
                continue
            prev = last_seen.get(ip)
            last_seen[ip] = ts
            if prev is not None and ts - prev < VISIT_GAP_S:
                continue
            g = locate(reader, ip)
            db.execute("INSERT INTO visits VALUES (?,?,?,?,?,?,?)",
                       (ts, ip, g["city"], g["region"], g["country"], g["lat"], g["lon"]))
            added += 1
    # Requests this recent may still be waiting for their asset fetches, so only
    # mark time as processed up to ASSET_WAIT_S before the newest log entry.
    last_ts = max(last_ts, newest - ASSET_WAIT_S)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('last_ts', ?)", (last_ts,))
    db.commit()
    return added


def write_map(db: sqlite3.Connection) -> None:
    place_idx: dict[tuple, int] = {}
    visitor_idx: dict[str, int] = {}
    places, visits = [], []
    for ts, ip, city, region, country, lat, lon in db.execute(
            "SELECT ts, ip, city, region, country, lat, lon FROM visits ORDER BY ts"):
        if lat is None or lon is None:
            continue
        key = (round(lat, 2), round(lon, 2))
        if key not in place_idx:
            place_idx[key] = len(places)
            places.append({"lat": key[0], "lon": key[1],
                           "label": ", ".join(x for x in (city, region, country) if x) or "Unknown"})
        # Visitors are anonymous indexes; the page never sees an IP.
        visits.append([int(ts), place_idx[key], visitor_idx.setdefault(ip, len(visitor_idx))])
    data = {"places": places, "visits": visits, "updated": time.strftime("%Y-%m-%d %H:%M")}
    page = TEMPLATE.replace("%DATA%", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    out_dir = WEB_ROOT / SECRET_FILE.read_text().strip()
    out_dir.mkdir(exist_ok=True)
    tmp = out_dir / "index.tmp"
    tmp.write_text(page)
    tmp.chmod(0o664)
    tmp.replace(out_dir / "index.html")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex,nofollow"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Weather site visitors</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
 :root{--bg:#fcfcfb;--panel:#f4f4f1;--ink:#0b0b0b;--ink2:#52514e;--grid:#e1e0db;--series:#eb6834;--sel:rgba(11,11,11,.09);--edge:#52514e}
 @media (prefers-color-scheme:dark){:root{--bg:#121211;--panel:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--grid:#32322f;--series:#d95926;--sel:rgba(255,255,255,.13);--edge:#c3c2b7}}
 html,body{height:100%}
 body{margin:0;display:flex;flex-direction:column;height:100dvh;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif}
 #bar{padding:8px 12px;background:var(--panel);border-bottom:1px solid var(--grid)}
 #bar .sub{color:var(--ink2)}
 #map{flex:1;min-height:160px;position:relative;z-index:0}
 #activity{position:relative;background:var(--panel);border-top:1px solid var(--grid);padding:8px 12px 6px}
 #head{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;justify-content:space-between}
 #head .t{color:var(--ink2)}
 #presets{display:flex;gap:6px}
 button.p{font:inherit;color:var(--ink);background:transparent;border:1px solid var(--grid);border-radius:6px;padding:2px 10px;cursor:pointer}
 button.p[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
 #chart{display:block;width:100%;height:130px;touch-action:none;user-select:none;-webkit-user-select:none}
 #chart text{fill:var(--ink2);font-size:11px}
 #chart .grid{stroke:var(--grid);stroke-width:1}
 #chart .base{stroke:var(--ink2);stroke-opacity:.5;stroke-width:1}
 #chart .bar{fill:var(--series);opacity:.35}
 #chart .bar.in{opacity:1}
 #chart .sel{fill:var(--sel)}
 #chart .edge{stroke:var(--edge);stroke-width:2}
 #tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--bg);padding:3px 8px;border-radius:5px;font-size:12px;white-space:nowrap;z-index:2000;display:none}
 details{margin-top:2px;color:var(--ink2);font-size:12px} summary{cursor:pointer}
 details table{border-collapse:collapse;margin:4px 0;max-height:120px;display:block;overflow:auto}
 details td,details th{padding:1px 14px 1px 0;text-align:left;font-weight:normal}
</style></head><body>
<div id="bar"><span id="stats"></span> <span class="sub" id="upd"></span></div>
<div id="map"></div>
<div id="activity">
 <div id="head"><span class="t"><span id="unit"></span> &middot; <span id="range"></span></span>
  <span id="presets"></span></div>
 <svg id="chart" role="img" aria-label="Visits over time. Drag to select a time range; the map shows only visits in that range."></svg>
 <div id="tip"></div>
 <details><summary>Data table</summary><table id="tbl"></table></details>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function(){
var D = %DATA%;
var MIN = 60e3, HOUR = 3600e3, DAY = 86400e3, MINSEL = 5 * MIN;
var BUCKETS = [[15*MIN,'15 minutes'],[30*MIN,'30 minutes'],[HOUR,'hour'],[3*HOUR,'3 hours'],
               [6*HOUR,'6 hours'],[12*HOUR,'12 hours'],[DAY,'day'],[7*DAY,'week']];
var visits = D.visits.map(function(v){ return {t: v[0]*1000, p: v[1], v: v[2]}; });
var now = Date.now();
var end0 = Math.max(now, visits.length ? visits[visits.length-1].t : 0);
var d0 = new Date(visits.length ? visits[0].t : end0 - DAY); d0.setHours(0,0,0,0);
var t0 = d0.getTime();
var selA = Math.max(t0, end0 - DAY), selB = end0;

var map = L.map('map').setView([30, 0], 2);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom: 18, attribution: '&copy; OpenStreetMap contributors. IP geolocation by DB-IP.com'}).addTo(map);
var layer = L.layerGroup().addTo(map);

function fmt(t, o){ return new Date(t).toLocaleString([], o || {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}); }
function plural(n, w){ return n + ' ' + w + (n == 1 ? '' : 's'); }
function cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

function updateMap(fit){
  layer.clearLayers();
  var agg = {}, seen = {}, total = 0, color = cssVar('--series');
  visits.forEach(function(v){
    if (v.t < selA || v.t > selB) return;
    total++; seen[v.v] = 1;
    var g = agg[v.p] || (agg[v.p] = {p: v.p, views: 0, vis: {}, last: 0});
    g.views++; g.vis[v.v] = 1; if (v.t > g.last) g.last = v.t;
  });
  var list = Object.keys(agg).map(function(k){ return agg[k]; }).sort(function(a, b){ return b.views - a.views; });
  var pts = [];
  list.forEach(function(g){
    var p = D.places[g.p], nv = Object.keys(g.vis).length, box = document.createElement('div'),
        b = document.createElement('b');
    b.textContent = p.label; box.appendChild(b);
    box.appendChild(document.createElement('br'));
    box.appendChild(document.createTextNode(plural(g.views, 'visit') + ' (' + plural(nv, 'visitor') + ')'));
    box.appendChild(document.createElement('br'));
    box.appendChild(document.createTextNode('last: ' + fmt(g.last)));
    L.circleMarker([p.lat, p.lon], {radius: 5 + 4 * Math.sqrt(g.views), color: color, fillColor: color, fillOpacity: 0.6, weight: 1})
      .bindPopup(box).addTo(layer);
    pts.push([p.lat, p.lon]);
  });
  document.getElementById('stats').textContent = plural(total, 'visit') + ' from ' +
    plural(Object.keys(seen).length, 'visitor') + ' in ' + plural(list.length, 'place');
  document.getElementById('upd').textContent = '· updated ' + D.updated;
  if (fit && pts.length > 1) map.fitBounds(pts, {padding: [30, 30], maxZoom: 6});
  else if (fit && pts.length == 1) map.setView(pts[0], 6);
}

// ---- activity chart ----
var svg = document.getElementById('chart'), tip = document.getElementById('tip'), NS = 'http://www.w3.org/2000/svg';
var M = {l: 30, r: 8, t: 8, b: 20};
var W, H, pw, ph, B, Bname, t1, nb, counts, yTop;
function X(t){ return M.l + (t - t0) / (t1 - t0) * pw; }
function inv(x){ return t0 + (x - M.l) / pw * (t1 - t0); }
function clampT(t){ return Math.max(t0, Math.min(t1, t)); }
function el(n, a, parent){
  var e = document.createElementNS(NS, n);
  for (var k in a) e.setAttribute(k, a[k]);
  if (parent) parent.appendChild(e);
  return e;
}

function layout(){
  W = svg.clientWidth; H = svg.clientHeight; pw = W - M.l - M.r; ph = H - M.t - M.b;
  var maxBars = Math.max(8, Math.floor(pw / 7));
  var pick = BUCKETS[BUCKETS.length - 1];
  for (var i = 0; i < BUCKETS.length; i++) if ((end0 - t0) / BUCKETS[i][0] <= maxBars) { pick = BUCKETS[i]; break; }
  B = pick[0]; Bname = pick[1];
  nb = Math.floor((end0 - t0) / B) + 1; t1 = t0 + nb * B;
  counts = []; for (i = 0; i < nb; i++) counts.push(0);
  visits.forEach(function(v){ counts[Math.min(nb - 1, Math.floor((v.t - t0) / B))]++; });
  var ymax = Math.max.apply(null, counts.concat([1])), step = 1;
  while (ymax / step > 3) step *= (step.toString()[0] == '2' ? 2.5 : 2);
  yTop = Math.ceil(ymax / step) * step; layout.step = step;
  document.getElementById('unit').textContent = 'Visits per ' + Bname;
  var rows = '<tr><th>Period starting</th><th>Visits</th></tr>';
  counts.forEach(function(c, i){ if (c) rows += '<tr><td>' + fmt(t0 + i * B) + '</td><td>' + c + '</td></tr>'; });
  document.getElementById('tbl').innerHTML = rows;
}

function draw(){
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  function Y(c){ return M.t + ph - c / yTop * ph; }
  for (var c = 0; c <= yTop; c += layout.step) {
    if (c) el('line', {x1: M.l, x2: W - M.r, y1: Y(c), y2: Y(c), 'class': 'grid'}, svg);
    el('text', {x: M.l - 6, y: Y(c) + 4, 'text-anchor': 'end'}, svg).textContent = c;
  }
  var bw = pw / nb;
  counts.forEach(function(c, i){
    if (!c) return;
    var s = t0 + i * B, inSel = s < selB && s + B > selA, h = Math.max(2, ph * c / yTop);
    el('rect', {x: X(s) + 1, y: M.t + ph - h, width: Math.max(1, bw - 2), height: h, rx: Math.min(2, bw / 2),
                'class': 'bar' + (inSel ? ' in' : '')}, svg);
  });
  el('line', {x1: M.l, x2: W - M.r, y1: M.t + ph, y2: M.t + ph, 'class': 'base'}, svg);
  var days = [1, 2, 7, 14, 30, 90], dstep = 90;
  for (var i = 0; i < days.length; i++) if (days[i] * DAY / (t1 - t0) * pw >= 56) { dstep = days[i]; break; }
  for (var d = new Date(t0); d.getTime() < t1; d.setDate(d.getDate() + dstep)) {
    var x = X(d.getTime());
    el('text', {x: x, y: H - 5, 'text-anchor': x < M.l + 14 ? 'start' : 'middle'}, svg).textContent = fmt(d.getTime(), {month: 'short', day: 'numeric'});
  }
  var xa = X(selA), xb = X(selB);
  el('rect', {x: xa, y: M.t, width: Math.max(0, xb - xa), height: ph, 'class': 'sel'}, svg);
  el('line', {x1: xa, x2: xa, y1: M.t, y2: M.t + ph, 'class': 'edge'}, svg);
  el('line', {x1: xb, x2: xb, y1: M.t, y2: M.t + ph, 'class': 'edge'}, svg);
  updateHead();
}

var presets = [['24 hours', DAY], ['7 days', 7 * DAY], ['30 days', 30 * DAY], ['All', null]];
var pbox = document.getElementById('presets');
presets.forEach(function(p){
  var b = document.createElement('button'); b.className = 'p'; b.textContent = p[0]; b.type = 'button';
  b.onclick = function(){ selA = p[1] ? Math.max(t0, end0 - p[1]) : t0; selB = end0; draw(); updateMap(true); };
  p.push(b); pbox.appendChild(b);
});
function updateHead(){
  document.getElementById('range').textContent = fmt(selA) + ' – ' + (selB >= end0 - 1000 ? 'now' : fmt(selB));
  presets.forEach(function(p){
    var a = p[1] ? Math.max(t0, end0 - p[1]) : t0;
    p[2].setAttribute('aria-pressed', Math.abs(selA - a) < 1000 && Math.abs(selB - end0) < 1000);
  });
}

var drag = null, raf = 0;
function px(e){ return e.clientX - svg.getBoundingClientRect().left; }
function modeAt(x){
  var xa = X(selA), xb = X(selB), da = Math.abs(x - xa), db = Math.abs(x - xb);
  if (da <= 8 && da <= db) return 'a';
  if (db <= 8) return 'b';
  return x > xa && x < xb ? 'move' : 'new';
}
var CURSOR = {a: 'ew-resize', b: 'ew-resize', move: 'grab', 'new': 'crosshair'};
svg.addEventListener('pointerdown', function(e){
  var x = px(e); drag = {mode: modeAt(x), x0: x, a0: selA, b0: selB, moved: false};
  svg.setPointerCapture(e.pointerId); e.preventDefault(); tip.style.display = 'none';
});
svg.addEventListener('pointermove', function(e){
  var x = px(e);
  if (!drag) { svg.style.cursor = CURSOR[modeAt(x)]; showTip(x); return; }
  if (Math.abs(x - drag.x0) > 3) drag.moved = true;
  if (!drag.moved) return;
  var t = clampT(inv(x)), m = drag.mode;
  if (m == 'a') selA = Math.min(t, drag.b0 - MINSEL);
  else if (m == 'b') selB = Math.max(t, drag.a0 + MINSEL);
  else if (m == 'move') { var w = drag.b0 - drag.a0; selA = Math.max(t0, Math.min(t1 - w, drag.a0 + inv(x) - inv(drag.x0))); selB = selA + w; }
  else { var s = clampT(inv(drag.x0)); selA = Math.min(s, t); selB = Math.max(s, t); if (selB - selA < MINSEL) selB = selA + MINSEL; }
  svg.style.cursor = m == 'move' ? 'grabbing' : CURSOR[m];
  if (!raf) raf = requestAnimationFrame(function(){ raf = 0; draw(); updateMap(false); });
});
function endDrag(){
  if (!drag) return;
  if (!drag.moved && drag.mode == 'new') {   // click on a bar: select that period
    var i = Math.max(0, Math.min(nb - 1, Math.floor((inv(drag.x0) - t0) / B)));
    selA = t0 + i * B; selB = Math.min(end0, selA + B);
  }
  drag = null; draw(); updateMap(true);
}
svg.addEventListener('pointerup', endDrag);
svg.addEventListener('pointercancel', endDrag);
svg.addEventListener('pointerleave', function(){ tip.style.display = 'none'; });
svg.addEventListener('dblclick', function(){ selA = t0; selB = end0; draw(); updateMap(true); });

function showTip(x){
  var i = Math.floor((inv(x) - t0) / B);
  if (i < 0 || i >= nb || x < M.l || x > W - M.r) { tip.style.display = 'none'; return; }
  var s = t0 + i * B;
  tip.textContent = fmt(s, B >= DAY ? {month: 'short', day: 'numeric'} : undefined) + ' · ' + plural(counts[i], 'visit');
  tip.style.display = 'block';
  var r = svg.getBoundingClientRect(), a = document.getElementById('activity').getBoundingClientRect();
  tip.style.left = Math.max(4, Math.min(r.left - a.left + x - tip.offsetWidth / 2, a.width - tip.offsetWidth - 4)) + 'px';
  tip.style.top = (r.top - a.top - 4) + 'px';
}

function relayout(){ layout(); draw(); }
if (window.ResizeObserver) {
  new ResizeObserver(relayout).observe(svg);
  new ResizeObserver(function(){ map.invalidateSize(); }).observe(document.getElementById('map'));
} else window.addEventListener('resize', function(){ relayout(); map.invalidateSize(); });
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(){ updateMap(false); });
relayout(); updateMap(true);
})();
</script></body></html>
"""


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not GEO_DB.is_file():
        print(f"Missing {GEO_DB} (download DB-IP City Lite).", file=sys.stderr)
        return 1
    db = sqlite3.connect(DB_PATH)
    added = update_db(db)
    write_map(db)
    print(f"{time.strftime('%F %T')} added {added} visit(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
