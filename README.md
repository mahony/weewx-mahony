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
`weectl report run`. Paths, hub address and the 192.168.x.x IP are hardcoded for one
setup; adjust the constants at the top of the script.

## License

GNU General Public License v3 — see `LICENSE`. `skins/Seasons/skin.conf` is derived
from the WeeWX Seasons skin, Copyright (c) 2018-2021 Tom Keffer and Matthew Wall,
distributed under the GPL v3 (or later); the rest of this repo is licensed the same way.
