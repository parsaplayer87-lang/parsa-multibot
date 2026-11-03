"""هندلرهای مشترک هر سه بازو: /start، منوی مدیریت، مسیریابی پیام‌ها و دکمه‌ها.

الگوی callback:  <bot>:<op>:<args...>
bot ∈ main|backup|admin — جلوگیری از تداخل دکمه‌های قدیمی بین بازوها.
"""
import asyncio
import datetime as dt
import logging

import admin as admin_panel
import browser
import db
import dqueue
import gh
import state
import txt
import tools
import util
import websearch
import ytdlp
import ytsearch
from baleapi import Bot, btn, kb

log = logging.getLogger("handlers")

START_TS = dt.datetime.now(dt.timezone.utc)


# ============================================================ منوها
def main_menu(bot: str, is_admin: bool) -> dict:
    rows = [
        [btn("📥 دانلودر همه‌چیز", f"{bot}:menu:downloader"),
         btn("🔍 جستجوی وب", f"{bot}:menu:websearch")],
        [btn("▶️ یوتیوب", f"{bot}:menu:yt"),
         btn("🐙 گیت‌هاب", f"{bot}:menu:gh")],
        [btn("🧰 ابزارها", f"{bot}:menu:tools")],
        [btn("⚙️ مدیریت", f"{bot}:menu:manage")],
    ]
    if is_admin:
        rows.append([btn("🛡 پنل ادمین", f"{bot}:adm:root")])
    return kb(rows)


def manage_menu(bot: str, is_admin: bool) -> dict:
    rows = [
        [btn("📥 دانلودر", f"{bot}:menu:downloader"),
         btn("🔍 سرچ وب", f"{bot}:menu:websearch")],
        [btn("▶️ یوتیوب", f"{bot}:menu:yt"),
         btn("🐙 گیت‌هاب", f"{bot}:menu:gh")],
        [btn("🧰 ابزارها", f"{bot}:menu:tools")],
        [btn("🤖 ربات پشتیبان", url=f"https://ble.ir/prsdownbackupbot"),
         btn("📖 راهنما", f"{bot}:menu:help")],
    ]
    if is_admin:
        rows.append([btn("🛡 پنل ادمین", f"{bot}:adm:root")])
    rows.append([btn("🔙 بازگشت", f"{bot}:menu:root")])
    return kb(rows)


def tools_menu(bot: str) -> dict:
    return kb([
        [btn("🧩 QR code", f"{bot}:menu:qr")],
        [btn("🌦 آب‌وهوا", f"{bot}:menu:weather"),
         btn("🌐 ترجمه", f"{bot}:menu:translate")],
        [btn("🔙 بازگشت", f"{bot}:menu:root")],
    ])


# ============================================================ guard
async def guard_msg(bot: Bot, msg: dict) -> tuple[int, dict] | None:
    """بررسی بن/اسپم؛ اطلاعات کاربر را ثبت می‌کند. None = نادیده."""
    user = msg.get("from") or {}
    uid = user.get("id", 0)
    chat = msg.get("chat", {})
    if not uid or chat.get("type") != "private":  # فقط چت خصوصی
        return None
    await db.upsert_user(uid, bot.name, user.get("first_name", ""), user.get("username", ""))
    if await db.is_banned(uid, bot.name):
        return None
    if not state.check_spam(bot.name, uid):
        return None
    return uid, msg


async def guard_cb(bot: Bot, cq: dict) -> tuple[int, dict] | None:
    user = cq.get("from") or {}
    uid = user.get("id", 0)
    if not uid:
        return None
    if await db.is_banned(uid, bot.name):
        await bot.answer_callback(cq.get("id", ""))
        return None
    return uid, cq


async def feat_on(bot: Bot, uid: int, feat: str) -> bool:
    return await db.feature_enabled(bot.name, feat)


