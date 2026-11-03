"""صف دانلود مرکزی: جلوگیری از هنگ کردن بات، اجرای همزمان محدود، پیشرفت زنده."""
import asyncio
import hashlib
import logging
import os
import time

import config
import db
import ytdlp

log = logging.getLogger("queue")


class CacheKey:
    """کلید یکتا برای dedup فایل‌های دانلودی بین بازوها (فایل واحد، سه ارسال)."""

    __slots__ = ("key",)

    def __init__(self, bot, chat_id: int, user_id: int, url: str, is_video: bool):
        self.key = f"{bot.name}|{chat_id}|{url}|{'v' if is_video else 'a'}"


def cache_key_of(bot, chat_id: int, user_id: int, url: str, is_video: bool = True) -> str:
    return CacheKey(bot, chat_id, user_id, url, is_video).key




class Job:
    def __init__(self, job_id: int, bot, chat_id: int, user_id: int, url: str,
                 fmt: str, kind: str, quality: str, title: str, platform: str,
                 status_msg_id: int | None = None):
        self.job_id = job_id
        self.bot = bot           # baleapi.Bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.url = url
        self.fmt = fmt
        self.kind = kind         # video | audio
        self.quality = quality   # "720" | "audio"
        self.title = title
        self.platform = platform
        self.status_msg_id = status_msg_id
        self.cancel = asyncio.Event()
        self.started = False
        self.error = ""

    @property
    def bot_key(self) -> str:
        return self.bot.name


class DownloadQueue:
    def __init__(self):
        self.q: asyncio.Queue[Job] = asyncio.Queue()
        self.workers: list[asyncio.Task] = []
        self.active: dict[int, Job] = {}       # job_id -> Job (در حال اجرا یا منتظر)
        self.user_active: dict[tuple[str, int], int] = {}
        # key = bot|chat|url|kind → (video_file_id, caption_ref, ts)؛ ارسال مجدد با file_id
        self._file_id_cache: dict[str, tuple[str, float]] = {}

    async def start(self):
        for i in range(config.MAX_CONCURRENT_DOWNLOADS):
            t = asyncio.create_task(self._worker(i), name=f"dlw-{i}")
            self.workers.append(t)

    async def stop(self):
        for t in self.workers:
            t.cancel()

    def _user_key(self, job: Job):
        return (job.bot_key, job.user_id)

    async def enqueue(self, job: Job) -> int:
        """نوبت را برمی‌گرداند (۱ = بلافاصله)."""
        uk = self._user_key(job)
        if self.user_active.get(uk, 0) >= config.MAX_ACTIVE_JOBS_PER_USER:
            raise RuntimeError("limit")
        self.user_active[uk] = self.user_active.get(uk, 0) + 1
        self.active[job.job_id] = job
        waiting = self.q.qsize() + sum(1 for j in self.active.values() if j.started)
        await self.q.put(job)
        return max(1, waiting)

    def cancel_job(self, job_id: int) -> bool:
        job = self.active.get(job_id)
        if job and not job.cancel.is_set():
            job.cancel.set()
            return True
        return False

    def cache_get(self, key: str) -> str | None:
        """file_id معتبر (کمتر از ۱۲ ساعت) برای این کلید یا None."""
        hit = self._file_id_cache.get(key)
        if not hit:
            return None
        fid, ts = hit
        if time.time() - ts > 12 * 3600:
            self._file_id_cache.pop(key, None)
            return None
        return fid

    def cache_put(self, key: str, file_id: str):
        self._file_id_cache[key] = (file_id, time.time())
        if len(self._file_id_cache) > 200:
            old = sorted(self._file_id_cache.items(), key=lambda kv: kv[1][1])[:50]
            for k, _ in old:
                self._file_id_cache.pop(k, None)


    def position_of(self, job_id: int) -> int:
        job = self.active.get(job_id)
        if not job:
            return 0
        if job.started:
            return 1
        pos = 1
        for j in self.active.values():
            if j.started:
                pos += 1
        return pos

    async def _worker(self, idx: int):
        while True:
            job = await self.q.get()
            job.started = True
            try:
                await self._run_job(job)
            except Exception:
                log.exception("job %s crashed", job.job_id)
                try:
                    await job.bot.send_message(job.chat_id, "⚠️ خطای غیرمنتظره در پردازش دانلود.")
                except Exception:
                    pass
            finally:
                self.active.pop(job.job_id, None)
                uk = self._user_key(job)
                self.user_active[uk] = max(0, self.user_active.get(uk, 1) - 1)
                if self.user_active[uk] == 0:
                    self.user_active.pop(uk, None)
                self.q.task_done()
                await _cleanup_tmp()

    async def _run_job(self, job: Job):
        bot, chat = job.bot, job.chat_id
        await db.update_download(job.job_id, status="running")
        try:
            await bot.edit_message(chat, job.status_msg_id,
                                   f"⬇️ *در حال دانلود...*\n📄 {job.title}\n🔁 نوبت: در حال اجرا")
        except Exception:
            pass

        async def prog(percent, done, total, speed, eta):
            if job.cancel.is_set():
                raise asyncio.CancelledError()
            now = time.time()
            if now - getattr(prog, "_last", 0) < 4:
                return
            prog._last = now
            try:
                await bot.edit_message(
                    chat, job.status_msg_id,
                    f"⬇️ *در حال دانلود...*\n📄 {job.title}\n"
                    f"📊 {percent}٪ | {ytdlp.human_size(done)} / {ytdlp.human_size(total)}\n"
                    f"🚀 سرعت: {speed} | ⏱ باقی: {eta}")
            except Exception:
                pass

        try:
            path = await ytdlp.download(job.url, job.fmt, config.DOWNLOAD_DIR, prog, job.cancel)
        except asyncio.CancelledError:
            await db.update_download(job.job_id, status="cancelled")
            try:
                await bot.edit_message(chat, job.status_msg_id, "🚫 *دانلود لغو شد.*")
            except Exception:
                pass
            return
        if job.cancel.is_set():
            await db.update_download(job.job_id, status="cancelled")
            try:
                await bot.edit_message(chat, job.status_msg_id, "🚫 *دانلود لغو شد.*")
            except Exception:
                pass
            return
        if not path or not os.path.exists(path):
            err = "فایل ساخته نشد (ممکن است سرویس به لاگین/کوکی نیاز داشته باشد یا فرمت در دسترس نباشد)."
            await db.update_download(job.job_id, status="failed", error=err)
            try:
                await bot.edit_message(chat, job.status_msg_id,
                                       f"❌ *دانلود ناموفق*\n_{err}_")
            except Exception:
                pass
            return

        size = os.path.getsize(path)
        await db.update_download(job.job_id, status="done", size=size)
        await _deliver(bot, chat, job, path, size)


