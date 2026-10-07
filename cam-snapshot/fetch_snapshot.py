#!/usr/bin/env python3
"""Fetch a fresh snapshot from the Reolink Home Hub camera and drop it into
weewx's HTML_ROOT so the skin template can display it. Meant to run from cron.

Uses the hub's HTTP API (Snap command) rather than RTSP: the Argus 4 Pro is a
battery camera that sleeps in standby, and the Snap call wakes it (~6s) while
RTSP just stalls with no video. Credentials come from cam.env (CAM_USER,
CAM_PASS), kept out of this file and the crontab.
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / "cam.env"

HUB = "192.168.68.2"
CHANNEL = 0
LATEST_PATH = Path("/var/www/html/weewx/cam_snapshot.jpg")
ARCHIVE_DIR = Path("/var/www/html/weewx/cam_snapshots")
ARCHIVE_ROOT = Path("/var/www/html/weewx/cam_archive")
ARCHIVE_RETENTION_S = 24 * 60 * 60

LATEST_WIDTH = 1920
ARCHIVE_WIDTH = 1440
ARCHIVE_QUALITY = 80
HTTP_TIMEOUT_S = 45
RETRIES = 3


def load_env() -> dict[str, str]:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


def fetch_snap(user: str, password: str) -> bytes | None:
    query = urllib.parse.urlencode({
        "cmd": "Snap", "channel": CHANNEL, "rs": "weewx",
        "user": user, "password": password,
    })
    url = f"http://{HUB}/cgi-bin/api.cgi?{query}"
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
                data = resp.read()
            if data[:2] == b"\xff\xd8":
                return data
            print(f"Snap returned non-JPEG ({len(data)} bytes): {data[:120]!r}", file=sys.stderr)
        except OSError as exc:
            print(f"Snap attempt {attempt + 1} failed: {exc}", file=sys.stderr)
        time.sleep(3)
    return None


def resized_jpeg(img: Image.Image, width: int, quality: int = 85) -> bytes:
    h = round(img.height * width / img.width)
    out = io.BytesIO()
    img.resize((width, h), Image.LANCZOS).save(out, "JPEG", quality=quality)
    return out.getvalue()


def day_key(epoch: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def _write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)


def roll_archive(now: float) -> None:
    """Move frames older than the rolling window into the permanent,
    day-bucketed archive instead of deleting them."""
    cutoff = now - ARCHIVE_RETENTION_S
    for jpg in ARCHIVE_DIR.glob("*.jpg"):
        try:
            epoch = float(jpg.stem)
        except ValueError:
            continue
        if epoch < cutoff:
            day_dir = ARCHIVE_ROOT / day_key(epoch)
            day_dir.mkdir(parents=True, exist_ok=True)
            jpg.replace(day_dir / jpg.name)


def write_manifests() -> None:
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    date_dirs = sorted((d for d in ARCHIVE_ROOT.iterdir() if d.is_dir()), reverse=True)
    dates = []
    for d in date_dirs:
        epochs = sorted(int(p.stem) for p in d.glob("*.jpg") if p.stem.isdigit())
        _write_json_atomic(d / "frames.json", {"date": d.name, "epochs": epochs})
        dates.append(d.name)
    _write_json_atomic(ARCHIVE_ROOT / "index.json", {"dates": dates})


def main() -> int:
    if not ENV_FILE.is_file():
        print(f"Missing {ENV_FILE} (needs CAM_USER and CAM_PASS).", file=sys.stderr)
        return 1
    env = load_env()
    raw = fetch_snap(env["CAM_USER"], env["CAM_PASS"])

    now = time.time()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    roll_archive(now)
    write_manifests()

    if not raw:
        print("No snapshot available this cycle; keeping last snapshot.", file=sys.stderr)
        return 0

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    tmp_path = LATEST_PATH.with_suffix(".jpg.tmp")
    tmp_path.write_bytes(resized_jpeg(img, LATEST_WIDTH))
    tmp_path.replace(LATEST_PATH)
    (ARCHIVE_DIR / f"{int(now)}.jpg").write_bytes(resized_jpeg(img, ARCHIVE_WIDTH, ARCHIVE_QUALITY))
    return 0


if __name__ == "__main__":
    sys.exit(main())