# ============================================================ پیام‌ها
async def on_message(bot: Bot, msg: dict):
    g = await guard_msg(bot, msg)
    if not g:
        return
    uid, m = g
    chat_id = m["chat"]["id"]
    text = (m.get("text") or "").strip()
    caption = m.get("caption") or ""

    # لاگ کامل همه ورودی‌ها (دیتای حیاتی)
    if text:
        await db.log_message(uid, bot.name, m.get("message_id"), "text", text)
    elif caption:
        await db.log_message(uid, bot.name, m.get("message_id"), "caption", caption)
    elif m.get("photo"):
        await db.log_message(uid, bot.name, m.get("message_id"), "photo", "")
    elif m.get("video"):
        await db.log_message(uid, bot.name, m.get("message_id"), "video", "")
    elif m.get("document"):
        await db.log_message(uid, bot.name, m.get("message_id"),
                             "document", (m.get("document") or {}).get("file_name", ""))
    elif m.get("audio"):
        await db.log_message(uid, bot.name, m.get("message_id"), "audio", "")
    else:
        await db.log_message(uid, bot.name, m.get("message_id"), "other", "")

    # ادمین: اولین /start روی بازوی ادمین = claim
    if bot.name == "admin" and not await admin_panel.is_admin(bot, uid):
        claimed = await admin_panel.try_claim(bot, uid)
        if claimed:
            await bot.send_message(chat_id, txt.ADMIN["claim_ok"])
            await bot.send_message(chat_id, txt.ADMIN["panel"],
                                   reply_markup=admin_panel.main_menu(bot.name))
        else:
            await bot.send_message(chat_id, txt.ADMIN["denied"])
        return

    # دستورها
    if text.startswith("/"):
        await handle_command(bot, uid, chat_id, text, m)
        return

    # حالت‌های مکالمه فعال
    st = state.get_state(bot.name, uid)
    mode = st["mode"] if st else ""
    data = st["data"] if st else {}

    if mode == "broadcast" and await admin_panel.is_admin(bot, uid):
        state.clear_state(bot.name, uid)
        await bot.send_message(chat_id, txt.ADMIN["broadcast_start"])
        ok, fail = await admin_panel.broadcast(bot, chat_id, m)
        await bot.send_message(chat_id, txt.ADMIN["broadcast_done"].format(ok=ok, fail=fail))
        return

    # هر پیام لینک‌دار → مسیر دانلودر/وب/گیت‌هاب
    url = util.extract_url(text or caption)
    if url:
        await handle_url(bot, uid, chat_id, url, m)
        return

    # متن سرچ/ابزار
    if mode in ("websearch", "yt", "gh"):
        state.clear_state(bot.name, uid)
        try:
            if mode == "websearch":
                await websearch.run_search(bot, chat_id, uid, text)
            elif mode == "yt":
                await ytsearch.run_search(bot, chat_id, uid, text)
            elif mode == "gh":
                await gh.search_and_show(bot, chat_id, uid, text)
        except Exception:
            log.exception("search failed")
            await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return

    if mode == "qr":
        state.clear_state(bot.name, uid)
        await tools.run_qr(bot, chat_id, uid, text)
        return
    if mode == "weather":
        state.clear_state(bot.name, uid)
        await tools.run_weather(bot, chat_id, uid, text)
        return
    if mode == "translate":
        state.clear_state(bot.name, uid)
        await tools.run_translate(bot, chat_id, uid, text)
        return

    await bot.send_message(chat_id, txt.MAIN_MENU_HINT, reply_markup=main_menu(bot.name, await is_adm(bot, uid)))


async def is_adm(bot: Bot, uid: int) -> bool:
    return await admin_panel.is_admin(bot, uid)


