"""بخش یوتیوب: جستجو مثل موتور جستجوی یوتیوب (۵ ویدیو با تامبنیل) + دانلود با کیفیت دلخواه."""
import logging

import db
import state
import txt
import util
import ytdlp
from baleapi import Bot, btn, kb

log = logging.getLogger("yt")

PAGE = 5
QUALITIES = [("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)]


def _dur(sec) -> str:
    return ytdlp.fmt_duration(sec)


def _caption(i: int, item: dict) -> str:
    t = util.md_escape(util.short(item["title"], 70))
    ch = util.md_escape(item["channel"])
    views = ""
    if item.get("view_count"):
        try:
            views = f" | 👁 {int(item['view_count']):,} "
        except (TypeError, ValueError):
            pass
    return (f"{i + 1}. *{t}*\n👤 {ch}{views}| ⏱ {_dur(item['duration'])}\n"
            f"🔗 {util.md_link(item['url'], 'ویدیو')}")


def results_kb(bot: str, sid: str, count: int) -> dict:
    row = [btn(str(i + 1), f"{bot}:ytm:{sid}:{i}") for i in range(count)]
    rows = [row] if row else []
    rows.append([btn("🔁 ۵ ویدیوی بعدی", f"{bot}:ytmore:{sid}"),
                 btn("❌", f"{bot}:webclose:{sid}")])
    return kb(rows)


async def run_search(bot: Bot, chat_id: int, user_id: int, query: str):
    await bot.send_message(chat_id, txt.SEND_QUERY["yt"] if not query else txt.SEARCHING)
    await db.log_search(user_id, bot.name, "yt", query)
    items = await ytdlp.yt_search(query, n=PAGE * 2)
    if not items:
        await bot.send_message(chat_id,
                               "😕 نتیجه‌ای پیدا نشد.\n(یوتیوب گاهی سرچ را محدود می‌کند؛ کمی بعد دوباره امتحان کن)")
        return
    five = items[:PAGE]
    sid = state.put_session({"query": query, "items": items, "offset": PAGE})
    await _send_results(bot, chat_id, sid, five)


async def _send_results(bot: Bot, chat_id: int, sid: str, five: list[dict]):
    await bot.send_chat_action(chat_id, "upload_photo")
    # آلبوم تامبنیل‌ها به صورت عکس (برای انتخاب راحت‌تر کاربر)
    media, files, good = [], {}, []
    import io
    for i, item in enumerate(five):
        cap = _caption(i, item)
        if item.get("thumbnail"):
            th = await util.fetch_bytes(item["thumbnail"], timeout=15)
            if th:
                media.append({"type": "photo", "media": f"attach://t{i}", "caption": cap[:1000]})
                files[f"t{i}"] = ("t.jpg", io.BytesIO(th))
                good.append(i)
    if media:
        import json
        try:
            await bot.call("sendMediaGroup", {"chat_id": chat_id,
                                              "media": json.dumps(media, ensure_ascii=False)},
                           files=files)
        except Exception:
            await bot.send_message(chat_id, "\n\n".join(_caption(i, it) for i, it in enumerate(five)),
                                   disable_preview=True)
    else:
        await bot.send_message(chat_id, "\n\n".join(_caption(i, it) for i, it in enumerate(five)),
                               disable_preview=True)
    await bot.send_message(chat_id, "👇 برای دانلود روی *شماره ویدیو* بزن:",
                           reply_markup=results_kb(bot.name, sid, len(five)))


async def more_results(bot: Bot, chat_id: int, sid: str):
    s = state.get_session(sid)
    if not s:
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    items = s.get("items", [])
    start = s.get("offset", PAGE)
    if start >= len(items):
        fresh = await ytdlp.yt_search(s["query"], n=PAGE)
        have = {it["url"] for it in items}
        items.extend([f for f in fresh if f["url"] not in have])
        s["items"] = items
    five = items[start:start + PAGE]
    if not five:
        await bot.send_message(chat_id, "😕 ویدیوی بیشتری پیدا نشد.")
        return
    s["offset"] = start + PAGE
    await _send_results(bot, chat_id, sid, five)


async def video_menu(bot: Bot, chat_id: int, sid: str, idx: int):
    """منوی کیفیت برای ویدیوی انتخاب‌شده."""
    s = state.get_session(sid)
    if not s:
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    items = s.get("items", [])
    if not (0 <= idx < len(items)):
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    item = items[idx]
    await bot.send_message(chat_id, txt.PROBING)
    info = await ytdlp.probe(item["url"])
    sizes = {}
    if info:
        sizes, _a = ytdlp.best_formats(info)
    rows = []
    label = {"1080": "۱۰۸۰p", "720": "۷۲۰p", "480": "۴۸۰p", "360": "۳۶۰p"}
    for name, h in QUALITIES:
        if sizes and h not in sizes and max(sizes or {0: 0}) < h:
            continue
        size_txt = ""
        if sizes.get(h):
            size_txt = f" (~{ytdlp.human_size(sizes[h])})"
        rows.append([btn(f"🎬 ویدیو {label[str(h)]}{size_txt}",
                         f"{bot}:ytd:{sid}:{idx}:{h}")])
    rows.append([btn("🎵 فقط صدا (m4a)", f"{bot}:ytd:{sid}:{idx}:audio")])
    rows.append([btn("بازگشت", f"{bot}:ytnone")])
    txt_msg = txt.YT_MENU.format(
        title=util.md_escape(util.short(item["title"], 60)),
        channel=util.md_escape(item["channel"]) or "؟",
        duration=_dur(item["duration"]))
    await bot.send_message(chat_id, txt_msg, reply_markup=kb(rows))
