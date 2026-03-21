"""Check whether HireMate MySQL connection is reachable."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.db import get_db_backend, get_mysql_database_name, list_mysql_tables, open_mysql_connection


def main() -> int:
    os.environ.setdefault("HIREMATE_DB_BACKEND", "mysql")
    try:
        conn = open_mysql_connection()
        try:
            row = conn.execute("SELECT 1 AS ping_value, DATABASE() AS db_name").fetchone()
        finally:
            conn.close()
        tables = list_mysql_tables()
    except Exception as exc:  # noqa: BLE001
        print(f"MySQL check failed: {exc}", file=sys.stderr)
        return 1

    print("MySQL connection is ready.")
    print(f"backend={get_db_backend()}")
    print(f"database={row['db_name'] or get_mysql_database_name() or '(unknown)'}")
    print(f"select_1={row['ping_value']}")
    print(f"table_count={len(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