async def handle_command(bot: Bot, uid: int, chat_id: int, text: str, msg: dict):
    cmd = text.split()[0].split("@")[0].lower()
    adm = await is_adm(bot, uid)
    if cmd in ("/start", "/menu"):
        state.clear_state(bot.name, uid)
        await bot.send_message(chat_id, txt.WELCOME.format(
            name=util.md_escape((msg.get("from") or {}).get("first_name", "دوست"))),
            reply_markup=main_menu(bot.name, adm), disable_preview=True)
    elif cmd == "/help":
        await bot.send_message(chat_id, txt.HELP, disable_preview=True)
    elif cmd == "/cancel":
        state.clear_state(bot.name, uid)
        await bot.send_message(chat_id, txt.CANCELLED_STATE,
                               reply_markup=main_menu(bot.name, adm))
    elif cmd == "/id":
        await bot.send_message(chat_id, f"🆔 آیدی تو: `{uid}`")
    elif cmd == "/panel" and adm:
        await bot.send_message(chat_id, txt.ADMIN["panel"],
                               reply_markup=admin_panel.main_menu(bot.name))
    elif cmd == "/stats" and adm:
        await bot.send_message(chat_id, await admin_panel.stats_text(bot))
    elif cmd == "/users" and adm:
        t, pages = await admin_panel.users_text(0)
        await bot.send_message(chat_id, t, reply_markup=admin_panel.users_kb(bot.name, 0, pages))
    elif cmd == "/broadcast" and adm:
        state.set_state(bot.name, uid, "broadcast")
        await bot.send_message(chat_id, txt.ADMIN["broadcast_ask"])
    elif adm and cmd.startswith("/user"):
        parts = text.split()
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            t = await admin_panel.user_detail(int(parts[1]))
            await bot.send_message(chat_id, t, reply_markup=admin_panel.user_menu(bot.name, int(parts[1])))
        else:
            await bot.send_message(chat_id, "استفاده: /user <آیدی>")
    else:
        await bot.send_message(chat_id, "❓ دستور ناشناخته. /help")


async def handle_url(bot: Bot, uid: int, chat_id: int, url: str, msg: dict):
    st = state.get_state(bot.name, uid)
    mode = st["mode"] if st else ""
    # لینک گیت‌هاب؟ → دانلودر گیت‌هاب
    repo = util.github_repo(url)
    if repo:
        try:
            await gh.repo_preview(bot, chat_id, uid, repo[0], repo[1])
        except Exception:
            log.exception("gh preview")
            await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    # در حالت سرچ وب یا لینک شخصی → گزینه اسکرین‌شات/فچ/هر دو
    if mode == "websearch" or mode == "web":
        state.clear_state(bot.name, uid)
        await bot.send_message(chat_id, txt.GOT_LINK_QUESTION,
                               reply_markup=websearch.ask_link_options(bot.name, url))
        return
    # بقیه لینک‌ها → دانلودر همه‌چیز (پیش‌نمایش + تأیید)
    await downloader_preview(bot, uid, chat_id, url)


# ============================================================ دانلودر
async def downloader_preview(bot: Bot, uid: int, chat_id: int, url: str):
    await bot.send_message(chat_id, txt.PROBING)
    await bot.send_chat_action(chat_id, "typing")
    info = await ytdlp.probe(url)
    if not info:
        # شاید صفحه وب معمولی است → پیشنهاد ذخیره صفحه
        await bot.send_message(
            chat_id,
            "😕 مشخصات مدیوم پیدا نشد.\nاگر این یک *صفحه وب* است می‌توانم ذخیره یا اسکرین‌شات بگیرم:",
            reply_markup=websearch.ask_link_options(bot.name, url), disable_preview=True)
        return
    platform = util.platform_name(info, url)
    title = util.md_escape(util.short(info.get("title") or "بدون عنوان", 70))
    duration = ytdlp.fmt_duration(info.get("duration"))
    sizes, audio_size = ytdlp.best_formats(info)

    # تامبنیل
    thumb = await util.fetch_bytes(info.get("thumbnail") or "", timeout=15) if info.get("thumbnail") else None
    sid = state.put_session({"url": url, "info_title": title, "platform": platform,
                             "sizes": sizes, "audio_size": audio_size,
                             "is_video": bool(sizes)})

    extra = ""
    if info.get("uploader"):
        extra += f"👤 ناشر: {util.md_escape(util.short(str(info.get('uploader')), 40))}\n"
    caption = txt.DL_PREVIEW.format(title=title, platform=platform, extra=extra, duration=duration)

    label = {"1080": "۱۰۸۰p", "720": "۷۲۰p", "480": "۴۸۰p", "360": "۳۶۰p"}
    rows = []
    if sizes:
        for h in sorted(sizes, reverse=True):
            if h < 144:
                continue
            size_txt = f" (~{ytdlp.human_size(sizes[h])})" if sizes[h] else ""
            rows.append([btn(f"🎬 ویدیو {label.get(str(h), str(h) + 'p')}{size_txt}",
                             f"{bot}:dlv:{sid}:{h}")])
    rows.append([btn(f"🎵 فقط صدا{f' (~{ytdlp.human_size(audio_size)})' if audio_size else ''}",
                     f"{bot}:dla:{sid}")])
    rows.append([btn("❌ انصراف", f"{bot}:dlx:{sid}")])
    if thumb:
        try:
            await bot.send_photo(chat_id, thumb, caption=caption, reply_markup=kb(rows))
            return
        except Exception:
            pass
    await bot.send_message(chat_id, caption, reply_markup=kb(rows), disable_preview=True)


