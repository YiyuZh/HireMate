"""SQLite-backed user store for HireMate."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .db import get_connection, now_str


def _normalize_user_row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "user_id": str(row["user_id"] or ""),
        "email": str(row["email"] or ""),
        "name": str(row["name"] or ""),
        "password_hash": str(row["password_hash"] or ""),
        "is_active": bool(int(row["is_active"] or 0)),
        "is_admin": bool(int(row["is_admin"] or 0)),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "last_login_at": str(row["last_login_at"] or ""),
    }


def count_users() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(1) AS total_count FROM users").fetchone()
    return int(row["total_count"] or 0) if row is not None else 0


def list_users() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT user_id, email, name, password_hash, is_active, is_admin, created_at, updated_at, last_login_at
            FROM users
            ORDER BY created_at ASC, email ASC
            """
        ).fetchall()
    return [_normalize_user_row(row) or {} for row in rows]


def get_user_by_email(email: str) -> dict[str, Any] | None:
    lookup = (email or "").strip().lower()
    if not lookup:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT user_id, email, name, password_hash, is_active, is_admin, created_at, updated_at, last_login_at
            FROM users
            WHERE lower(email) = lower(?)
            LIMIT 1
            """,
            (lookup,),
        ).fetchone()
    return _normalize_user_row(row)


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    lookup = (user_id or "").strip()
    if not lookup:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT user_id, email, name, password_hash, is_active, is_admin, created_at, updated_at, last_login_at
            FROM users
            WHERE user_id = ?
            LIMIT 1
            """,
            (lookup,),
        ).fetchone()
    return _normalize_user_row(row)


def create_user(
    *,
    email: str,
    name: str,
    password_hash: str,
    is_active: bool = True,
    is_admin: bool = False,
) -> dict[str, Any]:
    email_value = (email or "").strip().lower()
    name_value = (name or "").strip()
    password_hash_value = (password_hash or "").strip()
    if not email_value:
        raise ValueError("邮箱不能为空。")
    if not name_value:
        raise ValueError("姓名不能为空。")
    if not password_hash_value:
        raise ValueError("密码哈希不能为空。")

    ts = now_str()
    user_id = f"user_{uuid4().hex}"
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users(
                user_id, email, name, password_hash,
                is_active, is_admin, created_at, updated_at, last_login_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                email_value,
                name_value,
                password_hash_value,
                1 if is_active else 0,
                1 if is_admin else 0,
                ts,
                ts,
                "",
            ),
        )
        conn.commit()
    return get_user_by_id(user_id) or {
        "user_id": user_id,
        "email": email_value,
        "name": name_value,
        "password_hash": password_hash_value,
        "is_active": bool(is_active),
        "is_admin": bool(is_admin),
        "created_at": ts,
        "updated_at": ts,
        "last_login_at": "",
    }


def update_user_password_hash(user_id: str, password_hash: str) -> bool:
    lookup = (user_id or "").strip()
    password_hash_value = (password_hash or "").strip()
    if not lookup or not password_hash_value:
        return False
    ts = now_str()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET password_hash = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (password_hash_value, ts, lookup),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def set_user_active(user_id: str, is_active: bool) -> bool:
    lookup = (user_id or "").strip()
    if not lookup:
        return False
    ts = now_str()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET is_active = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (1 if is_active else 0, ts, lookup),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def set_user_admin(user_id: str, is_admin: bool) -> bool:
    lookup = (user_id or "").strip()
    if not lookup:
        return False
    ts = now_str()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET is_admin = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (1 if is_admin else 0, ts, lookup),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def update_last_login(user_id: str) -> bool:
    lookup = (user_id or "").strip()
    if not lookup:
        return False
    ts = now_str()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET last_login_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (ts, ts, lookup),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0
