"""جستجوی وب (موتور جستجو متا DDG + چند سرویس fallback) + اسکرین‌شات + fetch حرفه‌ای صفحه."""
import asyncio
import logging

import browser
import db
import state
import txt
import util
from baleapi import Bot, btn, kb

log = logging.getLogger("websearch")

PAGE = 5
SEARCH_BACKENDS = ("auto", "bing", "brave", "google", "duckduckgo", "yahoo", "wikipedia")


async def ddg_search(query: str, n: int = 12) -> list[dict]:
    """جستجوی متا با ddgs؛ چند بک‌اند را امتحان می‌کند."""
    from ddgs import DDGS
    for backend in SEARCH_BACKENDS:
        try:
            def _run():
                with DDGS() as d:
                    return list(d.text(query, max_results=n, backend=backend))
            rows = await asyncio.wait_for(asyncio.to_thread(_run), timeout=25)
            if rows:
                return [{"title": r.get("title", ""), "url": r.get("href") or r.get("url", ""),
                         "snippet": r.get("body", "")} for r in rows if r.get("href") or r.get("url")]
        except Exception as e:
            log.debug("ddgs backend %s failed: %s", backend, e)
            continue
    return []


def result_caption(idx: int, r: dict) -> str:
    t = util.md_escape(r["title"]) or "بدون عنوان"
    snip = util.md_escape(util.short(r.get("snippet", ""), 90))
    return f"{idx}. *{t}*\n🔗 {util.md_link(r['url'], r['url'][:60])}\n_{snip}_"


def results_kb(bot: str, sid: str, count: int, page: int) -> dict:
    row = [btn(str(i + 1), f"{bot}:webopen:{sid}:{i}") for i in range(count)]
    rows = [row] if row else []
    rows.append([btn("🔁 ۵ نتیجه بعدی", f"{bot}:webmore:{sid}"),
                 btn("❌", f"{bot}:webclose:{sid}")])
    return kb(rows)


async def run_search(bot: Bot, chat_id: int, user_id: int, query: str):
    await bot.send_message(chat_id, txt.SEARCHING)
    await db.log_search(user_id, bot.name, "web", query)
    results = await ddg_search(query, n=PAGE * 3)
    if not results:
        await bot.send_message(chat_id, txt.NO_RESULTS)
        return
    five = results[:PAGE]
    sid = state.put_session({"query": query, "results": results, "offset": PAGE})
    await bot.send_chat_action(chat_id, "upload_photo")
    shots = await browser.shot_many([r["url"] for r in five])
    await _send_results(bot, chat_id, sid, five, shots)


async def _send_results(bot: Bot, chat_id: int, sid: str, five: list[dict], shots):
    # آلبوم تصاویر + یک پیام جداگانه با دکمه‌ها
    media, good = [], []
    for i, (r, sh) in enumerate(zip(five, shots)):
        if sh:
            media.append({"type": "photo", "media": "attach://p%d" % i,
                          "caption": result_caption(i, r)[:1000]})
            good.append((i, sh))
    sent_text = "🔎 *نتایج جستجو*\n\n" + "\n\n".join(result_caption(i, r) for i, r in enumerate(five))
    if good:
        files = {f"p{i}": ("s.jpg", __import__("io").BytesIO(sh)) for i, sh in good}
        try:
            await bot.call("sendMediaGroup", {
                "chat_id": chat_id,
                "media": __import__("json").dumps(media, ensure_ascii=False)}, files=files)
        except Exception:
            await bot.send_message(chat_id, sent_text, disable_preview=True)
    else:
        await bot.send_message(chat_id, sent_text, disable_preview=True)
    await bot.send_message(
        chat_id,
        "👇 روی *شماره* نتیجه بزن تا صفحه به‌صورت کامل (HTML + CSS + JS) برایت ذخیره شود:",
        reply_markup=results_kb(bot.name, sid, len(five), 0))


async def more_results(bot: Bot, chat_id: int, sid: str):
    s = state.get_session(sid)
    if not s:
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    s["_ts"] = __import__("time").time()
    res = s.get("results", [])
    start = s.get("offset", PAGE)
    if start >= len(res):
        # نتایج تازه بگیر و اضافه کن
        fresh = await ddg_search(s["query"], n=PAGE * 2)
        have = {r["url"] for r in res}
        fresh = [r for r in fresh if r["url"] not in have]
        res.extend(fresh)
        s["results"] = res
    five = res[start:start + PAGE]
    if not five:
        await bot.send_message(chat_id, "😕 نتیجه بیشتری پیدا نشد. عبارت دقیق‌تری بزن.")
        return
    s["offset"] = start + PAGE
    await bot.send_message(chat_id, txt.SEARCHING)
    await bot.send_chat_action(chat_id, "upload_photo")
    shots = await browser.shot_many([r["url"] for r in five])
    await _send_results(bot, chat_id, sid, five, shots)


# ---------- باز کردن/ذخیره کامل صفحه ----------
async def open_result(bot: Bot, chat_id: int, user_id: int, sid: str, idx: int):
    s = state.get_session(sid)
    if not s:
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    res = s.get("results", [])
    if not (0 <= idx < len(res)):
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    await fetch_and_deliver(bot, chat_id, user_id, res[idx]["url"], screenshot=True)


async def fetch_and_deliver(bot: Bot, chat_id: int, user_id: int, url: str,
                            screenshot: bool = True, mhtml: bool = True):
    """fetch بسیار حرفه‌ای: MHTML کامل (زنده با JS) + اسکرین‌شات، تحویل در قالب فایل."""
    if screenshot and mhtml:
        await bot.send_message(chat_id, txt.FETCHING)
    elif screenshot:
        await bot.send_message(chat_id, txt.SHOOTING)
    else:
        await bot.send_message(chat_id, txt.FETCHING)
    await bot.send_chat_action(chat_id, "upload_document")
    mhtml_bytes, shot = await browser.capture_page(url)
    name = browser.mhtml_filename(url)
    sent_any = False
    if shot:
        try:
            await bot.send_photo(chat_id, shot, caption=f"📸 اسکرین‌شات: {util.md_link(url, url[:70])}",
                                 disable_preview=None)
            sent_any = True
        except Exception:
            pass
    if mhtml_bytes:
        try:
            await bot.send_document(chat_id, mhtml_bytes, filename=name,
                                    caption=("🌐 *صفحه کامل ذخیره شد* (HTML+CSS+JS زنده)\n"
                                             "با Chrome/Edge باز کن:\n" + util.md_link(url, url[:70])))
            sent_any = True
        except Exception:
            pass
    if not sent_any:
        await bot.send_message(chat_id,
                               "😕 ذخیره‌سازی ممکن نشد (سایت ممکن است اسکرپینگ را مسدود کرده باشد).",
                               disable_preview=True)


# ---------- لینک شخصی: فقط اسکرین‌شات / فقط fetch / هر دو ----------
def ask_link_options(bot: str, url: str) -> dict:
    sid = state.put_session({"url": url})
    return kb([
        [btn("📸 فقط اسکرین‌شات", f"{bot}:lnk:shot:{sid}"),
         btn("🌐 فقط ذخیره صفحه", f"{bot}:lnk:fetch:{sid}")],
        [btn("🔀 هر دو", f"{bot}:lnk:both:{sid}")],
    ])
