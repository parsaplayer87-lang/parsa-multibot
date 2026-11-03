"""وضعیت مکالمه هر کاربر + سشن‌های جستجو/پیش‌نمایش + ضد اسپم."""
import asyncio
import time
from typing import Any

import config

# state[(bot,user)] = {"mode": str, "data": {...}, "ts": float}
_states: dict[tuple[str, int], dict] = {}
_sessions: dict[str, dict] = {}  # sid -> {...}
_sid_counter = 0
_janitor_started = False


def new_sid() -> str:
    global _sid_counter
    _sid_counter += 1
    return f"{int(time.time())%1000000}{_sid_counter:x}"


def set_state(bot: str, user_id: int, mode: str, data: dict | None = None):
    _states[(bot, user_id)] = {"mode": mode, "data": data or {}, "ts": time.time()}


def get_state(bot: str, user_id: int) -> dict | None:
    s = _states.get((bot, user_id))
    if s and time.time() - s["ts"] > 3600:  # 1 ساعت انقضا
        _states.pop((bot, user_id), None)
        return None
    return s


def clear_state(bot: str, user_id: int):
    _states.pop((bot, user_id), None)


def put_session(data: dict, ttl: int = 7200) -> str:
    sid = new_sid()
    data["_ts"] = time.time()
    _sessions[sid] = data
    return sid


def get_session(sid: str) -> dict | None:
    s = _sessions.get(sid)
    if not s or time.time() - s.get("_ts", 0) > 7200:
        _sessions.pop(sid, None)
        return None
    return s


def update_session(sid: str, **kw):
    s = _sessions.get(sid)
    if s:
        s.update(kw)
        s["_ts"] = time.time()


# ---------- ضد اسپم ----------
_hits: dict[tuple[str, int], list[float]] = {}

def check_spam(bot: str, user_id: int) -> bool:
    """True = مجاز، False = اسپم"""
    key = (bot, user_id)
    t = time.time()
    lst = [x for x in _hits.get(key, []) if t - x < 20]
    lst.append(t)
    _hits[key] = lst
    return len(lst) <= 20


async def _janitor():
    while True:
        try:
            t = time.time()
            for k, s in list(_states.items()):
                if t - s["ts"] > 3600:
                    _states.pop(k, None)
            for sid, s in list(_sessions.items()):
                if t - s.get("_ts", 0) > 7200:
                    _sessions.pop(sid, None)
            for k, lst in list(_hits.items()):
                if not [x for x in lst if t - x < 60]:
                    _hits.pop(k, None)
        except Exception:
            pass
        await asyncio.sleep(300)


def ensure_janitor():
    global _janitor_started
    if not _janitor_started:
        _janitor_started = True
        asyncio.create_task(_janitor())
