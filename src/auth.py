"""Minimal password auth helpers for HireMate."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import MutableMapping
from typing import Any

from .user_store import create_user, get_user_by_email, update_last_login, update_user_password_hash

SESSION_USER_KEY = "auth_current_user"
PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 390000
SALT_BYTES = 16


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_password(password: str) -> str:
    if len(password or "") < 8:
        raise ValueError("密码至少需要 8 位。")
    salt = os.urandom(SALT_BYTES).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    raw_password = password or ""
    stored_hash = (password_hash or "").strip()
    if not raw_password or not stored_hash:
        return False

    try:
        algorithm, iterations_raw, salt_hex, digest_hex = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != PBKDF2_ALGORITHM:
        return False

    try:
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        raw_password.encode("utf-8"),
        salt,
        iterations,
    ).hex()
    return hmac.compare_digest(candidate, digest_hex)


def find_user_by_email(email: str) -> dict[str, Any] | None:
    return get_user_by_email(normalize_email(email))


def create_user_account(
    *,
    email: str,
    name: str,
    password: str,
    is_active: bool = True,
    is_admin: bool = False,
) -> dict[str, Any]:
    email_value = normalize_email(email)
    if not email_value:
        raise ValueError("邮箱不能为空。")
    if not (name or "").strip():
        raise ValueError("姓名不能为空。")
    if find_user_by_email(email_value):
        raise ValueError("该邮箱已存在，请使用其他邮箱。")
    return create_user(
        email=email_value,
        name=(name or "").strip(),
        password_hash=hash_password(password),
        is_active=is_active,
        is_admin=is_admin,
    )


def verify_user_password(user: dict[str, Any] | None, password: str) -> bool:
    if not isinstance(user, dict):
        return False
    return verify_password(password, str(user.get("password_hash") or ""))


def authenticate_user(email: str, password: str) -> tuple[dict[str, Any] | None, str]:
    email_value = normalize_email(email)
    if not email_value:
        return None, "请输入邮箱。"
    if not (password or ""):
        return None, "请输入密码。"

    user = find_user_by_email(email_value)
    if user is None:
        return None, "未找到对应账号，请检查邮箱或联系管理员。"
    if not bool(user.get("is_active")):
        return None, "该账号已停用，请联系管理员。"
    if not verify_user_password(user, password):
        return None, "邮箱或密码错误。"
    return user, ""


def reset_user_password(user_id: str, new_password: str) -> bool:
    password_hash = hash_password(new_password)
    return update_user_password_hash((user_id or "").strip(), password_hash)


def mark_login_success(user_id: str) -> None:
    update_last_login((user_id or "").strip())


def login_user(session_state: MutableMapping[str, Any], user: dict[str, Any]) -> None:
    session_state[SESSION_USER_KEY] = {
        "user_id": str(user.get("user_id") or ""),
        "email": str(user.get("email") or ""),
        "name": str(user.get("name") or ""),
        "is_active": bool(user.get("is_active", False)),
        "is_admin": bool(user.get("is_admin", False)),
        "last_login_at": str(user.get("last_login_at") or ""),
    }


def logout_user(session_state: MutableMapping[str, Any]) -> None:
    session_state.pop(SESSION_USER_KEY, None)


def get_current_user(session_state: MutableMapping[str, Any]) -> dict[str, Any] | None:
    raw_user = session_state.get(SESSION_USER_KEY)
    if not isinstance(raw_user, dict):
        return None
    user_id = str(raw_user.get("user_id") or "").strip()
    email = normalize_email(str(raw_user.get("email") or ""))
    name = str(raw_user.get("name") or "").strip()
    if not user_id or not email:
        return None
    return {
        "user_id": user_id,
        "email": email,
        "name": name,
        "is_active": bool(raw_user.get("is_active", False)),
        "is_admin": bool(raw_user.get("is_admin", False)),
        "last_login_at": str(raw_user.get("last_login_at") or ""),
    }
