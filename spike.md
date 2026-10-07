# Spike: visitor map + activity chart

Repo layout reminder: `skins/Seasons/*` and `cam-snapshot/fetch_snapshot.py` are
symlinked from their live paths (`/etc/weewx/skins/Seasons/...`,
`~/.local/share/cam-snapshot/`); `cam.env` (credentials) is not in the repo.
Web output lives in `/var/www/html/weewx` (group-writable by `weewx`; tod is in the group).

## What we did

- Verified the interrupted session's work end to end: `nginx/wx_log.conf` installed in
  `conf.d`, `access_log ... wx;` in both server blocks (443 and 80) of `wx-nerpy` and
  `wx-mahony`, log is live, venv + `db.mmdb` + `*/15` cron entry in place. (`nginx -t`
  as tod fails on the snakeoil key permission; harmless, needs sudo.)
- Moved the map from a private local file to the web: `visitors/visitors.py` now writes
  `index.html` into `/var/www/html/weewx/<name>/`, where `<name>` is read from
  `~/.local/share/wx-visitors/map_dir` (now `visitors`, so
  `https://wx.nerpy.co/visitors/`, also on wx.mahony.me). No nginx change needed
  (autoindex is off). Page is `noindex`. Started with a random dir name, dropped it for
  a readable URL since the content (city-level places + counts, no IPs) is low-sensitivity.
  Unlinked is not protected: anyone with the URL sees it. Basic auth was offered, declined.
- Added an activity bar chart under the map (client-side SVG, no lib): visits per
  bucket (15m..1w, chosen by width/history), brush to select a range (new / resize edges /
  move / click a bar / dblclick = all), presets 24h/7d/30d/All, default = last 24h.
  Map markers, stats line, and range label filter live while dragging; map re-fits on
  release. Tooltip on hover, collapsible data table, light/dark tokens (series colour
  from the dataviz palette, orange to match the map dots).
- `write_map` now embeds `{places, visits:[[ts, place, anonymousVisitorIdx]], updated}`
  instead of pre-aggregated places; no IPs reach the page. Visits with no lat/lon are
  dropped from the page (the old summary counted them).
- Bugs found while testing in Chrome: y-tick step loop jumped 5 -> 50 (rewritten);
  tooltip position used `svg.offsetTop` (undefined on SVG elements, now uses
  getBoundingClientRect); added a ResizeObserver on `#map` for `invalidateSize`.
  The brief grey strip on the map in screenshots was just tiles still loading.
- Installed the `frontend-design` plugin (`/plugin install frontend-design@claude-plugins-official`);
  not really needed for this work.
- Camera `weewx`-user check passed (`sudo -u weewx cat .../cam.inc` -> ok).
- User manually validated everything from this session (including real mouse drag, which
  Claude's automated drag tool could not exercise; synthetic PointerEvents did work).
- Commits: 03281f2 (serve map under web root), 438c8b0 (plain /visitors/ path),
  c6c7f8b (activity chart).

## Next steps

- **Revisit camera archive file size (tomorrow or later):** first check was inconclusive.
  `cam_archive` days 10-05/10-06 avg ~40 KB (old 960px, mostly night); live set in
  `cam_snapshots` avg ~45 KB (night frames). Once daytime 1440px frames have rotated into
  `cam_archive/<date>/` (frames archive after 24h), run:
  `cd /var/www/html/weewx/cam_archive; for d in 2026-10-0*/; do find $d -name '*.jpg' -printf '%s\n' | awk -v d=$d '{s+=$1;n++} END{printf "%s %d frames, avg %.0f KB\n", d, n, s/n/1024}'; done`
  Expect ~70-100 KB/day average (~10.5 GB/yr at ~97 KB). If much higher, lower
  `ARCHIVE_QUALITY`/`ARCHIVE_WIDTH` in `fetch_snapshot.py`.
- Visitor map polish (optional): exclude own IP(s) (Boston dot is likely Tod's own
  traffic); a few dots land in the Atlantic (DB-IP city-level guesses); refresh `db.mmdb`
  monthly (DB-IP City Lite); page is a static snapshot, reload for new data and the
  selection resets to 24h.
- Cam widget loose ends from last spike: confirm archive day-crossing/wrap and hold-scrub
  across day boundaries on desktop + phone (space bar and swipe-hold also unconfirmed
  then; user has since validated the widget generally); real cron run against the
  Reolink hub (`tail ~/.local/share/cam-snapshot/fetch.log`).
- Still deferred: ffmpeg MP4 timelapses per day; thinning old archive days.
- If the repo moves/renames, the symlinks break (and the ACL only covers /home/tod).
