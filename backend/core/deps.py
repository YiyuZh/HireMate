from __future__ import annotations

from typing import Any

import jwt
from fastapi import Cookie, Header, HTTPException, Request, status

from backend.core.config import settings
from backend.core.security import decode_token
from src.user_store import get_user_by_id


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def get_current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get(settings.access_cookie_name)
    if not token:
        raise _unauthorized()
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.PyJWTError as exc:
        raise _unauthorized(str(exc)) from exc
    user_id = str(payload.get("sub") or "").strip()
    user = get_user_by_id(user_id)
    if not user:
        raise _unauthorized("User not found")
    return user


def get_refresh_payload(request: Request) -> dict[str, Any]:
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise _unauthorized("Refresh token missing")
    try:
        return decode_token(token, expected_type="refresh")
    except jwt.PyJWTError as exc:
        raise _unauthorized(str(exc)) from exc


def require_admin(user: dict[str, Any] = None) -> dict[str, Any]:
    if not user or not bool(user.get("is_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def verify_csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="hm_csrf"),
) -> None:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return
    cookie_name = settings.csrf_cookie_name
    csrf_from_cookie = request.cookies.get(cookie_name) or csrf_cookie
    if not csrf_from_cookie or not x_csrf_token or csrf_from_cookie != x_csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")

