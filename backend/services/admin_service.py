from __future__ import annotations

from typing import Any

from src.auth import create_user_account, reset_user_password
from src.ai_reviewer import get_latest_ai_call_status
from src.db import get_connection, get_db_backend
from src.resume_loader import check_ocr_capabilities
from src.user_store import list_users, set_user_active, set_user_admin


def get_system_health() -> dict[str, Any]:
    backend = get_db_backend()
    db_ok = False
    users_count = 0
    jobs_count = 0
    batches_count = 0
    conn = get_connection()
    try:
        with conn:
            conn.execute("SELECT 1")
            users_count = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
            jobs_count = int(conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"])
            batches_count = int(conn.execute("SELECT COUNT(*) AS c FROM candidate_batches").fetchone()["c"])
            db_ok = True
    finally:
        conn.close()

    return {
        "database": {
            "backend": backend,
            "ok": db_ok,
            "users_count": users_count,
            "jobs_count": jobs_count,
            "batches_count": batches_count,
        },
        "ocr": check_ocr_capabilities(),
        "latest_ai_call": get_latest_ai_call_status() or {},
    }


def get_admin_users() -> list[dict[str, Any]]:
    return list_users()


def _find_user(user_id: str) -> dict[str, Any] | None:
    lookup = str(user_id or "").strip()
    for item in list_users():
        if str(item.get("user_id") or "").strip() == lookup:
            return item
    return None


def _active_admin_count() -> int:
    return sum(1 for item in list_users() if bool(item.get("is_admin")) and bool(item.get("is_active")))


def create_admin_user(*, email: str, name: str, password: str, is_admin: bool = False) -> dict[str, Any]:
    return create_user_account(
        email=email,
        name=name,
        password=password,
        is_active=True,
        is_admin=is_admin,
    )


def reset_admin_user_password(*, user_id: str, new_password: str) -> bool:
    return reset_user_password(user_id, new_password)


def set_admin_user_active(*, user_id: str, is_active: bool) -> bool:
    target = _find_user(user_id)
    if target is None:
        return False
    if not is_active and bool(target.get("is_admin")) and bool(target.get("is_active")) and _active_admin_count() <= 1:
        raise ValueError("Cannot deactivate the last active admin")
    return set_user_active(user_id, is_active)


def set_admin_user_admin(*, user_id: str, is_admin: bool) -> bool:
    target = _find_user(user_id)
    if target is None:
        return False
    if not is_admin and bool(target.get("is_admin")) and bool(target.get("is_active")) and _active_admin_count() <= 1:
        raise ValueError("Cannot remove admin role from the last active admin")
    return set_user_admin(user_id, is_admin)
