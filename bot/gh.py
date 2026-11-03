"""گیت‌هاب: موتور جستجوی ریپو + دانلودر ریپو (تحویل ZIP به کاربر)."""
import logging

import db
import state
import txt
import util
from baleapi import Bot, btn, kb

log = logging.getLogger("gh")

API = "https://api.github.com"
PAGE_N = 5


async def search_and_show(bot: Bot, chat_id: int, user_id: int, query: str):
    import httpx
    await bot.send_message(chat_id, txt.SEARCHING)
    await db.log_search(user_id, bot.name, "gh", query)
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                     headers=util.gh_api_headers()) as c:
            r = await c.get(f"{API}/search/repositories",
                            params={"q": query, "per_page": PAGE_N, "sort": "best-match"})
            data = r.json() if r.status_code == 200 else {}
    except httpx.HTTPError:
        data = {}
    items = (data.get("items") or [])[:PAGE_N]
    if not items:
        await bot.send_message(chat_id, "😕 ریپویی پیدا نشد.")
        return
    lines = []
    rows = []
    for i, it in enumerate(items):
        desc = util.md_escape(util.short(it.get("description") or "—", 90))
        lines.append(
            f"{i + 1}. *{util.md_escape(it['full_name'])}* ⭐️{it.get('stargazers_count', 0)}\n"
            f"   💻 {it.get('language') or '؟'} | 📦 {it.get('size', 0)}KB\n"
            f"   📝 _{desc}_")
        rows.append([
            btn(f"📥 ZIP {i + 1}", f"{bot}:ghd:{it['full_name']}"),
            btn("🔗", url=it["html_url"]),
        ])
    text = "🐙 *نتایج جستجوی گیت‌هاب*\n\n" + "\n\n".join(lines) + \
           "\n\n👇 برای دانلود ZIP روی دکمه بزن:"
    await bot.send_message(chat_id, text, reply_markup=kb(rows), disable_preview=True)


async def repo_preview(bot: Bot, chat_id: int, user_id: int, owner: str, repo: str):
    """وقتی کاربر لینک گیت‌هاب می‌فرستد: نمایش مشخصات + دکمه دانلود ZIP."""
    import httpx
    await bot.send_message(chat_id, txt.PROBING)
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                     headers=util.gh_api_headers()) as c:
            r = await c.get(f"{API}/repos/{owner}/{repo}")
            if r.status_code != 200:
                await bot.send_message(chat_id, "😕 ریپو پیدا نشد (خصوصی است یا وجود ندارد).")
                return
            data = r.json()
    except httpx.HTTPError:
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
        return
    text = txt.GH_PREVIEW.format(
        full_name=util.md_escape(data["full_name"]),
        description=util.md_escape(util.short(data.get("description") or "—", 120)),
        stars=data.get("stargazers_count", 0), forks=data.get("forks_count", 0),
        watchers=data.get("subscribers_count") or data.get("watchers_count", 0),
        lang=data.get("language") or "؟",
        size=data.get("size", 0), branch=data.get("default_branch", "main"))
    await bot.send_message(chat_id, text, reply_markup=kb([
        [btn("📥 بله، ZIP رو بفرست", f"{bot}:ghd:{owner}/{repo}")],
        [btn("🔗 باز کردن در گیت‌هاب", url=data["html_url"])],
    ]), disable_preview=True)


async def send_zip(bot: Bot, chat_id: int, user_id: int, full_name: str):
    """دانلود zipball ریپو و ارسال به کاربر (سقف ۴۸MB)."""
    await bot.send_message(chat_id, f"⏳ در حال دریافت ZIP از *{util.md_escape(full_name)}*...")
    await bot.send_chat_action(chat_id, "upload_document")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=120, follow_redirects=True,
                                     headers=util.gh_api_headers()) as c:
            r = await c.get(f"{API}/repos/{full_name}/zipball")
            if r.status_code != 200:
                await bot.send_message(chat_id, "😕 دریافت ZIP ناموفق بود.")
                return
            data = r.content
    except httpx.HTTPError:
        await bot.send_message(chat_id, "😕 خطا در ارتباط با گیت‌هاب.")
        return
    if len(data) > config_max():
        await bot.send_message(
            chat_id,
            f"📦 ZIP ریپو ({util.human_size(len(data))}) بزرگ‌تر از سقف ۵۰ مگابایت بله است.\n"
            f"🔗 دانلود مستقیم: {util.md_link(f'https://github.com/{full_name}/archive/refs/heads/main.zip', 'لینک ZIP')}")
        return
    fname = full_name.replace("/", "-") + ".zip"
    await bot.send_document(chat_id, data, filename=fname,
                            caption=f"🐙 *{util.md_escape(full_name)}*\n📦 {util.human_size(len(data))}")
    await db.log_search(user_id, bot.name, "gh_dl", full_name)


def config_max() -> int:
    import config
    return config.TELEGRAM_LIKE_MAX
