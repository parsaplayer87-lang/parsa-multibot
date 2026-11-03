"""ابزارهای اضافه: QR code، آب‌وهوا (Open-Meteo رایگان)، ترجمه (سرویس رایگان Google gtx)."""
import io
import logging

import httpx

import db
import txt
import util
from baleapi import Bot

log = logging.getLogger("tools")

WEATHER_CODES = {
    0: "☀️ صاف", 1: "🌤 عمدتاً صاف", 2: "⛅️ نیمه‌ابری", 3: "☁️ ابری",
    45: "🌫 مه", 48: "🌫 مه یخ‌زده", 51: "🌦 نم‌نم باران خفیف", 53: "🌦 نم‌نم باران",
    55: "🌧 نم‌نم باران شدید", 61: "🌧 باران خفیف", 63: "🌧 باران", 65: "🌧 باران شدید",
    71: "🌨 برف خفیف", 73: "🌨 برف", 75: "❄️ برف سنگین", 80: "🌦 رگبار خفیف",
    81: "🌦 رگبار", 82: "⛈ رگبار شدید", 95: "⛈ رعدوبرق", 96: "⛈ رعدوبرق با تگرگ",
}


async def make_qr(text: str) -> bytes | None:
    import qrcode
    def _gen():
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    try:
        return await __import__("asyncio").to_thread(_gen)
    except Exception:
        return None


async def weather(city: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                            params={"name": city, "count": 1, "language": "fa"})
            geo = (r.json().get("results") or [None])[0]
            if not geo:
                return None
            r2 = await c.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": geo["latitude"], "longitude": geo["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "forecast_days": 3, "timezone": "auto"})
            cur = r2.json()["current"]
            daily = r2.json()["daily"]
            daily_lines = []
            for i in range(len(daily["time"])):
                wc = WEATHER_CODES.get(daily["weather_code"][i], "—")
                daily_lines.append(
                    f"• {daily['time'][i]}: {wc} — {daily['temperature_2m_min'][i]}° تا {daily['temperature_2m_max'][i]}°")
            return txt.TOOLS["weather_fmt"].format(
                city=geo.get("name", city), country=geo.get("country", ""),
                temp=cur["temperature_2m"], feels=cur["apparent_temperature"],
                desc=WEATHER_CODES.get(cur["weather_code"], "—"),
                wind=cur["wind_speed_10m"], humidity=cur["relative_humidity_2m"],
                daily="\n".join(daily_lines))
    except (httpx.HTTPError, KeyError, IndexError, TypeError):
        return None


WEEK_FA = {"monday": "دوشنبه", "tuesday": "سه‌شنبه", "wednesday": "چهارشنبه",
           "thursday": "پنجشنبه", "friday": "جمعه", "saturday": "شنبه", "sunday": "یکشنبه"}


async def translate(text: str, target: str = "fa") -> tuple[str, str] | None:
    """(متن ترجمه‌شده, زبان مقصد) — سرویس رایگان gtx."""
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.get("https://translate.googleapis.com/translate_a/single", params={
                "client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text[:4000]})
            if r.status_code != 200:
                return None
            data = r.json()
            translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
            src = (data[2] if len(data) > 2 else "?") or "?"
            return translated, src
    except (httpx.HTTPError, IndexError, TypeError):
        return None


async def run_qr(bot: Bot, chat_id: int, user_id: int, text: str):
    await bot.send_chat_action(chat_id, "upload_photo")
    png = await make_qr(text)
    if png:
        await bot.send_photo(chat_id, png, caption=txt.TOOLS["qr_done"])
    else:
        await bot.send_message(chat_id, txt.GENERIC_ERROR)


async def run_weather(bot: Bot, chat_id: int, user_id: int, city: str):
    await bot.send_message(chat_id, "⏳ دارم آب‌وهوا رو می‌گیرم...")
    w = await weather(city)
    if w:
        await bot.send_message(chat_id, w, disable_preview=True)
    else:
        await bot.send_message(chat_id, "😕 شهری با این اسم پیدا نشد.")


async def run_translate(bot: Bot, chat_id: int, user_id: int, text: str):
    await bot.send_message(chat_id, "⏳ دارم ترجمه می‌کنم...")
    target = "fa"
    m = None
    if len(text) > 3 and text[2] in " :=" and text[:2].isalpha():
        m = text[:2].lower()
    if m:
        target = m
        text = text[3:].strip()
    res = await translate(text, target)
    if res:
        translated, src = res
        await bot.send_message(chat_id, txt.TOOLS["tr_fmt"].format(
            src=src, dest=target, translated=translated))
    else:
        await bot.send_message(chat_id, txt.GENERIC_ERROR)
