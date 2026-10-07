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
- Output: MAP_PATH, a standalone HTML page (Leaflet via CDN) that is NOT in any
  web root, so it is private. Open it locally.
"""
from __future__ import annotations

import gzip
import html
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
MAP_PATH = DATA_DIR / "map.html"
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
    places = {}
    for ts, ip, city, region, country, lat, lon in db.execute("SELECT * FROM visits ORDER BY ts"):
        if lat is None or lon is None:
            continue
        key = (round(lat, 2), round(lon, 2))
        p = places.setdefault(key, {"lat": key[0], "lon": key[1], "label": ", ".join(x for x in (city, region, country) if x) or "Unknown",
                                    "views": 0, "ips": set(), "last": 0})
        p["views"] += 1
        p["ips"].add(ip)
        p["last"] = max(p["last"], ts)
    total = db.execute("SELECT COUNT(*), COUNT(DISTINCT ip), MIN(ts), MAX(ts) FROM visits").fetchone()
    data = [{"lat": p["lat"], "lon": p["lon"], "label": p["label"], "views": p["views"],
             "visitors": len(p["ips"]), "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(p["last"]))}
            for p in places.values()]
    data.sort(key=lambda p: -p["views"])
    since = time.strftime("%Y-%m-%d", time.localtime(total[2])) if total[2] else "n/a"
    page = TEMPLATE.replace("%DATA%", json.dumps(data)).replace(
        "%SUMMARY%", html.escape(f"{total[0]} visits from {total[1]} unique visitors in {len(data)} places since {since} "
                                 f"(updated {time.strftime('%Y-%m-%d %H:%M')})"))
    tmp = MAP_PATH.with_suffix(".tmp")
    tmp.write_text(page)
    tmp.replace(MAP_PATH)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weather site visitors</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
 body{margin:0;font:14px system-ui,sans-serif} #bar{padding:8px 12px;background:#f3f4f6;color:#111}
 #map{height:calc(100vh - 36px)}
 @media (prefers-color-scheme:dark){#bar{background:#1f2937;color:#e5e7eb}}
</style></head><body>
<div id="bar">%SUMMARY%</div><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
var places = %DATA%;
var map = L.map('map').setView([30, 0], 2);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom: 18, attribution: '&copy; OpenStreetMap contributors. IP geolocation by DB-IP.com'}).addTo(map);
var pts = [];
places.forEach(function(p) {
  var r = 5 + 4 * Math.sqrt(p.views);
  L.circleMarker([p.lat, p.lon], {radius: r, color: '#c2410c', fillColor: '#f97316', fillOpacity: 0.6, weight: 1})
    .bindPopup('<b>' + p.label.replace(/</g, '&lt;') + '</b><br>' + p.views + ' visit' + (p.views == 1 ? '' : 's') +
               ' (' + p.visitors + ' visitor' + (p.visitors == 1 ? '' : 's') + ')<br>last: ' + p.last)
    .addTo(map);
  pts.push([p.lat, p.lon]);
});
if (pts.length > 1) map.fitBounds(pts, {padding: [30, 30], maxZoom: 6});
else if (pts.length == 1) map.setView(pts[0], 6);
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
