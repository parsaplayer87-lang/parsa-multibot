"""لایه دیتابیس SQLite (aiosqlite) — کاربران، لاگ کامل پیام‌ها، دانلودها، جستجوها، تنظیمات.

دیتا «حیاتی» است: هر متن/کپشن/دکمه‌ای که کاربر می‌فرستد ثبت می‌شود.
فایل زنده: data/live.db  |  فایل پوش‌شونده (اسنپ‌شات سازگار): data/botdata.db
"""
import asyncio
import datetime as dt
import json
import os
import shutil
import sqlite3
from typing import Any, Optional

import config

DB_PATH = os.path.join(config.DATA_DIR, "live.db")
SNAPSHOT_PATH = os.path.join(config.DATA_DIR, "botdata.db")

_lock = asyncio.Lock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER, bot TEXT,
  first_name TEXT DEFAULT '', username TEXT DEFAULT '',
  first_seen TEXT, last_seen TEXT, msg_count INTEGER DEFAULT 0, banned INTEGER DEFAULT 0,
  PRIMARY KEY(user_id, bot)
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, bot TEXT, msg_id INTEGER DEFAULT 0,
  kind TEXT,          -- text | caption | callback | webapp | photo | video | document | audio | ...
  text TEXT DEFAULT '',
  ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id, id DESC);