async def enqueue_download(bot: Bot, uid: int, chat_id: int, sid: str, kind: str, quality: str):
    s = state.get_session(sid)
    if not s:
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    url = s["url"]
    fmt = ytdlp.AUDIO_FORMAT_STRING if kind == "audio" else ytdlp.video_format_string(int(quality))
    try:
        dl_id = await db.add_download(uid, bot.name, url, platform=s.get("platform", ""),
                                      title=s.get("info_title", ""), kind=kind,
                                      quality=quality)
        status = await bot.send_message(chat_id, "⏳ در حال قرارگیری در صف...")
        job = dqueue.Job(dl_id, bot, chat_id, uid, url, fmt, kind, quality,
                         s.get("info_title", url), s.get("platform", ""),
                         status_msg_id=status["message_id"])
        pos = await dqueue.queue.enqueue(job)
        state.clear_state(bot.name, uid)
        await bot.edit_message(chat_id, status["message_id"],
                               txt.QUEUED.format(pos=pos) + f"\n\n🆔 job: {dl_id}",
                               reply_markup=kb([[btn("🚫 لغو دانلود", f"{bot}:qc:{dl_id}")]]))
    except RuntimeError:
        await bot.send_message(chat_id, "⛔️ حداکثر ۲ دانلود همزمان برای هر کاربر. کمی بعد تلاش کن.")
    except Exception:
        log.exception("enqueue failed")
        await bot.send_message(chat_id, txt.GENERIC_ERROR)


