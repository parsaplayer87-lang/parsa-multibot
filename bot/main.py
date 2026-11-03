"""نقطه ورود: اجرای همزمان ۳ بازو (اصلی، پشتیبان، ادمین) + صف دانلود + سینک دوره‌ای داده.

چرخه ۲۴/۷ روی GitHub Actions:
- SHUTDOWN_MINUTES دقیقه بعد از شروع، خروج نرم (پوش دیتا) — ورک‌فلو خودش run بعدی را تریگر می‌کند.
- با SHUTDOWN_MINUTES=0 (لوکال) تا ابد می‌ماند.
"""
import asyncio
import logging
import os
import signal
import sys
import time

import baleapi
import config

log = logging.getLogger("main")
START_TS = time.time()
_stop = asyncio.Event()


async def periodic_sync(stop_evt: asyncio.Event):
    """هر SYNC_INTERVAL ثانیه، اسنپ‌شات دیتابیس و مینی‌اپ را پوش می‌کند."""
    import datastore
    while not stop_evt.is_set():
        try:
            await asyncio.wait_for(stop_evt.wait(), timeout=config.SYNC_INTERVAL)
            break  # stop signal
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.to_thread(datastore.push_data)
            await datastore.push_admin_snapshot_async()
        except Exception:
            log.exception("periodic sync failed")


async def shutdown_timer():
    if config.SHUTDOWN_MINUTES <= 0:
        return
    await asyncio.sleep(config.SHUTDOWN_MINUTES * 60)
    log.info("shutdown timer fired — graceful exit")
    _stop.set()


async def amain():
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("starting bots... (pid %s)", os.getpid())

    import db
    import datastore
    import dqueue
    import handlers
    import state as st

    # ۱) دیتای قبلی
    try:
        result = await asyncio.to_thread(datastore.pull_data)
        log.info("data pull: %s", result)
    except Exception:
        log.exception("data pull failed (continuing with local)")

    await db.init()
    st.ensure_janitor()

    # ۲) ساخت سه بازو
    bots: list[baleapi.Bot] = []
    specs = [("main", config.BOT_TOKEN_MAIN), ("backup", config.BOT_TOKEN_BACKUP),
             ("admin", config.BOT_TOKEN_ADMIN)]
    for name, token in specs:
        if not token:
            log.warning("token for %s missing — skipped", name)
            continue
        bots.append(baleapi.Bot(token, name))

    for b in bots:
        b.on_message = handlers.on_message
        b.on_callback = handlers.on_callback
        b.on_webapp = handlers.on_webapp

    # ۳) صف دانلود
    await dqueue.queue.start()

    # ۴) استارت بازوها (با فاصله، برای جلوگیری از thundering poll)
    for i, b in enumerate(bots):
        try:
            await b.start()
        except Exception:
            log.exception("bot %s failed to start", b.name)
        if i < len(bots) - 1:
            await asyncio.sleep(2)

    # ۵) تسک‌های جانبی
    sync_task = asyncio.create_task(periodic_sync(_stop))
    timer_task = asyncio.create_task(shutdown_timer())

    # سیگنال‌ها
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        _add_signal(loop, sig)

    await _stop.wait()

    # ۶) خروج نرم: پوش نهایی داده
    log.info("shutting down gracefully...")
    for b in bots:
        try:
            await b.stop()
        except Exception:
            pass
    await dqueue.queue.stop()
    sync_task.cancel()
    timer_task.cancel()
    try:
        import browser
        await browser.stop_browser()
    except Exception:
        pass
    try:
        await asyncio.to_thread(datastore.push_data)
        await datastore.push_admin_snapshot_async()
        log.info("final data push done")
    except Exception:
        log.exception("final push failed")
    log.info("bye")


def _add_signal(loop, sig):
    try:
        loop.add_signal_handler(sig, _stop.set)
    except NotImplementedError:
        pass  # ویندوز


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
