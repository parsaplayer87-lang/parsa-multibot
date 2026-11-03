"""کلاینت سبک و غیرهمزمان Bale Bot API (سازگار با Bot API تلگرام) + دیسپچر long-polling.

نوشته‌شده دستی با httpx تا دقیقاً با سرویس بازوی بله کنترل‌شده کار کند.
متن‌ها Markdown بله: *bold*، _italic_، [link](url)
"""
import asyncio
import contextlib
import io
import json
import logging
import random
from typing import Any, Callable, Optional

import httpx

import config

log = logging.getLogger("baleapi")

class BaleError(Exception):
    def __init__(self, code: int, desc: str, retry_after: int | None = None):
        super().__init__(f"[{code}] {desc}")
        self.code = code
        self.desc = desc
        self.retry_after = retry_after

class InputFile:
    """فایل باینری برای آپلود multipart."""
    def __init__(self, data: bytes, filename: str):
        self.data = data
        self.filename = filename

def kb(rows: list[list[dict]]) -> dict:
    """inline keyboard ساده: [[{text, callback_data|url}, ...], ...]"""
    return {"inline_keyboard": rows}

def btn(text: str, cb: str = "", url: str = "", web_app: str = "") -> dict:
    b: dict[str, Any] = {"text": text}
    if cb:
        b["callback_data"] = cb
    if url:
        b["url"] = url
    if web_app:
        b["web_app"] = {"url": web_app}
    return b

def btn_copy(text: str, copy_text: str) -> dict:
    return {"text": text, "copy_text": {"text": copy_text}}