CREATE TABLE IF NOT EXISTS downloads(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, bot TEXT, url TEXT, platform TEXT DEFAULT '',
  title TEXT DEFAULT '', kind TEXT DEFAULT '', quality TEXT DEFAULT '',
  status TEXT DEFAULT 'queued',  -- queued|running|done|failed|cancelled
  size INTEGER DEFAULT 0, error TEXT DEFAULT '',
  ts_start TEXT, ts_end TEXT
);
CREATE TABLE IF NOT EXISTS searches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, bot TEXT, kind TEXT, query TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def _adapt(conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row

async def init():
    global _conn
    os.makedirs(config.DATA_DIR, exist_ok=True)
    # اگر اسنپ‌شات قبلی هست ولی دیتابیس زنده نیست (شروع تازه)، بازیابی کن
    if not os.path.exists(DB_PATH) and os.path.exists(SNAPSHOT_PATH):
        shutil.copyfile(SNAPSHOT_PATH, DB_PATH)
    _conn = await asyncio.to_thread(
        sqlite3.connect, DB_PATH, 30.0, 0, None, False  # check_same_thread=False
    )
    _adapt(_conn)
    await _exec_script(SCHEMA)
    await _exec("PRAGMA journal_mode=WAL")
    await _exec("PRAGMA synchronous=NORMAL")

async def _exec_script(script: str):
    async with _lock:
        await asyncio.to_thread(_conn.executescript, script)
        await asyncio.to_thread(_conn.commit)

async def _exec(sql: str, params: tuple = ()):
    async with _lock:
        cur = await asyncio.to_thread(_conn.execute, sql, params)
        await asyncio.to_thread(_conn.commit)
        return cur

async def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    async with _lock:
        cur = await asyncio.to_thread(_conn.execute, sql, params)
        rows = await asyncio.to_thread(cur.fetchall)
        return rows

async def one(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    rows = await query(sql, params)
    return rows[0] if rows else None

# ---------- کاربران ----------
async def upsert_user(user_id: int, bot: str, first_name: str = "", username: str = ""):
    if not user_id:
        return
    t = now()
    row = await one("SELECT user_id FROM users WHERE user_id=? AND bot=?", (user_id, bot))
    if row is None:
        await _exec(
            """INSERT INTO users(user_id,bot,first_name,username,first_seen,last_seen,msg_count,banned)
               VALUES(?,?,?,?,?,?,1,0)""",
            (user_id, bot, first_name or "", username or "", t, t),
        )
    else:
        await _exec(
            """UPDATE users SET
                 first_name=CASE WHEN ?<>'' THEN ? ELSE first_name END,
                 username=CASE WHEN ?<>'' THEN ? ELSE username END,
                 last_seen=?, msg_count=msg_count+1
               WHERE user_id=? AND bot=?""",
            (first_name or "", first_name or "", username or "", username or "", t, user_id, bot),
        )


async def is_banned(user_id: int, bot: str) -> bool:
    r = await one("SELECT banned FROM users WHERE user_id=? AND bot=?", (user_id, bot))
    return bool(r and r["banned"])

async def set_ban(user_id: int, bot: str, banned: bool):
    await _exec("UPDATE users SET banned=? WHERE user_id=? AND bot=?", (1 if banned else 0, user_id, bot))

async def list_users(bot: str | None = None, offset: int = 0, limit: int = 10):
    if bot:
        return await query(
            "SELECT * FROM users WHERE bot=? ORDER BY last_seen DESC LIMIT ? OFFSET ?",
            (bot, limit, offset))
    return await query(
        "SELECT user_id, MAX(first_seen) first_seen, MAX(last_seen) last_seen, SUM(msg_count) msg_count, "
        "MAX(first_name) first_name, MAX(username) username, MAX(banned) banned "
        "FROM users GROUP BY user_id ORDER BY last_seen DESC LIMIT ? OFFSET ?", (limit, offset))

async def count_users(bot: str | None = None) -> int:
    if bot:
        r = await one("SELECT COUNT(*) c FROM users WHERE bot=?", (bot,))
    else:
        r = await one("SELECT COUNT(DISTINCT user_id) c FROM users")
    return r["c"] if r else 0

async def get_user(user_id: int, bot: str | None = None):
    if bot:
        return await one("SELECT * FROM users WHERE user_id=? AND bot=?", (user_id, bot))
    return await one(
        "SELECT user_id, MAX(first_seen) first_seen, MAX(last_seen) last_seen, SUM(msg_count) msg_count, "
        "MAX(first_name) first_name, MAX(username) username, MAX(banned) banned "
        "FROM users WHERE user_id=? GROUP BY user_id", (user_id,))

# ---------- لاگ پیام‌ها ----------
async def log_message(user_id: int, bot: str, msg_id: int, kind: str, text: str):
    await _exec(
        "INSERT INTO messages(user_id,bot,msg_id,kind,text,ts) VALUES(?,?,?,?,?,?)",
        (user_id, bot, msg_id or 0, kind, (text or "")[:4000], now()))

async def user_messages(user_id: int, bot: str | None, limit: int = 20):
    if bot:
        return await query(
            "SELECT * FROM messages WHERE user_id=? AND bot=? ORDER BY id DESC LIMIT ?",
            (user_id, bot, limit))
    return await query("SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))

async def recent_messages(limit: int = 50, offset: int = 0):
    return await query("SELECT * FROM messages ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))

async def recent_messages_all(limit: int = 200):
    return await query("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))

# ---------- دانلودها ----------
async def add_download(user_id: int, bot: str, url: str, platform="", title="", kind="", quality="") -> int:
    cur = await _exec(
        "INSERT INTO downloads(user_id,bot,url,platform,title,kind,quality,status,ts_start) "
        "VALUES(?,?,?,?,?,?,?,'queued',?)",
        (user_id, bot, url, platform, title, kind, quality, now()))
    return cur.lastrowid

async def update_download(dl_id: int, **kw):
    if not kw:
        return
    cols = ",".join(f"{k}=?" for k in kw)
    await _exec(f"UPDATE downloads SET {cols} WHERE id=?", (*kw.values(), dl_id))

async def user_downloads(user_id: int, bot: str | None, limit: int = 15):
    if bot:
        return await query(
            "SELECT * FROM downloads WHERE user_id=? AND bot=? ORDER BY id DESC LIMIT ?",
            (user_id, bot, limit))
    return await query("SELECT * FROM downloads WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))

async def count_downloads(bot: str | None = None) -> int:
    if bot:
        r = await one("SELECT COUNT(*) c FROM downloads WHERE bot=?", (bot,))
    else:
        r = await one("SELECT COUNT(*) c FROM downloads")
    return r["c"] if r else 0

# ---------- جستجوها ----------
async def log_search(user_id: int, bot: str, kind: str, query_text: str):
    await _exec("INSERT INTO searches(user_id,bot,kind,query,ts) VALUES(?,?,?,?,?)",
                (user_id, bot, kind, query_text[:500], now()))

async def count_searches() -> int:
    r = await one("SELECT COUNT(*) c FROM searches")
    return r["c"] if r else 0

# ---------- تنظیمات ----------
async def get_setting(key: str, default: str = "") -> str:
    r = await one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default

async def set_setting(key: str, value: str):
    await _exec(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

async def feature_enabled(bot: str, feat: str) -> bool:
    v = await get_setting(f"feat:{bot}:{feat}", "1")
    return v == "1"

async def set_feature(bot: str, feat: str, enabled: bool):
    await set_setting(f"feat:{bot}:{feat}", "1" if enabled else "0")

async def admin_id() -> int:
    if config.ADMIN_ID:
        return config.ADMIN_ID
    try:
        return int(await get_setting("admin_id", "0") or 0)
    except ValueError:
        return 0

async def claim_admin(user_id: int) -> bool:
    """ادمین فعلی را ثبت می‌کند؛ اگر قبلاً ثبت شده False برمی‌گرداند."""
    if config.ADMIN_ID and user_id != config.ADMIN_ID:
        return False
    cur_admin = int(await get_setting("admin_id", "0") or 0)
    if cur_admin and cur_admin != user_id:
        return False
    await set_setting("admin_id", str(user_id))
    return True

# ---------- آمار ----------
async def stats() -> dict:
    today = now()[:10]
    r = await one("SELECT COUNT(DISTINCT user_id) c FROM users")
    r2 = await one("SELECT COUNT(DISTINCT user_id) c FROM users WHERE last_seen LIKE ?", (today + "%",))
    r3 = await one("SELECT COUNT(*) c FROM messages WHERE ts LIKE ?", (today + "%",))
    r4 = await one("SELECT COUNT(*) c FROM messages")
    return {
        "users": r["c"], "active_today": r2["c"],
        "msgs_today": r3["c"], "msgs_total": r4["c"],
        "downloads": await count_downloads(), "searches": await count_searches(),
    }

# ---------- اسنپ‌شات سازگار برای پوش ----------
async def backup_snapshot() -> str:
    """با VACUUM INTO یک کپی سازگار و فشرده از دیتابیس می‌سازد."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = SNAPSHOT_PATH + ".tmp"
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    async with _lock:
        await asyncio.to_thread(_conn.execute, "VACUUM INTO ?", (tmp,))
        await asyncio.to_thread(_conn.commit)
    os.replace(tmp, SNAPSHOT_PATH)
    return SNAPSHOT_PATH

# ---------- مینی‌اپ: snapshot رمزنگاری‌شده ----------
async def snapshot_payload() -> bytes:
    users = await query(
        "SELECT user_id, MAX(first_name) first_name, MAX(username) username, "
        "MAX(first_seen) first_seen, MAX(last_seen) last_seen, SUM(msg_count) msg_count, MAX(banned) banned "
        "FROM users GROUP BY user_id ORDER BY last_seen DESC LIMIT 2000")
    msgs = await recent_messages_all(300)
    dls = await query("SELECT * FROM downloads ORDER BY id DESC LIMIT 100")
    data = {
        "generated_at": now(),
        "stats": await stats(),
        "features": {},
        "users": [dict(u) for u in users],
        "messages": [dict(m) for m in msgs],
        "downloads": [dict(d) for d in dls],
    }
    for b in config.BOTS:
        for f in config.FEATURES:
            data["features"][f"{b}:{f}"] = await feature_enabled(b, f)
    return json.dumps(data, ensure_ascii=False).encode("utf-8")
