"""List HireMate users."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.db import init_db
from src.user_store import list_users


def main() -> int:
    init_db()
    users = list_users()
    print("user_id\temail\tname\tis_admin\tis_active\tcreated_at\tlast_login_at")
    for user in users:
        print(
            "\t".join(
                [
                    str(user.get("user_id") or ""),
                    str(user.get("email") or ""),
                    str(user.get("name") or ""),
                    str(user.get("is_admin") or False),
                    str(user.get("is_active") or False),
                    str(user.get("created_at") or ""),
                    str(user.get("last_login_at") or ""),
                ]
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
