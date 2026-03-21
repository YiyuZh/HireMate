"""Bootstrap HireMate MySQL schema."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.db import bootstrap_mysql_schema, get_mysql_database_name, list_mysql_tables


def main() -> int:
    os.environ["HIREMATE_DB_BACKEND"] = "mysql"
    try:
        result = bootstrap_mysql_schema()
        tables = list_mysql_tables()
    except Exception as exc:  # noqa: BLE001
        print(f"MySQL schema bootstrap failed: {exc}", file=sys.stderr)
        return 1

    print("MySQL schema bootstrap completed.")
    print(f"database={get_mysql_database_name() or '(unknown)'}")
    print(f"schema_statements={result['schema_statements']}")
    print(f"indexes_applied={result['indexes_applied']}")
    print(f"indexes_skipped={result['indexes_skipped']}")
    print(f"table_count={len(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
