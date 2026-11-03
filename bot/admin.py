"""پنل ادمین: آمار، لیست کاربران با جزئیات کامل، لاگ پیام‌ها، ارسال همگانی،
مدیریت قابلیت‌ها، بن، خروجی CSV/JSON، مینی‌اپ.
همه چیز به شکل inline keyboard پیامی (پشتیبان کامل مینی‌اپ).
"""
import asyncio
import csv
import io
import json
import logging
import time

import config
import db
import state
import txt
import util
from baleapi import Bot, btn, kb

log = logging.getLogger("admin")


async def is_admin(bot: Bot, user_id: int) -> bool:
    aid = await db.admin_id()
    return bool(aid) and user_id == aid


async def try_claim(bot: Bot, user_id: int) -> bool:
    return await db.claim_admin(user_id)


# ---------------- منوها ----------------
def main_menu(bot: str) -> dict:
    return kb([
        [btn("📊 آمار کلی", f"{bot}:adm:stats"),
         btn("👥 کاربران", f"{bot}:adm:users:0")],
        [btn("📜 لاگ پیام‌ها", f"{bot}:adm:log:0"),
         btn("📥 لاگ دانلودها", f"{bot}:adm:dls")],
        [btn("📣 ارسال همگانی", f"{bot}:adm:bc"),
         btn("🔌 قابلیت‌ها", f"{bot}:adm:feat")],
        [btn("🌐 مینی‌اپ پنل ادمین", f"{bot}:adm:web")],
        [btn("❌ بستن", f"{bot}:adm:close")],
    ])


async def stats_text(bot: Bot) -> str:
    s = await db.stats()
    try:
        import main as _m
        up = int(time.time() - _m.START_TS)
        h, rem = divmod(up, 3600)
        m, _ = divmod(rem, 60)
        uptime = f"{h}h {m}m"
        q = _m.queue
        uptime += f"\n⚙️ صف: {q.q.qsize()} در انتظار | {len(q.active)} فعال"
    except Exception:
        uptime = "؟"
    return txt.ADMIN["stats"].format(uptime=uptime, **s)


def users_kb(bot: str, page: int, pages: int) -> dict:
    rows = [[btn("◀️ قبلی", f"{bot}:adm:users:{page - 1}"),
             btn(f"{page + 1}/{pages}", f"{bot}:adm:noop"),
             btn("بعدی ▶️", f"{bot}:adm:users:{page + 1}")]]
    if page == 0:
        rows = [[btn("بعدی ▶️", f"{bot}:adm:users:{page + 1}")]]
    elif page >= pages - 1:
        rows = [[btn("◀️ قبلی", f"{bot}:adm:users:{page - 1}")]]
    rows.append([btn("🔙 منو", f"{bot}:adm:root")])
    return kb(rows)


