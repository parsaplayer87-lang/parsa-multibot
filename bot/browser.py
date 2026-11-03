"""پلی‌رایت: مرورگر singleton برای اسکرین‌شات و ذخیره کامل صفحه (MHTML زنده با HTML/CSS/JS)."""
import asyncio
import logging
import os
import tempfile
import urllib.parse

import config

log = logging.getLogger("browser")

_pw = None
_browser = None
_lock = asyncio.Lock()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


async def get_browser():
    global _pw, _browser
    async with _lock:
        if _browser is None and _pw is None:
            from playwright.async_api import async_playwright
            _pw = await async_playwright().start()
            try:
                _browser = await _pw.chromium.launch(headless=True, args=[
                    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                    "--hide-scrollbars", "--mute-audio", "--disable-blink-features=AutomationControlled"])
            except Exception:
                # اگر chromium نصب نیست
                await _pw.stop()
                _pw = None
                raise
        return _browser


async def stop_browser():
    global _pw, _browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _pw:
        try:
            await _pw.stop()
        except Exception:
            pass
        _pw = None


async def _new_context():
    b = await get_browser()
    return await b.new_context(
        user_agent=UA, viewport={"width": 1280, "height": 800},
        locale="en-US", ignore_https_errors=True, java_script_enabled=True,
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9,fa;q=0.8"})


def _safe_name(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.replace(":", "_")
    return "".join(c for c in host if c.isalnum() or c in "-._")[:40] or "page"


async def screenshot(url: str, timeout_ms: int = 25000) -> bytes | None:
    """اسکرین‌شات از نمای اول صفحه (jpeg فشرده برای آلبوم بله)."""
    ctx = await _new_context()
    try:
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            with __import__("contextlib").suppress(Exception):
                await page.goto("https://" + url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        await page.wait_for_timeout(1200)
        data = await page.screenshot(type="jpeg", quality=72)
        await page.close()
        return data
    except Exception as e:
        log.warning("screenshot %s failed: %s", url, e)
        return None
    finally:
        try:
            await ctx.close()
        except Exception:
            pass


async def capture_page(url: str, timeout_ms: int = 45000) -> tuple[bytes | None, bytes | None]:
    """ذخیره حرفه‌ای صفحه: (mhtml_bytes, screenshot_jpeg)

    MHTML توسط Page.captureSnapshot ساخته می‌شود؛ خروجی را در Chrome/Edge باز کن —
    صفحه با تمام CSS، JS، تصاویر و تعامل‌هایش «زنده» است.
    """
    ctx = await _new_context()
    try:
        page = await ctx.new_page()
        norm = url if url.startswith("http") else "https://" + url
        try:
            await page.goto(norm, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            log.warning("goto %s: %s", norm, e)
        with __import__("contextlib").suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=8000)
        await page.wait_for_timeout(1500)
        shot = None
        with __import__("contextlib").suppress(Exception):
            shot = await page.screenshot(type="jpeg", quality=80)
        mhtml = None
        with __import__("contextlib").suppress(Exception):
            cdp = await ctx.new_cdp_session(page)
            snap = await cdp.send("Page.captureSnapshot", {"format": "mhtml"})
            mhtml = snap.get("data", "").encode("utf-8")
        await page.close()
        return mhtml, shot
    except Exception as e:
        log.warning("capture %s failed: %s", url, e)
        return None, None
    finally:
        try:
            await ctx.close()
        except Exception:
            pass


async def shot_many(urls: list[str], concurrency: int = 3) -> list[bytes | None]:
    """اسکرین‌شات موازی چند URL؛ خروجی هم‌تراز با ورودی (None برای شکست)."""
    sem = asyncio.Semaphore(concurrency)

    async def one(u):
        async with sem:
            return await screenshot(u)

    return list(await asyncio.gather(*(one(u) for u in urls)))


def mhtml_filename(url: str) -> str:
    return _safe_name(url) + ".mhtml"
