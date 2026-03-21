"""One-time migration from legacy JSON files into SQLite."""

from __future__ import annotations

from src.db import get_db_path, init_db
from src.legacy_json_compat import migrate_legacy_json_if_needed


def main() -> None:
    init_db()
    migrate_legacy_json_if_needed(force=True)
    print(f"Migration completed. SQLite database: {get_db_path()}")


if __name__ == "__main__":
    main()