async def users_text(page: int) -> tuple[str, int]:
    limit = 8
    total = await db.count_users()
    pages = max(1, (total + limit - 1) // limit)
    rows_db = await db.list_users(offset=page * limit, limit=limit)
    rows = []
    for i, u in enumerate(rows_db, start=page * limit + 1):
        name = util.md_escape(u["first_name"] or u["username"] or "کاربر")
        seen = (u["last_seen"] or "")[:16].replace("T", " ")
        dls = await db.one("SELECT COUNT(*) c FROM downloads WHERE user_id=?", (u["user_id"],))
        rows.append(txt.ADMIN["user_row"].format(
            idx=i, name=name, uid=u["user_id"], msgs=u["msg_count"],
            dls=dls["c"] if dls else 0, seen=seen,
        ))
    text = txt.ADMIN["users_title"].format(
        page=page + 1, pages=pages, total=total,
        rows="\n".join(rows) or txt.ADMIN["users_empty"])
    return text, pages


def user_menu(bot: str, uid: int) -> dict:
    return kb([
        [btn("📜 پیام‌ها", f"{bot}:adm:ulog:{uid}:0"),
         btn("📥 دانلودها", f"{bot}:adm:udl:{uid}")],
        [btn("📦 خروجی JSON", f"{bot}:adm:uexp:{uid}"),
         btn("🚫/✅ بن", f"{bot}:adm:ban:{uid}")],
        [btn("🔙 کاربران", f"{bot}:adm:users:0")],
    ])


async def user_detail(uid: int) -> str:
    u = await db.get_user(uid)
    if not u:
        return "😕 کاربر پیدا نشد."
    dls = await db.one("SELECT COUNT(*) c FROM downloads WHERE user_id=?", (uid,))
    return txt.ADMIN["user_detail"].format(
        name=util.md_escape(u["first_name"] or u["username"] or "کاربر"),
        uid=uid, username=u["username"] or "—",
        first_seen=(u["first_seen"] or "")[:19].replace("T", " "),
        last_seen=(u["last_seen"] or "")[:19].replace("T", " "),
        msgs=u["msg_count"], dls=dls["c"] if dls else 0,
        banned="✅ بله" if u["banned"] else "❌ خیر")


async def user_log_text(uid: int, page: int) -> str:
    limit = 12
    rows = await db.user_messages(uid, None, limit * (page + 1))
    page_rows = rows[page * limit:(page + 1) * limit]
    name = "کاربر"
    u = await db.get_user(uid)
    if u:
        name = util.md_escape(u["first_name"] or u["username"] or "کاربر")
    lines = []
    for m in page_rows:
        t = util.md_escape(util.short(m["text"] or "", 60)) or f"({m['kind']})"
        lines.append(f"• {m['ts'][11:16]} {m['ts'][:10]} [{m['bot']}] {t}")
    return f"📜 پیام‌های *{name}* (صفحه {page + 1}):\n\n" + ("\n".join(lines) or "—")


async def export_user(uid: int) -> io.BytesIO | None:
    u = await db.get_user(uid)
    if not u:
        return None
    msgs = await db.user_messages(uid, None, 1000)
    dls = await db.user_downloads(uid, None, 200)
    payload = {
        "user": dict(u),
        "messages": [dict(m) for m in msgs],
        "downloads": [dict(d) for d in dls],
    }
    return io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))


def features_menu(bot: str) -> dict:
    rows = []
    for b in config.BOTS:
        for f, label in config.FEATURES.items():
            rows.append([btn(f"{'🟢' if False else '❓'} {config.BOT_NAMES[b]} — {label}",
                             f"{bot}:adm:ft:{b}:{f}")])
    # مقدار واقعی در show_features پر می‌شود
    return kb(rows)


async def show_features(bot: Bot, chat_id: int, mid: int, admin_bot_key: str):
    rows = []
    for b in config.BOTS:
        for f, label in config.FEATURES.items():
            en = await db.feature_enabled(b, f)
            icon = "🟢" if en else "🔴"
            rows.append([btn(f"{icon} {config.BOT_NAMES[b]} | {label}",
                             f"{admin_bot_key}:adm:ft:{b}:{f}")])
    rows.append([btn("🔙 منو", f"{admin_bot_key}:adm:root")])
    await bot.edit_message(chat_id, mid, txt.ADMIN["feat_title"], reply_markup=kb(rows))


async def export_all_users_csv() -> io.BytesIO:
    rows = await db.list_users(limit=100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id", "first_name", "username", "first_seen", "last_seen", "msg_count", "banned"])
    for u in rows:
        w.writerow([u["user_id"], u["first_name"], u["username"],
                    u["first_seen"], u["last_seen"], u["msg_count"], u["banned"]])
    out = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return out


# ---------------- ارسال همگانی ----------------
async def broadcast(bot: Bot, source_chat: int, message: dict) -> tuple[int, int]:
    """پیام را به همه کاربران هر سه بات می‌فرستد (کپی)."""
    ok = fail = 0
    seen = set()
    for bk in ("main", "backup", "admin"):
        target_bot = bot if bk == bot.name else getattr(bot, "_sibling_" + bk, None)
        if target_bot is None:
            continue
        users = await db.query("SELECT user_id FROM users WHERE bot=?", (bk,))
        for row in users:
            uid = row["user_id"]
            if uid in seen:
                continue
            seen.add(uid)
            if await db.is_banned(uid, bk):
                continue
            try:
                await target_bot.copy_message(uid, source_chat, message["message_id"])
                ok += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.15)  # محدودیت نرخ
    return ok, fail


# ---------------- مینی‌اپ ----------------
def miniapp_url() -> str | None:
    import base64
    if not (config.ADMIN_WEB_URL and config.ADMIN_WEB_KEY):
        return None
    sep = "&" if "?" in config.ADMIN_WEB_URL else "#"
    return f"{config.ADMIN_WEB_URL}{sep}k={config.ADMIN_WEB_KEY}"
