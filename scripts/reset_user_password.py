"""Reset a HireMate user password."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.auth import reset_user_password
from src.db import init_db
from src.user_store import get_user_by_email, get_user_by_id


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reset a HireMate user password.")
    lookup_group = parser.add_mutually_exclusive_group(required=True)
    lookup_group.add_argument("--user-id", help="Target user id")
    lookup_group.add_argument("--email", help="Target user email")
    parser.add_argument("--password", required=True, help="New password (min 8 chars)")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    init_db()

    user = get_user_by_id(args.user_id) if args.user_id else get_user_by_email(args.email)
    if not user:
        print("重置失败：未找到目标用户。", file=sys.stderr)
        return 1

    try:
        ok = reset_user_password(str(user.get("user_id") or ""), args.password)
    except Exception as exc:  # noqa: BLE001
        print(f"重置失败：{exc}", file=sys.stderr)
        return 1

    if not ok:
        print("重置失败：密码未更新。", file=sys.stderr)
        return 1

    print("密码重置成功")
    print(f"user_id={user['user_id']}")
    print(f"email={user['email']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
