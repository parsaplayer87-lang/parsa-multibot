"""دسته‌ای ابزارهای کوچک مشترک بین ماژول‌ها."""
import re
from typing import Optional

import httpx

import config

URL_RE = re.compile(r"(https?://\S+)|(^|\s)(www\.\S+)", re.I)

def extract_url(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return None
    m = URL_RE.search(t)
    if not m:
        return None
    u = m.group(1) or m.group(3)
    return u if u.startswith("http") else "https://" + u

def is_url(text: str) -> bool:
    return extract_url(text) is not None

def md_escape(s: str) -> str:
    """برای متن‌های داخل *...* یا _..._ بله؛ براکت/پرانتز لینک را هم امن می‌کند."""
    s = (s or "").replace("[", "(").replace("]", ")").replace("`", "'")
    s = s.replace("*", "·").replace("_", "‌")
    return s

def md_link(url: str, text: str = "لینک") -> str:
    url = url.replace(")", "%29").replace("(", "%28")
    return f"[{text}]({url})"

def short(s: str, n: int = 60) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"

_gh_re = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)", re.I)

def github_repo(url: str) -> Optional[tuple[str, str]]:
    m = _gh_re.search(url or "")
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo

async def http_get(url: str, timeout: float = 30, headers: dict | None = None,
                   follow: bool = True) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow) as c:
        return await c.get(url, headers=headers or {})

async def fetch_bytes(url: str, timeout: float = 30, headers: dict | None = None) -> bytes | None:
    try:
        r = await http_get(url, timeout, headers)
        if r.status_code == 200:
            return r.content
    except httpx.HTTPError:
        pass
    return None

def gh_api_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "parsa-multibot"}
    if config.GH_PAT:
        h["Authorization"] = f"Bearer {config.GH_PAT}"
    return h

PLATFORM_NAMES = {
    "youtube": "یوتیوب", "instagram": "اینستاگرام", "pinterest": "پینترست",
    "tiktok": "تیک‌تاک", "twitter": "توییتر (X)", "soundcloud": "ساندکلاد",
    "facebook": "فیس‌بوک", "vimeo": "ویمیو", "aparat": "آپارات", "reddit": "ردیت",
}

def platform_name(info: dict, url: str) -> str:
    ex = (info.get("extractor_key") or info.get("extractor") or "").lower()
    for k, v in PLATFORM_NAMES.items():
        if k in ex or k in (url or "").lower():
            return v
    host = re.sub(r"^www\.", "", (url or "").split("//")[-1].split("/")[0])
    return host or "سرویس ناشناس"
