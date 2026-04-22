from __future__ import annotations

from typing import Any

from backend.core.security import create_auth_tokens
from src.auth import authenticate_user, mark_login_success
from src.user_store import get_user_by_id


def login(email: str, password: str) -> tuple[dict[str, Any], dict[str, str]]:
    user, error_message = authenticate_user(email, password)
    if user is None:
        raise ValueError(error_message or "登录失败")
    user_id = str(user.get("user_id") or "")
    mark_login_success(user_id)
    fresh_user = get_user_by_id(user_id) or user
    tokens = create_auth_tokens(fresh_user)
    return fresh_user, tokens


def refresh_from_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    user_id = str(payload.get("sub") or "").strip()
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("用户不存在")
    tokens = create_auth_tokens(user)
    return user, tokens

