"""Set HireMate user admin flag."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.db import init_db
from src.user_store import get_user_by_email, get_user_by_id, set_user_admin


def _parse_bool(raw_value: str) -> bool:
    value = (raw_value or "").strip().lower()
    if value in {"true", "1", "yes", "y"}:
        return True
    if value in {"false", "0", "no", "n"}:
        return False
    raise ValueError("参数必须为 true 或 false。")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Set HireMate user admin flag.")
    lookup_group = parser.add_mutually_exclusive_group(required=True)
    lookup_group.add_argument("--user-id", help="Target user id")
    lookup_group.add_argument("--email", help="Target user email")
    parser.add_argument("--admin", required=True, help="true or false")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    init_db()

    user = get_user_by_id(args.user_id) if args.user_id else get_user_by_email(args.email)
    if not user:
        print("设置失败：未找到目标用户。", file=sys.stderr)
        return 1

    try:
        is_admin = _parse_bool(args.admin)
    except ValueError as exc:
        print(f"设置失败：{exc}", file=sys.stderr)
        return 1

    if not set_user_admin(str(user.get("user_id") or ""), is_admin):
        print("设置失败：管理员状态未更新。", file=sys.stderr)
        return 1

    print("管理员状态更新成功")
    print(f"user_id={user['user_id']}")
    print(f"email={user['email']}")
    print(f"is_admin={is_admin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
