"""
Authentication helpers: password hashing, token verification, FastAPI dependency.
"""
import logging
import secrets
import time
from typing import Optional

import aiosqlite
from fastapi import Header, HTTPException
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

DEFAULT_PASSWORD = "Huawei@123"
_pwd_ctx = CryptContext(schemes=["sha256_crypt"], deprecated="auto")

# In-memory token cache — avoids a DB round-trip on every authenticated request.
# { token: valid_until_monotonic }
_TOKEN_CACHE: dict[str, float] = {}
_TOKEN_TTL = 30.0   # seconds before re-validating from DB

# Session expiry (seconds)
SESSION_TTL_SECONDS = 7 * 24 * 3600


async def ensure_admin(db_path: str) -> None:
    """Create admin account with default password if settings table is empty."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key='password_hash'"
        ) as cur:
            row = await cur.fetchone()
        if not row:
            hashed = _pwd_ctx.hash(DEFAULT_PASSWORD)
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('password_hash', ?)",
                (hashed,),
            )
            await db.commit()
            logger.info("Admin account created with default password")


def _invalidate_token_cache() -> None:
    """Clear the in-memory token cache (call on login/logout/password-change)."""
    _TOKEN_CACHE.clear()


async def login(password: str, db_path: str) -> Optional[str]:
    """Verify password; on success create and return a new session token, else None."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key='password_hash'"
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        if not _pwd_ctx.verify(password, row[0]):
            return None
        token = secrets.token_hex(32)
        now = int(time.time())
        expires_at = now + SESSION_TTL_SECONDS
        await db.execute(
            "INSERT OR REPLACE INTO sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, now, expires_at),
        )
        await db.commit()
    _invalidate_token_cache()
    return token


async def logout(token: str, db_path: str) -> None:
    """Invalidate a single session token."""
    if not token:
        return
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM sessions WHERE token=?", (token,))
        await db.commit()
    _invalidate_token_cache()


async def change_password(old_pw: str, new_pw: str, db_path: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key='password_hash'"
        ) as cur:
            row = await cur.fetchone()
        if not row or not _pwd_ctx.verify(old_pw, row[0]):
            return False
        hashed = _pwd_ctx.hash(new_pw)
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('password_hash', ?)",
            (hashed,),
        )
        # Invalidate all sessions so re-login is required everywhere
        await db.execute("DELETE FROM sessions")
        await db.commit()
    _invalidate_token_cache()
    return True


async def _verify_token(token: str, db_path: str) -> bool:
    if not token:
        return False
    now = time.monotonic()
    # Fast path: cache hit (valid within TTL window)
    cached_until = _TOKEN_CACHE.get(token)
    if cached_until is not None and now < cached_until:
        return True
    # Slow path: validate from DB then cache result
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT expires_at FROM sessions WHERE token=?",
            (token,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        _TOKEN_CACHE.pop(token, None)
        return False
    expires_at = int(row[0])
    if expires_at <= int(time.time()):
        # best-effort cleanup
        try:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("DELETE FROM sessions WHERE token=?", (token,))
                await db.commit()
        except Exception:
            pass
        _TOKEN_CACHE.pop(token, None)
        return False
    _TOKEN_CACHE[token] = now + _TOKEN_TTL
    return True


# Lazy import to avoid circular dependency
def make_auth_dependency(db_path_getter):
    async def require_auth(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = None,   # populated by Query() below, but injected via override
    ):
        # Accept Bearer header (normal requests) or query param (EventSource / SSE)
        if authorization and authorization.startswith("Bearer "):
            t = authorization[7:]
        elif token:
            t = token
        else:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not await _verify_token(t, db_path_getter()):
            raise HTTPException(status_code=401, detail="Invalid or expired token")
    return require_auth


def make_sse_auth_dependency(db_path_getter):
    """Like make_auth_dependency but also reads ?token= query param for EventSource."""
    from fastapi import Query as FQuery
    async def require_auth_sse(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = FQuery(None, description="Bearer token (for EventSource)"),
    ):
        if authorization and authorization.startswith("Bearer "):
            t = authorization[7:]
        elif token:
            t = token
        else:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not await _verify_token(t, db_path_getter()):
            raise HTTPException(status_code=401, detail="Invalid or expired token")
    return require_auth_sse
