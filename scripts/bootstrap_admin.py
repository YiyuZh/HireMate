"""Create an admin account for HireMate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.auth import create_user_account
from src.db import init_db
from src.user_store import get_user_by_email


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a HireMate admin account.")
    parser.add_argument("--email", required=True, help="Admin email")
    parser.add_argument("--name", required=True, help="Admin display name")
    parser.add_argument("--password", required=True, help="Admin password (min 8 chars)")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    init_db()
    if get_user_by_email(args.email):
        print("创建失败：该邮箱已存在。", file=sys.stderr)
        return 1

    try:
        user = create_user_account(
            email=args.email,
            name=args.name,
            password=args.password,
            is_admin=True,
            is_active=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"创建失败：{exc}", file=sys.stderr)
        return 1

    print("管理员创建成功")
    print(f"user_id={user['user_id']}")
    print(f"email={user['email']}")
    print(f"name={user['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
