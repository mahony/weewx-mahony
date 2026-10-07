# weewx-mahony

A webcam snapshot widget for a [WeeWX](https://weewx.com) station: shows the
latest camera snapshot on the Seasons skin home page, with a 24-hour timelapse and a
browsable archive of past days.

## Contents

- `cam-snapshot/fetch_snapshot.py` — cron script (every 5 min). Grabs a frame from a
  Reolink Home Hub camera, writes the live image, a rolling 24h frame buffer, and a
  permanent day-bucketed archive (1440px, JPEG quality 80, about 10 GB/year) with
  JSON manifests. Credentials are read from an untracked `cam.env`
  (`CAM_USER`, `CAM_PASS`).
- `skins/Seasons/cam.inc` — live widget (latest snapshot + 24h timelapse).
- `skins/Seasons/cam_archive.inc`, `cam_archive.html.tmpl` — archive page.
- `skins/Seasons/skin.conf` — the stock WeeWX Seasons config with the
  `cam_archive` page registered.

## Controls

| Input | Action |
|---|---|
| Click photo / Space | Start timelapse; freeze on the current frame; resume |
| ← / → (hold to repeat) | Step one snapshot back / forward (when not playing) |
| Swipe left / right (hold to repeat) | Same as → / ← on touch devices |
| Esc | Back to the current snapshot (archive: the day's first frame) |

On the live widget the arrows wrap around the 24h window. On the archive page,
stepping past the end of a day continues into the adjacent day and wraps around
the whole archive.

## Install

The live files are meant to be symlinked to this repo so edits are always tracked:

- `/etc/weewx/skins/Seasons/{cam.inc,cam_archive.inc,cam_archive.html.tmpl,skin.conf}`
- `~/.local/share/cam-snapshot/fetch_snapshot.py`

The `weewx` user must be able to read the repo path (e.g.
`setfacl -m u:weewx:x ~`). Add the cron entry
`*/5 * * * * python3 ~/.local/share/cam-snapshot/fetch_snapshot.py`, then run
`weectl report run`. Paths and the hub address are hardcoded for one
setup; adjust the constants at the top of the script.

## License

GNU General Public License v3 — see `LICENSE`. `skins/Seasons/skin.conf` is derived
from the WeeWX Seasons skin, Copyright (c) 2018-2021 Tom Keffer and Matthew Wall,
distributed under the GPL v3 (or later); the rest of this repo is licensed the same way.

## Visitor map (`visitors/`, `nginx/`)

`visitors/visitors.py` (cron, every 15 min) counts page views from the nginx logs and
writes a map of where visitors are to `index.html` in an unlinked directory
under the web root (name in `~/.local/share/wx-visitors/map_dir`, default `visitors`; the URL is
`https://<host>/<that name>/`, marked noindex — anyone with the URL can view it). A view is a GET of `/` by a non-bot
public IP that also fetches page assets like a browser does, which filters out the
scrapers that fetch only the HTML. IPs are located offline with the free
[DB-IP City Lite](https://db-ip.com/db/download/ip-to-city-lite) database (CC-BY 4.0;
`db.mmdb` in the data dir, refresh monthly); raw IPs stay in the local SQLite file,
never in the map. Setup: a venv with `maxminddb` in the data dir, the `.mmdb`
file, and `nginx/wx_log.conf` (see its header) so requests are logged with their host.

The page ends with an activity bar chart of visits over time. It opens on the last 24 hours;
drag on the chart to select another range (drag the edges to resize or the middle to move,
click a bar to select it, double-click for everything, or use the 24 hours / 7 days / 30 days /
All buttons), and the map shows only visits in that range.
