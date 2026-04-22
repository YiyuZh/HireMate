from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Response

from backend.core.config import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_token(payload: dict[str, Any], *, token_type: str, expires_delta: timedelta) -> str:
    now = _utcnow()
    body = {
        **payload,
        "type": token_type,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(body, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, *, expected_type: str) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
    )
    token_type = str(payload.get("type") or "")
    if token_type != expected_type:
        raise jwt.InvalidTokenError(f"invalid token type: {token_type}")
    return payload


def create_auth_tokens(user: dict[str, Any]) -> dict[str, str]:
    base_payload = {
        "sub": str(user.get("user_id") or ""),
        "email": str(user.get("email") or ""),
        "name": str(user.get("name") or ""),
        "is_admin": bool(user.get("is_admin")),
    }
    access_token = create_token(
        base_payload,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_minutes),
    )
    refresh_token = create_token(
        base_payload,
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_days),
    )
    csrf_token = secrets.token_urlsafe(24)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "csrf_token": csrf_token,
    }


def set_auth_cookies(response: Response, tokens: dict[str, str]) -> None:
    response.set_cookie(
        key=settings.access_cookie_name,
        value=tokens["access_token"],
        httponly=True,
        secure=settings.secure_cookies,
        samesite=settings.same_site,
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=tokens["refresh_token"],
        httponly=True,
        secure=settings.secure_cookies,
        samesite=settings.same_site,
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=tokens["csrf_token"],
        httponly=False,
        secure=settings.secure_cookies,
        samesite=settings.same_site,
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    for key in [settings.access_cookie_name, settings.refresh_cookie_name, settings.csrf_cookie_name]:
        response.delete_cookie(key=key, path="/", samesite=settings.same_site)

