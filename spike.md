# Spike: cam widget browsing UX + archive resolution

Files are tracked in this repo (`skins/Seasons/*`, `cam-snapshot/fetch_snapshot.py`)
and the live copies are symlinks to them: `/etc/weewx/skins/Seasons/{cam.inc,
cam_archive.inc,cam_archive.html.tmpl,skin.conf}` and
`/home/tod/.local/share/cam-snapshot/fetch_snapshot.py`. Edit either path (same
file), but avoid tools that replace a symlink with a regular file. Web output
lives in `/var/www/html/weewx`; `cam.env` (credentials) is deliberately not in
the repo. After editing skin files run `cd /etc/weewx && weectl report run` (a
`seasons.css` PermissionError in its output is pre-existing/harmless).

## What we did

- Timelapse interaction rework, applied to both the live widget (`cam.inc`,
  last 24h + latest snapshot) and the archive page (`cam_archive.inc`):
  - Click while playing freezes on the current frame (no longer jumps to latest);
    click again resumes from that frame.
  - Left/right arrows step one snapshot when not playing; held keys auto-repeat.
  - Esc returns to current snapshot (live widget: latest; archive: selected
    day's first frame) whether playing or browsing.
  - Space bar = same as clicking the photo (ignored on input/select/button/link
    focus, ignores key repeat).
  - Live widget wraps at both ends (→ at latest goes to 24h ago, ← at oldest
    goes to latest). 
  - Archive page: stepping past a day's end/start moves to the adjacent day and
    wraps around the whole archive (date list is newest-first; empty days are
    skipped; date picker follows along; old photo stays on screen while the
    next day's frames.json loads).
  - Mobile: swipe left/right = right/left arrow; swipe-and-hold auto-repeats
    (500ms delay, then ~30/s) via touchmove, direction fixed at first crossing
    (40px, mostly horizontal). `touch-action: pan-y` on the img.
  - Arrow/space/swipe handlers ignored while a timelapse is playing (except Esc/space).
  - User confirmed arrows, freeze, Esc, wrap, and swipe work ("works great");
    swipe-hold, archive day-crossing and the space bar were not yet confirmed,
    and none of the JS was syntax-checked (no node on this box).
- Archive resolution: raised `ARCHIVE_WIDTH` 960 → 1440 (`ARCHIVE_QUALITY = 80`
  constant added) in `fetch_snapshot.py`. Measured against a real camera frame:
  ~97KB/frame ≈ 10.5 GB/yr (was ~45KB ≈ 4.7 GB/yr). Alternatives considered:
  1920/q70 ≈ 14 GB/yr, 2560/q80 ≈ 31 GB/yr.
- Camera's native frame is 5120×1440 (~545KB JPEG ≈ 59 GB/yr if archived
  untouched; raw RGB would be ~184–327 GB/yr+). Used a temporary sample-save
  hack in the script to measure, then removed it and deleted the sample file.
- Altitude check: USGS EPQS gives ~81 ft ground at the configured coords;
  + ~40 ft building ≈ 120–130 ft, consistent with `altitude = 127, foot` in
  `weewx.conf`. (weewx location in config is "Cranston St Armory"; coords are
  taken from the config, not geocoded from the street address.)

- Put the files under git and symlinked the live paths to the repo (commit
  882e726, author "Tod" <me@tod.me>, repo-local git config). Needed
  `sudo setfacl -m u:weewx:x /home/tod` (run by the user in a real terminal; sudo
  has no tty in Claude's shell) so the `weewx` user can traverse /home/tod to
  read the linked skin files. `weectl report run` as tod works; the check as the
  weewx user was left for the user to run.

## Next steps

- Verify as the weewx user (user to run; not done yet):
  `sudo -u weewx cat /etc/weewx/skins/Seasons/cam.inc >/dev/null && echo ok`, and
  confirm the next scheduled report cycle still renders (no errors in syslog).

- Confirm on desktop + phone: archive day-crossing/wrap, hold-scrub across day
  boundaries, and the space bar. Smoke-test the JS in a browser console if
  anything misbehaves (nothing was syntax-checked).
- After a day or so at 1440px, check avg file size in
  `/var/www/html/weewx/cam_snapshots` to confirm ~10 GB/yr; adjust
  `ARCHIVE_WIDTH`/`ARCHIVE_QUALITY` if off. Existing archived days stay 960px.
- Still unconfirmed from last spike: real cron run against the Reolink hub
  (`tail /home/tod/.local/share/cam-snapshot/fetch.log`) — the sample capture
  this session did succeed, so that is likely fine now.
- Loose ends / possible polish: no touch equivalent of Esc (only reload or
  keep swiping on the live widget); archive wrap loops forever only if every
  day were empty (not realistic).
- Still deferred: ffmpeg MP4 timelapses per day; thinning old archive days.
- If the repo ever moves/renames, the symlinks break (and the ACL only covers
  /home/tod traversal).
