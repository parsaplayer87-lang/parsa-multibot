"""پوشش yt-dlp (اجرای subprocess برای جداسازی کرش‌ها) + استخراج مشخصات.

- probe(url):  مشخصات کامل JSON یک لینک
- yt_search(query, n): جستجوی یوتیوب (مثل موتور جستجوی خود یوتیوب) — flat
- download(url, fmt, out_dir, progress_cb): دانلود با گزارش پیشرفت
"""
import asyncio
import json
import logging
import os
import re
import sys

import config

log = logging.getLogger("ytdlp")

PY = sys.executable


def _base_args() -> list[str]:
    args = [PY, "-m", "yt_dlp", "--no-warnings", "--no-playlist", "--no-check-certificates"]
    ck = config.cookies_file()
    if ck:
        args += ["--cookies", ck]
    return args


async def _run_json(args: list[str], timeout: int = 90) -> dict | None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        log.debug("ytdlp err: %s", err.decode(errors="replace")[:300])
        return None
    try:
        return json.loads(out.decode(errors="replace"))
    except Exception:
        return None


async def probe(url: str) -> dict | None:
    """مشخصات کامل یک لینک (بدون دانلود)."""
    data = await _run_json(_base_args() + ["-J", url], timeout=75)
    return data


async def yt_search(query: str, n: int = 5) -> list[dict]:
    """جستجوی یوتیوب با yt-dlp (بدون API key)."""
    args = [PY, "-m", "yt_dlp", "--no-warnings", "--flat-playlist", "-J",
            f"ytsearch{n}:{query}"]
    ck = config.cookies_file()
    if ck:
        args += ["--cookies", ck]
    data = await _run_json(args, timeout=75)
    if not data:
        return []
    items = []
    for e in (data.get("entries") or []):
        if not e:
            continue
        items.append({
            "id": e.get("id"),
            "url": e.get("url") or (f"https://www.youtube.com/watch?v={e.get('id')}" if e.get("id") else ""),
            "title": e.get("title") or "(بدون عنوان)",
            "channel": e.get("channel") or e.get("uploader") or "",
            "duration": e.get("duration") or 0,
            "thumbnail": e.get("thumbnails") and (e["thumbnails"][-1].get("url") if isinstance(e.get("thumbnails"), list) else e.get("thumbnail")) or e.get("thumbnail") or "",
            "view_count": e.get("view_count") or 0,
        })
    return items


def fmt_duration(sec) -> str:
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        return "؟"
    if not sec:
        return "زنده/نامشخص"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def human_size(nbytes) -> str:
    try:
        n = float(nbytes or 0)
    except (TypeError, ValueError):
        return "؟"
    if not n:
        return "؟"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def best_formats(info: dict) -> dict:
    """از formats ویدیویی، بهترین کیفیت هر ارتفاع + تخمین حجم."""
    heights = {}
    best_audio_size = 0
    for f in info.get("formats") or []:
        if f.get("vcodec") not in (None, "none") and f.get("height"):
            h = int(f["height"])
            size = f.get("filesize") or f.get("filesize_approx") or 0
            cur = heights.get(h)
            score = (f.get("tbr") or 0)
            if cur is None or score > cur[0]:
                heights[h] = (score, size)
        if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none"):
            best_audio_size = max(best_audio_size, f.get("filesize") or f.get("filesize_approx") or 0)
    out = {}
    for h in sorted(heights, reverse=True):
        out[h] = heights[h][1]
    return out, best_audio_size


QUALITY_ORDER = [1080, 720, 480, 360]

def video_format_string(height: int) -> str:
    return (f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={height}]+ba/b[height<={height}]/b")


AUDIO_FORMAT_STRING = "ba[ext=m4a]/ba/b"


async def download(url: str, fmt: str, out_dir: str,
                   progress_cb=None, cancel_event: asyncio.Event | None = None) -> str | None:
    """دانلود؛ مسیر فایل نهایی را برمی‌گرداند. progress_cb(percent, done, total, speed, eta)."""
    os.makedirs(out_dir, exist_ok=True)
    out_tmpl = os.path.join(out_dir, "%(title).80s.%(ext)s")
    prog_tpl = "PROG|%(progress._percent_str)s|%(progress.downloaded_bytes)s|%(progress.total_bytes_estimate)s|%(progress._speed_str)s|%(progress._eta_str)s"
    args = _base_args() + [
        "-f", fmt, "--merge-output-format", "mp4",
        "--newline", "--progress-template", prog_tpl,
        "--no-mtime", "-o", out_tmpl, url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    last_file = None
    try:
        async def read_out():
            nonlocal last_file
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if line.startswith("[download]") and "Destination:" in line:
                    m = re.search(r"Destination:\s+(.+)$", line)
                    if m:
                        last_file = m.group(1).strip()
                elif line.startswith("PROG|"):
                    parts = line.split("|")
                    if progress_cb and len(parts) >= 6:
                        def to_i(x):
                            try:
                                return int(float(x.strip().replace("%", "")))
                            except (ValueError, TypeError):
                                return 0
                        with __import__("contextlib").suppress(Exception):
                            await progress_cb(
                                to_i(parts[1]), to_i(parts[2]), to_i(parts[3]),
                                parts[4].strip(), parts[5].strip())
        async def read_err():
            assert proc.stderr
            async for _ in proc.stderr:
                pass
        await asyncio.gather(read_out(), read_err())
        await asyncio.wait_for(proc.wait(), timeout=3600)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    # پیدا کردن فایل جدیدترین ساخته‌شده
    if last_file and os.path.exists(last_file):
        return last_file
    try:
        files = [(os.path.getmtime(os.path.join(out_dir, f)), os.path.join(out_dir, f))
                 for f in os.listdir(out_dir)]
        return max(files)[1] if files else None
    except OSError:
        return None
