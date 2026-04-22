from __future__ import annotations

from datetime import datetime
from typing import Any


def operator_from_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(user.get("user_id") or "").strip(),
        "name": str(user.get("name") or "").strip(),
        "email": str(user.get("email") or "").strip(),
        "is_admin": bool(user.get("is_admin")),
    }


def now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