# ============================================================ دکمه‌ها
async def on_callback(bot: Bot, cq: dict):
    g = await guard_cb(bot, cq)
    if not g:
        return
    uid, q = g
    chat = (q.get("message") or {}).get("chat") or {}
    chat_id = chat.get("id")
    mid = (q.get("message") or {}).get("message_id")
    data = q.get("data", "")
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != bot.name:
        await bot.answer_callback(q.get("id", ""))
        return
    await db.log_message(uid, bot.name, mid or 0, "callback", data)

    async def alert(t: str, show: bool = False):
        await bot.answer_callback(q.get("id", ""), t, show)

    op = parts[1]

    try:
        # ---------- منوها ----------
        if op == "menu":
            sub = parts[2] if len(parts) > 2 else "root"
            adm = await is_adm(bot, uid)
            if sub == "root":
                await bot.edit_message(chat_id, mid, txt.MAIN_MENU_HINT,
                                       reply_markup=main_menu(bot.name, adm))
            elif sub == "manage":
                await bot.edit_message(chat_id, mid, txt.MANAGE_TITLE,
                                       reply_markup=manage_menu(bot.name, adm))
            elif sub == "help":
                await bot.edit_message(chat_id, mid, txt.HELP,
                                       reply_markup=kb([[btn("🔙 بازگشت", f"{bot}:menu:root")]]))
            elif sub == "tools":
                await bot.edit_message(chat_id, mid, "🧰 *ابزارها*",
                                       reply_markup=tools_menu(bot.name))
            else:
                feat = {"downloader": "downloader", "websearch": "websearch",
                        "yt": "yt", "gh": "gh"}.get(sub)
                if feat and not await feat_on(bot, uid, feat):
                    await alert(txt.FEAT_OFF, True)
                    return
                prompts = {"downloader": "📥 *دانلودر همه‌چیز*\n\nلینک (یوتیوب، اینستاگرام، پینترست، تیک‌تاک و ...) رو بفرست:",
                           "websearch": txt.SEND_QUERY["websearch"],
                           "yt": txt.SEND_QUERY["yt"],
                           "gh": txt.SEND_QUERY["gh"]}
                state.set_state(bot.name, uid, sub if sub != "downloader" else "downloader")
                await bot.edit_message(chat_id, mid, prompts[sub])
            await alert()
            return

        # ---------- سرچ وب ----------
        if op == "webopen" and await feat_on(bot, uid, "websearch"):
            await alert("🌐 دارم صفحه رو ذخیره می‌کنم...")
            await websearch.open_result(bot, chat_id, uid, parts[2], int(parts[3]))
            return
        if op == "webmore" and await feat_on(bot, uid, "websearch"):
            await alert("🔍 دارم نتایج بعدی رو می‌گیرم...")
            await websearch.more_results(bot, chat_id, parts[2])
            return
        if op == "webclose":
            state.clear_state(bot.name, uid)
            with __import__("contextlib").suppress(Exception):
                await bot.delete_message(chat_id, mid)
            await alert("بسته شد ✅")
            return
        if op == "lnk":
            sid = parts[3]
            s = state.get_session(sid)
            url = s.get("url") if s else None
            if not url:
                await bot.send_message(chat_id, txt.GENERIC_ERROR)
                await alert()
                return
            action = parts[2]
            state.clear_state(bot.name, uid)
            await alert("⏳ شروع شد...")
            if action == "shot":
                await websearch.fetch_and_deliver(bot, chat_id, uid, url, screenshot=True, mhtml=False)
            elif action == "fetch":
                await websearch.fetch_and_deliver(bot, chat_id, uid, url, screenshot=False, mhtml=True)
            else:
                await websearch.fetch_and_deliver(bot, chat_id, uid, url, screenshot=True, mhtml=True)
            return

        # ---------- یوتیوب ----------
        if op == "ytm" and await feat_on(bot, uid, "yt"):
            await alert("🔎 دارم مشخصات رو می‌گیرم...")
            await ytsearch.video_menu(bot, chat_id, parts[2], int(parts[3]))
            return
        if op == "ytmore" and await feat_on(bot, uid, "yt"):
            await alert("🔍 دارم ویدیوهای بعدی رو می‌گیرم...")
            await ytsearch.more_results(bot, chat_id, parts[2])
            return
        if op == "ytd" and await feat_on(bot, uid, "yt"):
            sid, idx, q_ = parts[2], int(parts[3]), parts[4]
            kind = "audio" if q_ == "audio" else "video"
            quality = "audio" if q_ == "audio" else q_
            s = state.get_session(sid)
            if s:
                it = (s.get("items") or [])[idx]
                s["url"] = it["url"]
                s["info_title"] = util.md_escape(util.short(it["title"], 60))
                s["platform"] = "یوتیوب"
            await alert("✅ در صف دانلود قرار گرفت")
            await enqueue_download(bot, uid, chat_id, sid, kind, quality)
            return
        if op == "ytnone":
            await alert()
            return

        # ---------- گیت‌هاب ----------
        if op == "ghd" and await feat_on(bot, uid, "gh"):
            await alert("⏳ در حال آماده‌سازی ZIP...")
            await gh.send_zip(bot, chat_id, uid, parts[2])
            return

        # ---------- دانلودر ----------
        if op == "dlv" and await feat_on(bot, uid, "downloader"):
            await alert("✅ در صف قرار گرفت")
            await enqueue_download(bot, uid, chat_id, parts[2], "video", parts[3])
            return
        if op == "dla" and await feat_on(bot, uid, "downloader"):
            await alert("✅ در صف قرار گرفت")
            await enqueue_download(bot, uid, chat_id, parts[2], "audio", "audio")
            return
        if op == "dlx":
            state.clear_state(bot.name, uid)
            with __import__("contextlib").suppress(Exception):
                await bot.delete_message(chat_id, mid)
            await alert("لغو شد")
            return
        if op == "qc":
            job_id = int(parts[2])
            if dqueue.queue.cancel_job(job_id):
                await alert("🚫 لغو شد")
            else:
                await alert("این دانلود در صف نیست یا تمام شده.", True)
            return

        # ---------- پنل ادمین ----------
        if op == "adm":
            await handle_admin_cb(bot, uid, q, chat_id, mid, parts)
            return

        await alert()
    except Exception:
        log.exception("callback error: %s", data)
        with __import__("contextlib").suppress(Exception):
            await bot.send_message(chat_id, txt.GENERIC_ERROR)
        await bot.answer_callback(q.get("id", ""))