class Bot:
    def __init__(self, token: str, name: str):
        self.token = token
        self.name = name  # main | backup | admin
        self.base = f"{config.API_BASE}{token}"
        self.client: Optional[httpx.AsyncClient] = None
        self.username = ""
        self.bot_id = 0
        self.offset = 0
        self.running = False
        # هندلرها (در main.py ست می‌شوند)
        self.on_message: Optional[Callable] = None      # async (bot, msg)
        self.on_callback: Optional[Callable] = None     # async (bot, cq)
        self.on_webapp: Optional[Callable] = None       # async (bot, msg) با web_app_data
        self.on_error: Optional[Callable] = None

    # ---------- HTTP ----------
    async def _client_(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=20.0))
        return self.client

    async def call(self, method: str, params: dict | None = None, files: dict | None = None,
                   _retry: int = 3) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        c = await self._client_()
        data: dict[str, Any] = {}
        mfiles = None
        if files:
            mfiles = {}
            for k, v in files.items():
                if isinstance(v, InputFile):
                    mfiles[k] = (v.filename, io.BytesIO(v.data))
                else:
                    data[k] = str(v)
            for k, v in params.items():
                data[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        try:
            if mfiles:
                r = await c.post(f"{self.base}/{method}", data=data, files=mfiles)
            else:
                r = await c.post(f"{self.base}/{method}", json=params)
            if r.status_code == 429:
                ra = 3
                try:
                    ra = int(r.json().get("parameters", {}).get("retry_after", 3))
                except Exception:
                    pass
                if _retry > 0:
                    await asyncio.sleep(ra + 1)
                    return await self.call(method, params, files, _retry - 1)
                raise BaleError(429, "rate limit", ra)
            if r.status_code >= 500 and _retry > 0:
                await asyncio.sleep(2 + random.random() * 3)
                return await self.call(method, params, files, _retry - 1)
            j = r.json()
            if not j.get("ok"):
                raise BaleError(j.get("error_code", r.status_code), j.get("description", "unknown"))
            return j.get("result")
        except httpx.HTTPError as e:
            if _retry > 0:
                await asyncio.sleep(2 + random.random() * 3)
                return await self.call(method, params, files, _retry - 1)
            raise BaleError(-1, f"network: {e}")

    # ---------- متدهای پرکاربرد ----------
    async def get_me(self) -> dict:
        r = await self.call("getMe")
        self.username = r.get("username", "")
        self.bot_id = r.get("id", 0)
        return r

    async def send_message(self, chat_id: int | str, text: str, reply_markup: dict | None = None,
                           reply_to: int | None = None, disable_preview: bool = False) -> dict:
        return await self.call("sendMessage", {
            "chat_id": chat_id, "text": text[:4096],
            "reply_markup": reply_markup, "reply_to_message_id": reply_to,
            "disable_web_page_preview": disable_preview,
        })

    async def edit_message(self, chat_id: int | str, message_id: int, text: str,
                           reply_markup: dict | None = None) -> dict:
        try:
            return await self.call("editMessageText", {
                "chat_id": chat_id, "message_id": message_id, "text": text[:4096],
                "reply_markup": reply_markup,
            })
        except BaleError as e:
            if "message is not modified" in e.desc.lower():
                return {}
            raise

    async def edit_markup(self, chat_id: int | str, message_id: int, reply_markup: dict | None = None):
        return await self.call("editMessageReplyMarkup", {
            "chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup})

    async def answer_callback(self, callback_query_id: str, text: str = "",
                              show_alert: bool = False):
        # کلاینت‌های خیلی قدیمی بله از popup پشتیبانی نمی‌کنند؛ خطا را نادیده می‌گیریم
        try:
            await self.call("answerCallbackQuery", {
                "callback_query_id": callback_query_id,
                "text": text[:200] or None, "show_alert": show_alert,
            })
        except BaleError:
            pass

    async def send_photo(self, chat_id: int | str, photo: bytes | str, caption: str = "",
                         reply_markup: dict | None = None) -> dict:
        if isinstance(photo, bytes):
            return await self.call("sendPhoto", {
                "chat_id": chat_id, "caption": caption or None, "reply_markup": reply_markup},
                files={"photo": InputFile(photo, "photo.jpg")})
        return await self.call("sendPhoto", {
            "chat_id": chat_id, "photo": photo, "caption": caption or None,
            "reply_markup": reply_markup})

    async def send_video(self, chat_id: int | str, video: bytes | str, caption: str = "",
                         reply_markup: dict | None = None, width=0, height=0, duration=0,
                         thumb: bytes | None = None) -> dict:
        params = {"chat_id": chat_id, "caption": caption or None, "reply_markup": reply_markup,
                  "supports_streaming": True}
        if width: params["width"] = width
        if height: params["height"] = height
        if duration: params["duration"] = duration
        if isinstance(video, bytes):
            files = {"video": InputFile(video, "video.mp4")}
            if thumb:
                files["thumbnail"] = InputFile(thumb, "thumb.jpg")
            return await self.call("sendVideo", params, files=files)
        params["video"] = video
        return await self.call("sendVideo", params)

    async def send_audio(self, chat_id: int | str, audio: bytes | str, caption: str = "",
                         title: str = "", performer: str = "", duration=0,
                         reply_markup: dict | None = None, thumb: bytes | None = None) -> dict:
        params = {"chat_id": chat_id, "caption": caption or None, "reply_markup": reply_markup,
                  "title": title or None, "performer": performer or None}
        if duration: params["duration"] = duration
        if isinstance(audio, bytes):
            files = {"audio": InputFile(audio, "audio.mp3")}
            if thumb:
                files["thumbnail"] = InputFile(thumb, "thumb.jpg")
            return await self.call("sendAudio", params, files=files)
        params["audio"] = audio
        return await self.call("sendAudio", params)

    async def send_document(self, chat_id: int | str, document: bytes | str, caption: str = "",
                            filename: str = "file.bin", reply_markup: dict | None = None,
                            thumb: bytes | None = None) -> dict:
        params = {"chat_id": chat_id, "caption": caption or None, "reply_markup": reply_markup}
        if isinstance(document, bytes):
            files = {"document": InputFile(document, filename)}
            if thumb:
                files["thumbnail"] = InputFile(thumb, "thumb.jpg")
            return await self.call("sendDocument", params, files=files)
        params["document"] = document
        return await self.call("sendDocument", params)

    async def send_media_group(self, chat_id: int | str, media: list[dict]) -> list:
        return await self.call("sendMediaGroup", {"chat_id": chat_id,
                                                  "media": json.dumps(media, ensure_ascii=False)})

    async def send_chat_action(self, chat_id: int | str, action: str = "typing"):
        with contextlib.suppress(BaleError):
            await self.call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def delete_message(self, chat_id: int | str, message_id: int):
        with contextlib.suppress(BaleError):
            await self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    async def copy_message(self, chat_id: int | str, from_chat_id: int | str, message_id: int,
                           reply_markup: dict | None = None) -> dict:
        return await self.call("copyMessage", {
            "chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id,
            "reply_markup": reply_markup})

    async def forward_message(self, chat_id: int | str, from_chat_id: int | str, message_id: int):
        return await self.call("forwardMessage", {
            "chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id})

    async def get_file(self, file_id: str) -> dict:
        return await self.call("getFile", {"file_id": file_id})

    async def download_file(self, file_id: str) -> bytes:
        f = await self.get_file(file_id)
        path = f.get("file_path", "")
        c = await self._client_()
        r = await c.get(f"{config.FILE_BASE}{self.token}/{path}")
        r.raise_for_status()
        return r.content

    # ---------- long polling ----------
    async def _poll_loop(self):
        while self.running:
            try:
                r = await self.call("getUpdates", {
                    "offset": self.offset, "timeout": 40,
                    "allowed_updates": ["message", "callback_query"],
                }, _retry=0)
                for upd in r or []:
                    self.offset = max(self.offset, upd.get("update_id", 0) + 1)
                    try:
                        await self._dispatch(upd)
                    except Exception:
                        log.exception("[%s] dispatch error", self.name)
            except BaleError as e:
                log.warning("[%s] poll error: %s", self.name, e)
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("[%s] poll crashed, retrying", self.name)
                await asyncio.sleep(5)

    async def _dispatch(self, upd: dict):
        msg = upd.get("message")
        if msg:
            wad = msg.get("web_app_data")
            if wad and self.on_webapp:
                await self.on_webapp(self, msg)
                return
            if self.on_message:
                await self.on_message(self, msg)
            return
        cq = upd.get("callback_query")
        if cq and self.on_callback:
            await self.on_callback(self, cq)

    async def start(self):
        await self.get_me()
        self.running = True
        self._task = asyncio.create_task(self._poll_loop(), name=f"poll-{self.name}")
        log.info("[%s] started as @%s", self.name, self.username)

    async def stop(self):
        self.running = False
        t = getattr(self, "_task", None)
        if t:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if self.client:
            await self.client.aclose()
            self.client = None
