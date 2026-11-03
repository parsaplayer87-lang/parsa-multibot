"""همگام‌سازی داده‌ها با گیت‌هاب:
1) push دیتابیس (botdata.db) به ریپوی خصوصی DATA_REPO
2) push snapshot رمزنگاری‌شده (snapshot.enc) به ریپوی Pages ادمین
دستورات CLI:  python -m bot.datastore pull | push
"""
import asyncio
import base64
import datetime as dt
import os
import subprocess
import sys

import config

def _token_url(repo: str) -> str:
    if not config.GH_PAT:
        return f"https://github.com/{repo}.git"
    return f"https://x-access-token:{config.GH_PAT}@github.com/{repo}.git"

def _run(cmd: list[str], cwd: str | None = None, timeout: int = 90) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, shell=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)

# ---------------- data repo ----------------
def _data_repo_dir() -> str:
    return config.DATA_DIR

def pull_data() -> str:
    """دیتای قبلی را از ریپوی خصوصی می‌کشد (clone نازک)."""
    d = _data_repo_dir()
    os.makedirs(d, exist_ok=True)
    if not os.path.isdir(os.path.join(d, ".git")):
        # clone نازک: فقط فایل‌های دیتا
        r, out = _run(["git", "clone", "--depth", "1", "--filter=blob:none",
                       _token_url(config.DATA_REPO), d], timeout=180)
        if r != 0:
            print(f"[datastore] clone failed: {out[:300]}", flush=True)
            return "clone-failed"
        # فایل‌های کاری داخل ریپو نمی‌مانند
        for junk in ("README.md", ".gitignore", "LICENSE"):
            pass
        return "cloned"
    r, out = _run(["git", "fetch", "--depth", "1", "origin"], cwd=d)
    if r == 0:
        _run(["git", "reset", "--hard", "origin/HEAD"], cwd=d)
    # اگر remote به خاطر توکن جدید عوض شده، آپدیت کن
    _run(["git", "remote", "set-url", "origin", _token_url(config.DATA_REPO)], cwd=d)
    return "pulled"

def push_data() -> str:
    """اسنپ‌شات دیتابیس را commit و push می‌کند (با retry برای تداخل)."""
    d = _data_repo_dir()
    if not os.path.isdir(os.path.join(d, ".git")):
        # ریپو خالی است: init کن
        _run(["git", "init", "-b", "main"], cwd=d)
        _run(["git", "remote", "add", "origin", _token_url(config.DATA_REPO)], cwd=d)
        _run(["git", "config", "user.email", "bot@users.noreply.github.com"], cwd=d)
        _run(["git", "config", "user.name", "data-bot"], cwd=d)
    _run(["git", "config", "user.email", "bot@users.noreply.github.com"], cwd=d)
    _run(["git", "config", "user.name", "data-bot"], cwd=d)
    _run(["git", "remote", "set-url", "origin", _token_url(config.DATA_REPO)], cwd=d)
    # فایل‌های موقت نباید پوش شوند
    with open(os.path.join(d, ".gitignore"), "w") as f:
        f.write("live.db\nlive.db-wal\nlive.db-shm\ntmp_dl/\ncookies.txt\n*.tmp\n")
    for attempt in range(3):
        _run(["git", "add", "-A"], cwd=d)
        msg = f"data sync {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}"
        rc, out = _run(["git", "commit", "-m", msg], cwd=d)
        if rc != 0 and "nothing to commit" not in out:
            # احتمالاً merge conflict
            _run(["git", "fetch", "origin"], cwd=d)
            _run(["git", "reset", "--hard", "origin/HEAD"], cwd=d)
            continue
        rc, out = _run(["git", "push", "-u", "origin", "main"], cwd=d, timeout=180)
        if rc == 0:
            return "pushed"
        if "fetch first" in out.lower() or "rejected" in out.lower() or "non-fast-forward" in out.lower():
            _run(["git", "fetch", "origin"], cwd=d)
            _run(["git", "reset", "--hard", "origin/HEAD"], cwd=d)
            continue
        print(f"[datastore] push error: {out[:300]}", flush=True)
    return "push-failed"

# ---------------- admin web snapshot ----------------
def encrypt_snapshot(payload: bytes) -> bytes | None:
    if not config.ADMIN_WEB_KEY:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = base64.b64decode(config.ADMIN_WEB_KEY)
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, payload, None)
        return nonce + ct  # nonce اول فایل
    except Exception as e:
        print(f"[datastore] encrypt failed: {e}", flush=True)
        return None

async def _snapshot():
    import db
    return await db.snapshot_payload()

async def push_admin_snapshot_async() -> str:
    """snapshot.enc را در ریپوی Pages ادمین پوش می‌کند (shallow clone موقت)."""
    import db as _db
    if not config.ADMIN_WEB_KEY or not config.GH_PAT:
        return "skipped"
    enc = encrypt_snapshot(await _db.snapshot_payload())
    if not enc:
        return "encrypt-failed"
    tmpdir = os.path.join(config.DATA_DIR, "adminweb")
    os.makedirs(config.DATA_DIR, exist_ok=True)
    try:
        if not os.path.isdir(os.path.join(tmpdir, ".git")):
            r, out = _run(["git", "clone", "--depth", "1",
                           _token_url(config.ADMIN_WEB_REPO), tmpdir], timeout=180)
            if r != 0:
                print(f"[datastore] adminweb clone failed: {out[:200]}", flush=True)
                return "clone-failed"
        _run(["git", "remote", "set-url", "origin", _token_url(config.ADMIN_WEB_REPO)], cwd=tmpdir)
        with open(os.path.join(tmpdir, "snapshot.enc"), "wb") as f:
            f.write(enc)
        _run(["git", "config", "user.email", "bot@users.noreply.github.com"], cwd=tmpdir)
        _run(["git", "config", "user.name", "snapshot-bot"], cwd=tmpdir)
        _run(["git", "add", "snapshot.enc"], cwd=tmpdir)
        rc, out = _run(["git", "commit", "-m",
                        f"snapshot {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}"], cwd=tmpdir)
        if rc != 0 and "nothing to commit" not in out:
            return "commit-failed"
        rc, out = _run(["git", "push", "origin", "HEAD:main"], cwd=tmpdir, timeout=120)
        if rc != 0:
            # تلاش مجدد بعد از fetch
            _run(["git", "fetch", "origin"], cwd=tmpdir)
            _run(["git", "reset", "--hard", "origin/HEAD"], cwd=tmpdir)
            with open(os.path.join(tmpdir, "snapshot.enc"), "wb") as f:
                f.write(enc)
            _run(["git", "add", "snapshot.enc"], cwd=tmpdir)
            _run(["git", "commit", "-m", "snapshot retry"], cwd=tmpdir)
            rc, out = _run(["git", "push", "origin", "HEAD:main"], cwd=tmpdir, timeout=120)
            if rc != 0:
                print(f"[datastore] adminweb push failed: {out[:200]}", flush=True)
                return "push-failed"
        return "pushed"
    finally:
        pass

# ---------------- CLI ----------------
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "push"
    if cmd == "pull":
        print(pull_data())
    elif cmd == "push":
        print(push_data())
    elif cmd == "push-all":
        print(push_data())
        try:
            asyncio.run(push_admin_snapshot_async())
        except Exception as e:
            print(f"admin snapshot error: {e}")
    else:
        print("usage: python -m bot.datastore pull|push|push-all")

if __name__ == "__main__":
    main()