def dedup_key(job: Job) -> str:
    """کلید dedup: هر سه بازو ممکن است از یک لینک بخواهند؛ فایل واحد، سه ارسال."""
    kind = "v" if job.kind == "video" else "a"
    return f"{job.url}|{kind}"


queue = DownloadQueue()


async def _deliver(bot, chat_id: int, job: Job, path: str, size: int):
    """ارسال فایل دانلودشده به کاربر — با کش file_id بین بازوها (dedup)."""
    caption = f"✅ *{job.title}*\n📦 {ytdlp.human_size(size)}"
    cap_url = f"\n🔗 {job.url}"
    caption = caption + cap_url
    await bot.send_chat_action(chat_id, "upload_document")
    ext = os.path.splitext(path)[1].lower()
    key = dedup_key(job)
    sent = False

    # ۱) کش file_id این بازو؟ (file_id بین بازوها یکتاست؛ هر بازو کش خودش را دارد با پیشوند name)
    bkey = f"{bot.name}|{key}"
    fid = queue.cache_get(bkey)
    if fid:
        try:
            if job.kind == "audio" or ext in (".m4a", ".mp3"):
                await bot.send_audio(chat_id, fid, caption=caption, title=job.title[:60])
            elif job.kind == "video" and ext == ".mp4":
                await bot.send_video(chat_id, fid, caption=caption)
            else:
                await bot.send_document(chat_id, fid, caption=caption,
                                        filename=os.path.basename(path))
            sent = True
        except Exception:
            sent = False

    # ۲) آپلود کامل + ثبت file_id در کش
    if not sent:
        data = None
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            data = None
        try:
            if data is None:
                await bot.send_message(chat_id, "❌ فایل دانلودی خوانده نشد.")
                return
            if size > config.TELEGRAM_LIKE_MAX:
                await bot.send_message(chat_id, "📦 فایل بزرگ‌تر از سقف آپلود ۵۰ مگابایتی بله است.")
                return
            resp = None
            if job.kind == "audio" or ext in (".m4a", ".mp3"):
                resp = await bot.send_audio(chat_id, data, caption=caption, title=job.title[:60])
            elif job.kind == "video" and ext in (".mp4", ".mkv", ".webm"):
                if ext == ".mp4":
                    resp = await bot.send_video(chat_id, data, caption=caption)
                else:
                    resp = await bot.send_document(chat_id, data, caption=caption,
                                                   filename=os.path.basename(path))
            else:
                resp = await bot.send_document(chat_id, data, caption=caption,
                                               filename=os.path.basename(path))
            # ثبت file_id
            try:
                new_fid = None
                if isinstance(resp, dict):
                    for k in ("video", "audio", "document"):
                        if resp.get(k) and resp[k].get("file_id"):
                            new_fid = resp[k]["file_id"]
                            break
                if new_fid:
                    queue.cache_put(bkey, new_fid)
            except Exception:
                pass
        finally:
            del data

    try:
        await bot.edit_message(chat_id, job.status_msg_id, "✅ *دانلود انجام و ارسال شد.*")
    except Exception:
        pass


async def _cleanup_tmp():
    """پاکسازی فایل‌های نیمه‌کاره قدیمی (۲ ساعت)."""
    d = config.DOWNLOAD_DIR
    if not os.path.isdir(d):
        return
    now = time.time()
    for f in os.listdir(d):
        p = os.path.join(d, f)
        try:
            if now - os.path.getmtime(p) > 7200:
                os.remove(p)
        except OSError:
            pass