# ============================================================ ادمین
async def handle_admin_cb(bot: Bot, uid: int, q: dict, chat_id: int, mid: int, parts: list[str]):
    if not await admin_panel.is_admin(bot, uid):
        await bot.answer_callback(q.get("id", ""), txt.ADMIN["denied"], True)
        return

    async def alert(t: str = "", show: bool = False):
        await bot.answer_callback(q.get("id", ""), t, show)

    sub = parts[2] if len(parts) > 2 else "root"

    if sub == "root":
        await bot.edit_message(chat_id, mid, txt.ADMIN["panel"],
                               reply_markup=admin_panel.main_menu(bot.name))
        await alert()
    elif sub == "stats":
        await bot.edit_message(chat_id, mid, await admin_panel.stats_text(bot),
                               reply_markup=kb([[btn("🔄", f"{bot.name}:adm:stats"),
                                                 btn("🔙 منو", f"{bot.name}:adm:root")]]))
        await alert()
    elif sub == "users":
        page = int(parts[3]) if len(parts) > 3 else 0
        t, pages = await admin_panel.users_text(page)
        await bot.edit_message(chat_id, mid, t,
                               reply_markup=admin_panel.users_kb(bot.name, page, pages))
        await alert()
    elif sub == "u":  # جزئیات کاربر: adm:u:<uid>
        target = int(parts[3])
        await bot.edit_message(chat_id, mid, await admin_panel.user_detail(target),
                               reply_markup=admin_panel.user_menu(bot.name, target))
        await alert()
    elif sub == "ulog":  # adm:ulog:<uid>:<page>
        target = int(parts[3]); page = int(parts[4])
        await bot.edit_message(chat_id, mid, await admin_panel.user_log_text(target, page),
                               reply_markup=kb([
                                   [btn("◀️", f"{bot.name}:adm:ulog:{target}:{max(0, page - 1)}"),
                                    btn("▶️", f"{bot.name}:adm:ulog:{target}:{page + 1}")],
                                   [btn("🔙 کاربر", f"{bot.name}:adm:u:{target}")]]))
        await alert()
    elif sub == "udl":
        target = int(parts[3])
        dls = await db.user_downloads(target, None, 15)
        u = await db.get_user(target)
        name = util.md_escape(u["first_name"] if u else str(target))
        rows = [f"• {util.md_escape(util.short(d['title'] or d['url'], 50))} — {d['status']} ({d['ts_start'][:16]})\n  _{d['kind']} {d['quality']}_"
                for d in dls]
        await bot.edit_message(chat_id, mid,
                               f"📥 دانلودهای *{name}*:\n\n" + ("\n".join(rows) or "—"),
                               reply_markup=kb([[btn("🔙 کاربر", f"{bot.name}:adm:u:{target}")]]))
        await alert()
    elif sub == "uexp":
        target = int(parts[3])
        buf = await admin_panel.export_user(target)
        if buf:
            await bot.send_document(chat_id, buf.getvalue(), filename=f"user_{target}.json",
                                    caption=txt.ADMIN["exported"])
        await alert()
    elif sub == "uall":
        buf = await admin_panel.export_all_users_csv()
        await bot.send_document(chat_id, buf.getvalue(), filename="users.csv",
                                caption="👥 خروجی CSV همه کاربران")
        await alert()
    elif sub == "ban":
        target = int(parts[3])
        u = await db.get_user(target)
        newb = not (u and u["banned"])
        for bk in ("main", "backup", "admin"):
            await db.set_ban(target, bk, newb)
        await bot.edit_message(chat_id, mid,
                               await admin_panel.user_detail(target),
                               reply_markup=admin_panel.user_menu(bot.name, target))
        await alert("🚫 بن شد" if newb else "✅ رفع بن شد")
    elif sub == "log":
        page = int(parts[3]) if len(parts) > 3 else 0
        limit = 12
        rows = await db.recent_messages(limit * (page + 1))
        page_rows = rows[page * limit:(page + 1) * limit]
        lines = []
        for m in page_rows:
            u = await db.get_user(m["user_id"])
            name = util.md_escape(u["first_name"] if u else str(m["user_id"]))
            t = util.md_escape(util.short(m["text"] or "", 50)) or f"({m['kind']})"
            lines.append(txt.ADMIN["msg_row"].format(name=name, uid=m["user_id"], text=t))
        total_pages = 500
        await bot.edit_message(chat_id, mid,
                               txt.ADMIN["msg_log_title"].format(page=page + 1, pages="∞",
                                                                 rows="\n".join(lines) or "—"),
                               reply_markup=kb([
                                   [btn("◀️", f"{bot.name}:adm:log:{max(0, page - 1)}"),
                                    btn("▶️", f"{bot.name}:adm:log:{page + 1}")],
                                   [btn("🔙 منو", f"{bot.name}:adm:root")]]))
        await alert()
    elif sub == "dls":
        rows = await db.query(
            "SELECT d.*, u.first_name FROM downloads d LEFT JOIN users u "
            "ON d.user_id=u.user_id AND u.bot=d.bot ORDER BY d.id DESC LIMIT 20")
        lines = []
        for d in rows:
            nm = util.md_escape(d["first_name"] or str(d["user_id"]))
            lines.append(txt.ADMIN["dl_row"].format(
                name=nm, title=util.md_escape(util.short(d["title"] or d["url"], 40)),
                status=d["status"]))
        await bot.edit_message(chat_id, mid,
                               txt.ADMIN["dl_log_title"].format(rows="\n".join(lines) or "—"),
                               reply_markup=kb([[btn("🔙 منو", f"{bot.name}:adm:root")]]))
        await alert()
    elif sub == "bc":
        state.set_state(bot.name, uid, "broadcast")
        await bot.send_message(chat_id, txt.ADMIN["broadcast_ask"])
        await alert()
    elif sub == "feat":
        await admin_panel.show_features(bot, chat_id, mid, bot.name)
        await alert()
    elif sub == "ft":  # adm:ft:<bot>:<feat>
        target_bot, feat = parts[3], parts[4]
        en = not await db.feature_enabled(target_bot, feat)
        await db.set_feature(target_bot, feat, en)
        await admin_panel.show_features(bot, chat_id, mid, bot.name)
        await alert(f"{config.BOT_NAMES[target_bot]} — {'روشن ✅' if en else 'خاموش ❌'}")
    elif sub == "web":
        url = admin_panel.miniapp_url()
        if url:
            await bot.send_message(
                chat_id,
                "🌐 *پنل ادمین مینی‌اپ*\n\nلینک زیر را باز کن (کلید داخل لینک است):\n" +
                util.md_link(url, "باز کردن پنل مینی‌اپ") +
                "\n\nاگر مینی‌اپ باز نشد، همین پنل پیامی جایگزین کامل آن است.")
            rows = [[btn("🚀 باز کردن مینی‌اپ", url=url)]]
            await bot.send_message(chat_id, "یا با دکمه:", reply_markup=kb(rows))
        else:
            await bot.send_message(chat_id,
                                   "⚠️ مینی‌اپ تنظیم نشده است (ADMIN_WEB_KEY/Pages).\n"
                                   "همین پنل پیامی، جایگزین کامل مینی‌اپ است.")
        await alert()
    elif sub == "close":
        with __import__("contextlib").suppress(Exception):
            await bot.delete_message(chat_id, mid)
        await alert()
    else:
        await alert()


# ============================================================ web_app_data (مینی‌اپ)
async def on_webapp(bot: Bot, msg: dict):
    user = msg.get("from") or {}
    uid = user.get("id", 0)
    chat_id = msg["chat"]["id"]
    data = (msg.get("web_app_data") or {}).get("data", "")
    await db.log_message(uid, bot.name, msg.get("message_id"), "webapp", data)
    if not await admin_panel.is_admin(bot, uid):
        await bot.send_message(chat_id, txt.ADMIN["denied"])
        return
    # دستور از مینی‌اپ: "cmd:stats" یا متن آزاد
    text = data.strip()
    if text.startswith("cmd:"):
        text = "/" + text[4:]
    fake = {"from": user, "chat": msg["chat"], "message_id": msg.get("message_id"),
            "text": text if text.startswith("/") else "/panel"}
    if text.startswith("/"):
        await handle_command(bot, uid, chat_id, text, fake)
    else:
        await bot.send_message(chat_id, f"📩 از مینی‌اپ:\n{text}",
                               reply_markup=admin_panel.main_menu(bot.name))
