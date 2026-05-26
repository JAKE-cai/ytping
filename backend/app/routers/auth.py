import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..auth import change_password, login, logout, make_auth_dependency
from ..database import DB_PATH

router = APIRouter(prefix="/api/auth", tags=["auth"])
_require_auth = make_auth_dependency(lambda: DB_PATH)

# --- Simple in-memory rate limiter for login ---
# Tracks (ip -> [attempt_timestamps]) with a sliding 5-minute window.
_MAX_ATTEMPTS = 10       # max failures per window
_WINDOW_SECS = 300       # 5-minute sliding window
_LOCKOUT_SECS = 600      # 10-minute lockout after threshold
_login_attempts: dict[str, list[float]] = defaultdict(list)
_lockout_until: dict[str, float] = {}


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    if _lockout_until.get(ip, 0) > now:
        remaining = int(_lockout_until[ip] - now)
        raise HTTPException(status_code=429, detail=f"登录尝试过多，请 {remaining} 秒后重试")
    # Purge old attempts outside the window
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _WINDOW_SECS]


def _record_failure(ip: str) -> None:
    now = time.time()
    _login_attempts[ip].append(now)
    if len(_login_attempts[ip]) >= _MAX_ATTEMPTS:
        _lockout_until[ip] = now + _LOCKOUT_SECS
        _login_attempts[ip].clear()


def _clear_failures(ip: str) -> None:
    _login_attempts.pop(ip, None)
    _lockout_until.pop(ip, None)


class LoginBody(BaseModel):
    password: str


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pw_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码长度至少 8 位")
        return v


@router.post("/login")
async def api_login(body: LoginBody, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)
    token = await login(body.password, DB_PATH)
    if not token:
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="密码错误")
    _clear_failures(client_ip)
    return {"token": token}


@router.post("/logout", dependencies=[Depends(_require_auth)])
async def api_logout(request: Request):
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    await logout(token, DB_PATH)
    return {"ok": True}


@router.get("/me", dependencies=[Depends(_require_auth)])
async def api_me():
    return {"ok": True}


@router.put("/password", dependencies=[Depends(_require_auth)])
async def api_change_password(body: ChangePasswordBody):
    ok = await change_password(body.old_password, body.new_password, DB_PATH)
    if not ok:
        raise HTTPException(status_code=400, detail="原密码错误")
    return {"ok": True}
