"""تنظیمات مرکزی — همه از environment می‌آید (GitHub Secrets)."""
import os

def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v.strip() if v else default

# --- توکن بازوها (بله) ---
BOT_TOKEN_MAIN = _env("BOT_MAIN_TOKEN")
BOT_TOKEN_BACKUP = _env("BOT_BACKUP_TOKEN")
BOT_TOKEN_ADMIN = _env("BOT_ADMIN_TOKEN")

API_BASE = _env("BALE_API_BASE", "https://tapi.bale.ai/bot")
FILE_BASE = _env("BALE_FILE_BASE", "https://tapi.bale.ai/file/bot")

# آیدی عددی ادمین؛ اگر خالی باشد حالت Claim: اولین /start در بازوی ادمین، ادمین می‌شود.
ADMIN_ID = int(_env("ADMIN_ID", "0") or 0)

# --- گیتهاب (ذخیره داده) ---
GH_OWNER = _env("GH_OWNER", "parsaplayer87-lang")
DATA_REPO = _env("DATA_REPO", f"{GH_OWNER}/parsa-multibot-data")
ADMIN_WEB_REPO = _env("ADMIN_WEB_REPO", f"{GH_OWNER}/parsa-multibot-admin")
ADMIN_WEB_URL = _env("ADMIN_WEB_URL", f"https://{GH_OWNER}.github.io/parsa-multibot-admin/")
GH_PAT = _env("GH_PAT")  # برای push به ریپوی دیتا و API گیت‌هاب

# کلید AES-256 (base64 ۳۲ بایتی) برای رمزنگاری snapshot مینی‌اپ ادمین
ADMIN_WEB_KEY = _env("ADMIN_WEB_KEY")

# --- محدودیت‌ها ---
TELEGRAM_LIKE_MAX = 48 * 1024 * 1024          # سقف امن آپلود فایل به بله (~۵۰MB)
MAX_CONCURRENT_DOWNLOADS = int(_env("MAX_CONCURRENT_DOWNLOADS", "2"))
MAX_ACTIVE_JOBS_PER_USER = int(_env("MAX_ACTIVE_JOBS_PER_USER", "2"))
SHUTDOWN_MINUTES = int(_env("SHUTDOWN_MINUTES", "300"))  # خروج نرم قبل از سقف job (۰=هرگز)
SYNC_INTERVAL = int(_env("SYNC_INTERVAL", "600"))        # هر چند ثانیه دیتا پوش شود
DATA_DIR = _env("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
DOWNLOAD_DIR = os.path.join(DATA_DIR, "tmp_dl")

# کوکی‌های اختیاری برای یوتیوب/اینستاگرام و ... (فرمت Netscape cookies.txt)
YTDLP_COOKIES = _env("YTDLP_COOKIES")  # محتوای فایل؛ در runtime نوشته می‌شود

# --- قابلیت‌ها و کلیدهای تنظیمات ---
FEATURES = {
    "downloader": "📥 دانلودر همه‌چیز",
    "websearch": "🔍 جستجوی وب",
    "yt": "▶️ یوتیوب",
    "gh": "🐙 گیت‌هاب",
    "tools": "🧰 ابزارها",
}
BOTS = ("main", "backup", "admin")
BOT_NAMES = {"main": "ربات اصلی", "backup": "ربات پشتیبان", "admin": "ربات ادمین"}

LOG_LEVEL = _env("LOG_LEVEL", "INFO")

def cookies_file() -> str | None:
    """اگر کوکی ست شده باشد در فایل می‌نویسد و مسیرش را می‌دهد."""
    if not YTDLP_COOKIES:
        return None
    os.makedirs(DATA_DIR, exist_ok=True)
    p = os.path.join(DATA_DIR, "cookies.txt")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(YTDLP_COOKIES.replace("\\n", "\n"))
        return p
    except Exception:
        return None
