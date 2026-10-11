# web_ui/auth.py — cookie-сессии + CSRF для веб-панели

import hashlib
import os
import secrets
import time

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE = "lada_session"
CSRF_COOKIE = "lada_csrf"
SESSION_TTL_SECONDS = 7 * 24 * 3600

WEB_USER = os.environ.get("LADA_WEB_USER", "lada").strip()
WEB_PASS_PLAIN = os.environ.get("LADA_WEB_PASS", "").strip()
WEB_PASS_HASH = os.environ.get("LADA_WEB_PASS_HASH", "").strip()

_PASS_HASH = ""

_sessions: dict[str, dict] = {}


# ─────────────── хеш пароля ───────────────

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 200_000
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${h.hex()}"


def _verify_password(password: str, hashed: str) -> bool:
    try:
        algo, iters_s, salt_hex, hash_hex = hashed.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return secrets.compare_digest(h, expected)
    except Exception:
        return False


def _init():
    global _PASS_HASH
    if WEB_PASS_HASH:
        _PASS_HASH = WEB_PASS_HASH
    elif WEB_PASS_PLAIN:
        _PASS_HASH = _hash_password(WEB_PASS_PLAIN)


_init()


def _auth_enabled() -> bool:
    return bool(_PASS_HASH)


# ─────────────── сессии ───────────────

def _create_session() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    _sessions[token] = {
        "csrf": csrf,
        "expires": time.time() + SESSION_TTL_SECONDS,
    }
    return token, csrf


def _validate_session(token: str) -> dict | None:
    s = _sessions.get(token)
    if not s:
        return None
    if s["expires"] < time.time():
        _sessions.pop(token, None)
        return None
    return s


def _purge_expired():
    now = time.time()
    expired = [t for t, s in _sessions.items() if s["expires"] < now]
    for t in expired:
        _sessions.pop(t, None)


def _set_cookies(response: Response, token: str, csrf: str, secure: bool = False):
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True, samesite="lax", secure=secure, path="/",
    )
    response.set_cookie(
        CSRF_COOKIE, csrf,
        max_age=SESSION_TTL_SECONDS,
        httponly=False, samesite="lax", secure=secure, path="/",
    )


# ─────────────── зависимость ───────────────

async def require_auth(
    request: Request,
    lada_session: str | None = Cookie(None),
    x_csrf_token: str | None = Header(None),
):
    if not _auth_enabled():
        return True

    if not lada_session:
        raise HTTPException(401, "unauthorized")

    s = _validate_session(lada_session)
    if not s:
        raise HTTPException(401, "unauthorized")

    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if not x_csrf_token or x_csrf_token != s["csrf"]:
            raise HTTPException(403, "csrf")

    _purge_expired()
    return True


# ─────────────── роуты ───────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    if not _auth_enabled():
        raise HTTPException(400, "auth disabled")

    user_ok = secrets.compare_digest(req.username, WEB_USER)
    pass_ok = _verify_password(req.password, _PASS_HASH)

    if not (user_ok and pass_ok):
        time.sleep(0.4)
        raise HTTPException(401, "invalid credentials")

    token, csrf = _create_session()
    _set_cookies(response, token, csrf)
    return {"ok": True, "user": WEB_USER}


@router.post("/logout")
async def logout(response: Response, lada_session: str | None = Cookie(None)):
    if lada_session:
        _sessions.pop(lada_session, None)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(lada_session: str | None = Cookie(None)):
    if not _auth_enabled():
        return {"auth_enabled": False, "user": None}
    s = _validate_session(lada_session) if lada_session else None
    if not s:
        raise HTTPException(401, "unauthorized")
    return {"auth_enabled": True, "user": WEB_USER}