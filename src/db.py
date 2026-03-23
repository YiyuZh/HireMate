"""Centralized database access layer for HireMate."""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # pragma: no cover - optional until MySQL backend is enabled.
    pymysql = None
    DictCursor = None

try:
    import cryptography  # noqa: F401
except ImportError:  # pragma: no cover - optional until MySQL backend is enabled.
    cryptography = None

DEFAULT_DB_PATH = "/app/data/hiremate.db"
DEFAULT_MYSQL_CHARSET = "utf8mb4"

DB_BACKEND_SQLITE = "sqlite"
DB_BACKEND_MYSQL = "mysql"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SQL_DIR = _PROJECT_ROOT / "sql"
_MYSQL_SCHEMA_PATH = _SQL_DIR / "mysql_schema.sql"
_MYSQL_INDEXES_PATH = _SQL_DIR / "mysql_indexes.sql"

_ACTION_LOG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS candidate_action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id TEXT NOT NULL UNIQUE,
    batch_id TEXT NOT NULL DEFAULT '',
    candidate_id TEXT NOT NULL DEFAULT '',
    review_id TEXT NOT NULL DEFAULT '',
    jd_title TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL DEFAULT '',
    operator_user_id TEXT NOT NULL DEFAULT '',
    operator_name TEXT NOT NULL DEFAULT '',
    operator_email TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_action_logs_batch_candidate
ON candidate_action_logs(batch_id, candidate_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_candidate_action_logs_review
ON candidate_action_logs(review_id, created_at DESC);
"""

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    title TEXT PRIMARY KEY,
    jd_text TEXT NOT NULL,
    openings INTEGER NOT NULL DEFAULT 0,
    created_by_user_id TEXT NOT NULL DEFAULT '',
    created_by_name TEXT NOT NULL DEFAULT '',
    created_by_email TEXT NOT NULL DEFAULT '',
    updated_by_user_id TEXT NOT NULL DEFAULT '',
    updated_by_name TEXT NOT NULL DEFAULT '',
    updated_by_email TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_scoring_configs (
    job_title TEXT PRIMARY KEY,
    scoring_config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_title) REFERENCES jobs(title) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS candidate_batches (
    batch_id TEXT PRIMARY KEY,
    jd_title TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL DEFAULT '',
    created_by_name TEXT NOT NULL DEFAULT '',
    created_by_email TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    total_resumes INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    pass_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    reject_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS candidate_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    candidate_name TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    parse_status TEXT NOT NULL DEFAULT '',
    screening_result TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL DEFAULT 'unknown',
    candidate_pool TEXT NOT NULL DEFAULT '',
    manual_decision TEXT NOT NULL DEFAULT '',
    manual_note TEXT NOT NULL DEFAULT '',
    manual_priority TEXT NOT NULL DEFAULT '',
    review_summary TEXT NOT NULL DEFAULT '',
    scores_json TEXT NOT NULL DEFAULT '{}',
    extract_info_json TEXT NOT NULL DEFAULT '{}',
    row_json TEXT NOT NULL DEFAULT '{}',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_by_user_id TEXT NOT NULL DEFAULT '',
    created_by_name TEXT NOT NULL DEFAULT '',
    created_by_email TEXT NOT NULL DEFAULT '',
    last_operated_by_user_id TEXT NOT NULL DEFAULT '',
    last_operated_by_name TEXT NOT NULL DEFAULT '',
    last_operated_by_email TEXT NOT NULL DEFAULT '',
    last_operated_at TEXT NOT NULL DEFAULT '',
    lock_status TEXT NOT NULL DEFAULT 'unlocked',
    lock_owner_user_id TEXT NOT NULL DEFAULT '',
    lock_owner_name TEXT NOT NULL DEFAULT '',
    lock_owner_email TEXT NOT NULL DEFAULT '',
    lock_acquired_at TEXT NOT NULL DEFAULT '',
    lock_expires_at TEXT NOT NULL DEFAULT '',
    lock_last_heartbeat_at TEXT NOT NULL DEFAULT '',
    lock_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(batch_id, candidate_id),
    FOREIGN KEY (batch_id) REFERENCES candidate_batches(batch_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    jd_title TEXT NOT NULL DEFAULT '',
    resume_name TEXT NOT NULL DEFAULT '',
    resume_file TEXT NOT NULL DEFAULT '',
    auto_screening_result TEXT NOT NULL DEFAULT '',
    auto_risk_level TEXT NOT NULL DEFAULT 'unknown',
    reviewed_by_user_id TEXT NOT NULL DEFAULT '',
    reviewed_by_name TEXT NOT NULL DEFAULT '',
    reviewed_by_email TEXT NOT NULL DEFAULT '',
    manual_decision TEXT NOT NULL DEFAULT '',
    manual_note TEXT NOT NULL DEFAULT '',
    manual_priority TEXT NOT NULL DEFAULT '',
    scores_json TEXT NOT NULL DEFAULT '{}',
    screening_reasons_json TEXT NOT NULL DEFAULT '[]',
    risk_points_json TEXT NOT NULL DEFAULT '[]',
    interview_summary TEXT NOT NULL DEFAULT '',
    evidence_snippets_json TEXT NOT NULL DEFAULT '[]',
    record_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT NOT NULL DEFAULT ''
);
""" + _ACTION_LOG_SCHEMA_SQL + """

CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_review_id_nonempty
ON reviews(review_id)
WHERE review_id <> '';

CREATE INDEX IF NOT EXISTS idx_candidate_batches_jd_created
ON candidate_batches(jd_title, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_candidate_rows_batch
ON candidate_rows(batch_id);

CREATE INDEX IF NOT EXISTS idx_candidate_rows_batch_manual
ON candidate_rows(batch_id, manual_decision);

CREATE INDEX IF NOT EXISTS idx_reviews_timestamp
ON reviews(timestamp DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_users_email
ON users(email);
"""

_INIT_LOCK = threading.Lock()
_SCHEMA_READY = False
_SCHEMA_BACKEND = ""


class DBCursor:
    def __init__(self, backend: str, raw_cursor: Any):
        self._backend = backend
        self._raw_cursor = raw_cursor

    def fetchone(self) -> dict[str, Any] | None:
        row = self._raw_cursor.fetchone()
        return _normalize_row(row)

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._raw_cursor.fetchall()
        return [_normalize_row(row) for row in rows if row is not None]

    def fetchmany(self, size: int | None = None) -> list[dict[str, Any]]:
        if size is None:
            rows = self._raw_cursor.fetchmany()
        else:
            rows = self._raw_cursor.fetchmany(size)
        return [_normalize_row(row) for row in rows if row is not None]

    def close(self) -> None:
        self._raw_cursor.close()

    @property
    def rowcount(self) -> int:
        return int(getattr(self._raw_cursor, "rowcount", 0) or 0)

    @property
    def lastrowid(self) -> Any:
        return getattr(self._raw_cursor, "lastrowid", None)

    def __iter__(self):
        for row in self._raw_cursor:
            normalized = _normalize_row(row)
            if normalized is not None:
                yield normalized

    def __getattr__(self, item: str) -> Any:
        return getattr(self._raw_cursor, item)


class DBConnection:
    def __init__(self, backend: str, raw_connection: Any):
        self._backend = backend
        self._raw_connection = raw_connection

    @property
    def backend(self) -> str:
        return self._backend

    def execute(self, sql: str, parameters: Any = ()) -> DBCursor:
        translated_sql = _translate_sql_for_backend(self._backend, sql)
        translated_params = _normalize_parameters(parameters)
        cursor = self._raw_connection.cursor()
        if translated_params is None:
            cursor.execute(translated_sql)
        else:
            cursor.execute(translated_sql, translated_params)
        return DBCursor(self._backend, cursor)

    def executemany(self, sql: str, seq_of_parameters: Any) -> DBCursor:
        translated_sql = _translate_sql_for_backend(self._backend, sql)
        cursor = self._raw_connection.cursor()
        cursor.executemany(translated_sql, [_normalize_parameters(params) or () for params in seq_of_parameters])
        return DBCursor(self._backend, cursor)

    def commit(self) -> None:
        self._raw_connection.commit()

    def rollback(self) -> None:
        self._raw_connection.rollback()

    def close(self) -> None:
        self._raw_connection.close()

    def cursor(self) -> DBCursor:
        return DBCursor(self._backend, self._raw_connection.cursor())

    def __enter__(self) -> "DBConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    def __getattr__(self, item: str) -> Any:
        return getattr(self._raw_connection, item)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(payload: str | None, default: Any) -> Any:
    if not payload:
        return copy.deepcopy(default)
    try:
        return json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return copy.deepcopy(default)


def get_db_backend() -> str:
    backend = str(os.getenv("HIREMATE_DB_BACKEND", DB_BACKEND_SQLITE) or DB_BACKEND_SQLITE).strip().lower()
    if backend == DB_BACKEND_MYSQL:
        return DB_BACKEND_MYSQL
    return DB_BACKEND_SQLITE


def get_db_path() -> Path:
    raw_path = os.getenv("HIREMATE_DB_PATH", DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
    return Path(raw_path)


def get_data_dir() -> Path:
    return get_db_path().parent


def get_mysql_database_name() -> str:
    return str(os.getenv("HIREMATE_MYSQL_DATABASE") or "").strip()


def _meta_table_name_for_backend(backend: str) -> str:
    return "app_meta" if backend == DB_BACKEND_MYSQL else "meta"


def _read_sql_script(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"failed to read SQL script: {path}") from exc


def _split_sql_statements(sql_script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    length = len(sql_script)

    while i < length:
        char = sql_script[i]
        next_char = sql_script[i + 1] if i + 1 < length else ""

        if in_line_comment:
            current.append(char)
            if char == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            current.append(char)
            if char == "*" and next_char == "/":
                current.append(next_char)
                i += 2
                in_block_comment = False
            else:
                i += 1
            continue

        if in_single:
            current.append(char)
            if char == "\\" and next_char:
                current.append(next_char)
                i += 2
                continue
            if char == "'" and next_char == "'":
                current.append(next_char)
                i += 2
                continue
            if char == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            current.append(char)
            if char == "\\" and next_char:
                current.append(next_char)
                i += 2
                continue
            if char == '"':
                in_double = False
            i += 1
            continue

        if in_backtick:
            current.append(char)
            if char == "`":
                in_backtick = False
            i += 1
            continue

        if char == "-" and next_char == "-":
            current.append(char)
            current.append(next_char)
            i += 2
            in_line_comment = True
            continue
        if char == "#":
            current.append(char)
            i += 1
            in_line_comment = True
            continue
        if char == "/" and next_char == "*":
            current.append(char)
            current.append(next_char)
            i += 2
            in_block_comment = True
            continue
        if char == "'":
            current.append(char)
            in_single = True
            i += 1
            continue
        if char == '"':
            current.append(char)
            in_double = True
            i += 1
            continue
        if char == "`":
            current.append(char)
            in_backtick = True
            i += 1
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            i += 1
            continue

        current.append(char)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _strip_identifier(identifier: str) -> str:
    return identifier.strip().strip("`").strip()


def _parse_mysql_index_statement(statement: str) -> tuple[str, str] | None:
    compact = " ".join(statement.strip().split())
    match = re.search(
        r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(`?[\w]+`?)\s+ON\s+(`?[\w]+`?)\s*\(",
        compact,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    index_name = _strip_identifier(match.group(1))
    table_name = _strip_identifier(match.group(2))
    if not index_name or not table_name:
        return None
    return table_name, index_name


def _normalize_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return row


def _normalize_parameters(parameters: Any) -> Any:
    if parameters is None:
        return None
    if isinstance(parameters, tuple):
        return parameters
    if isinstance(parameters, list):
        return tuple(parameters)
    return parameters


def _translate_sql_for_backend(backend: str, sql: str) -> str:
    statement = str(sql or "")
    stripped = statement.strip()
    if backend != DB_BACKEND_MYSQL:
        return statement
    if not stripped:
        return statement
    normalized = stripped.upper()
    if normalized == "BEGIN IMMEDIATE":
        return "START TRANSACTION"
    if normalized.startswith("PRAGMA "):
        raise RuntimeError("PRAGMA is not supported when HIREMATE_DB_BACKEND=mysql")
    return _convert_qmark_placeholders(statement)


def _convert_qmark_placeholders(sql: str) -> str:
    result: list[str] = []
    i = 0
    in_single = False
    in_double = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    length = len(sql)

    while i < length:
        char = sql[i]
        next_char = sql[i + 1] if i + 1 < length else ""

        if in_line_comment:
            result.append(char)
            if char == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            result.append(char)
            if char == "*" and next_char == "/":
                result.append(next_char)
                i += 2
                in_block_comment = False
            else:
                i += 1
            continue

        if in_single:
            result.append(char)
            if char == "\\" and next_char:
                result.append(next_char)
                i += 2
                continue
            if char == "'" and next_char == "'":
                result.append(next_char)
                i += 2
                continue
            if char == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            result.append(char)
            if char == "\\" and next_char:
                result.append(next_char)
                i += 2
                continue
            if char == '"':
                in_double = False
            i += 1
            continue

        if in_backtick:
            result.append(char)
            if char == "`":
                in_backtick = False
            i += 1
            continue

        if char == "-" and next_char == "-":
            result.append(char)
            result.append(next_char)
            i += 2
            in_line_comment = True
            continue
        if char == "#":
            result.append(char)
            i += 1
            in_line_comment = True
            continue
        if char == "/" and next_char == "*":
            result.append(char)
            result.append(next_char)
            i += 2
            in_block_comment = True
            continue
        if char == "'":
            result.append(char)
            in_single = True
            i += 1
            continue
        if char == '"':
            result.append(char)
            in_double = True
            i += 1
            continue
        if char == "`":
            result.append(char)
            in_backtick = True
            i += 1
            continue
        if char == "?":
            result.append("%s")
            i += 1
            continue

        result.append(char)
        i += 1

    return "".join(result)


def _configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {str(row["name"]) for row in rows}
    if column_name in existing:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")


def _upgrade_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_ACTION_LOG_SCHEMA_SQL)

    _ensure_column(conn, "jobs", "created_by_user_id", "created_by_user_id TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jobs", "created_by_name", "created_by_name TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jobs", "created_by_email", "created_by_email TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jobs", "updated_by_user_id", "updated_by_user_id TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jobs", "updated_by_name", "updated_by_name TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jobs", "updated_by_email", "updated_by_email TEXT NOT NULL DEFAULT ''")

    _ensure_column(conn, "candidate_batches", "created_by_user_id", "created_by_user_id TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_batches", "created_by_name", "created_by_name TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_batches", "created_by_email", "created_by_email TEXT NOT NULL DEFAULT ''")

    _ensure_column(conn, "candidate_rows", "created_by_user_id", "created_by_user_id TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "created_by_name", "created_by_name TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "created_by_email", "created_by_email TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "last_operated_by_user_id", "last_operated_by_user_id TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "last_operated_by_name", "last_operated_by_name TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "last_operated_by_email", "last_operated_by_email TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "last_operated_at", "last_operated_at TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "lock_status", "lock_status TEXT NOT NULL DEFAULT 'unlocked'")
    _ensure_column(conn, "candidate_rows", "lock_owner_user_id", "lock_owner_user_id TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "lock_owner_name", "lock_owner_name TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "lock_owner_email", "lock_owner_email TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "lock_acquired_at", "lock_acquired_at TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "lock_expires_at", "lock_expires_at TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "lock_last_heartbeat_at", "lock_last_heartbeat_at TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "candidate_rows", "lock_reason", "lock_reason TEXT NOT NULL DEFAULT ''")

    _ensure_column(conn, "reviews", "reviewed_by_user_id", "reviewed_by_user_id TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "reviews", "reviewed_by_name", "reviewed_by_name TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "reviews", "reviewed_by_email", "reviewed_by_email TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "reviews", "manual_priority", "manual_priority TEXT NOT NULL DEFAULT ''")

    _ensure_column(conn, "users", "is_active", "is_active INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "users", "is_admin", "is_admin INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "users", "last_login_at", "last_login_at TEXT NOT NULL DEFAULT ''")

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_candidate_rows_batch_lock_status
        ON candidate_rows(batch_id, lock_status);

        CREATE INDEX IF NOT EXISTS idx_candidate_rows_batch_lock_owner
        ON candidate_rows(batch_id, lock_owner_user_id);
        """
    )


def _open_mysql_connection():
    if pymysql is None:
        raise RuntimeError("pymysql is required when HIREMATE_DB_BACKEND=mysql")
    if cryptography is None:
        raise RuntimeError(
            "MySQL backend requires the 'cryptography' package in the image when using "
            "MySQL 8 sha256_password or caching_sha2_password authentication. "
            "Add 'cryptography' to requirements.txt, rebuild the Docker image, and restart the container."
        )

    required_envs = {
        "host": "HIREMATE_MYSQL_HOST",
        "user": "HIREMATE_MYSQL_USER",
        "password": "HIREMATE_MYSQL_PASSWORD",
        "database": "HIREMATE_MYSQL_DATABASE",
    }
    missing = [env_name for env_name in required_envs.values() if not str(os.getenv(env_name, "") or "").strip()]
    if missing:
        raise RuntimeError(f"missing mysql env vars: {', '.join(missing)}")

    port_raw = str(os.getenv("HIREMATE_MYSQL_PORT", "3306") or "3306").strip()
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError(f"invalid HIREMATE_MYSQL_PORT: {port_raw}") from exc

    try:
        return pymysql.connect(
            host=str(os.getenv("HIREMATE_MYSQL_HOST") or "").strip(),
            port=port,
            user=str(os.getenv("HIREMATE_MYSQL_USER") or "").strip(),
            password=str(os.getenv("HIREMATE_MYSQL_PASSWORD") or ""),
            database=str(os.getenv("HIREMATE_MYSQL_DATABASE") or "").strip(),
            charset=str(os.getenv("HIREMATE_MYSQL_CHARSET", DEFAULT_MYSQL_CHARSET) or DEFAULT_MYSQL_CHARSET).strip() or DEFAULT_MYSQL_CHARSET,
            autocommit=False,
            cursorclass=DictCursor,
        )
    except RuntimeError as exc:
        message = str(exc)
        if "cryptography" in message and (
            "sha256_password" in message or "caching_sha2_password" in message
        ):
            raise RuntimeError(
                "MySQL backend failed during authentication because the container is missing the "
                "'cryptography' package required by PyMySQL for MySQL 8 sha256_password / "
                "caching_sha2_password auth. Install 'cryptography' in requirements.txt, rebuild "
                "the image, and restart the service."
            ) from exc
        raise


def open_mysql_connection() -> DBConnection:
    return DBConnection(DB_BACKEND_MYSQL, _open_mysql_connection())


def _mysql_index_exists(conn: DBConnection, table_name: str, index_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 AS present
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = ?
          AND index_name = ?
        LIMIT 1
        """,
        (table_name, index_name),
    ).fetchone()
    return row is not None


def list_mysql_tables() -> list[str]:
    raw_conn = _open_mysql_connection()
    conn = DBConnection(DB_BACKEND_MYSQL, raw_conn)
    try:
        rows = conn.execute(
            """
            SELECT table_name AS table_name
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            ORDER BY table_name ASC
            """
        ).fetchall()
        table_names: list[str] = []
        for row in rows:
            table_name = str(row.get("table_name") or row.get("TABLE_NAME") or "").strip()
            if table_name:
                table_names.append(table_name)
        return table_names
    finally:
        conn.close()


def bootstrap_mysql_schema() -> dict[str, int]:
    raw_conn = _open_mysql_connection()
    conn = DBConnection(DB_BACKEND_MYSQL, raw_conn)
    try:
        schema_script = _read_sql_script(_MYSQL_SCHEMA_PATH)
        schema_statements = _split_sql_statements(schema_script)
        applied_tables = 0
        for statement in schema_statements:
            conn.execute(statement)
            applied_tables += 1

        index_script = _read_sql_script(_MYSQL_INDEXES_PATH)
        index_statements = _split_sql_statements(index_script)
        applied_indexes = 0
        skipped_indexes = 0
        for statement in index_statements:
            parsed = _parse_mysql_index_statement(statement)
            if parsed is not None:
                table_name, index_name = parsed
                if _mysql_index_exists(conn, table_name, index_name):
                    skipped_indexes += 1
                    continue
            conn.execute(statement)
            applied_indexes += 1

        conn.commit()
        return {
            "schema_statements": applied_tables,
            "indexes_applied": applied_indexes,
            "indexes_skipped": skipped_indexes,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_mysql_schema() -> dict[str, int]:
    return bootstrap_mysql_schema()


def _build_mysql_init_error(exc: Exception) -> RuntimeError:
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()

    if (
        "cryptography" in lowered
        or "missing mysql env vars" in lowered
        or "invalid hiremate_mysql_port" in lowered
        or "pymysql is required" in lowered
    ):
        return RuntimeError(message)

    if (
        "access denied" in lowered
        or "permission denied" in lowered
        or "1044" in lowered
        or "1045" in lowered
    ):
        return RuntimeError(
            "MySQL backend initialization failed during schema bootstrap. "
            "Check HIREMATE_MYSQL_USER / HIREMATE_MYSQL_PASSWORD and make sure the account can "
            "access the target database and create tables/indexes. "
            f"Original error: {message}"
        )

    if (
        "can't connect" in lowered
        or "connection refused" in lowered
        or "timed out" in lowered
        or "unknown mysql server host" in lowered
        or "name or service not known" in lowered
        or "2003" in lowered
        or "2005" in lowered
    ):
        return RuntimeError(
            "MySQL backend initialization failed because the application could not connect to the "
            "MySQL server. Check that MySQL is running and that HIREMATE_MYSQL_HOST / "
            "HIREMATE_MYSQL_PORT are reachable from the app container. "
            f"Original error: {message}"
        )

    return RuntimeError(
        "MySQL backend initialization failed during schema bootstrap. Check MySQL connectivity, "
        "credentials, and whether the application user can bootstrap schema objects successfully. "
        f"Original error: {message}"
    )


def init_db() -> None:
    global _SCHEMA_READY, _SCHEMA_BACKEND
    backend = get_db_backend()
    if backend == DB_BACKEND_MYSQL:
        with _INIT_LOCK:
            if _SCHEMA_READY and _SCHEMA_BACKEND == backend:
                return
            try:
                bootstrap_mysql_schema()
            except Exception as exc:
                raise _build_mysql_init_error(exc) from exc
            _SCHEMA_READY = True
            _SCHEMA_BACKEND = backend
        return

    db_path = get_db_path()
    if _SCHEMA_READY and _SCHEMA_BACKEND == backend and db_path.exists():
        return

    with _INIT_LOCK:
        db_path = get_db_path()
        if _SCHEMA_READY and _SCHEMA_BACKEND == backend and db_path.exists():
            return

        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            _configure_sqlite_connection(conn)
            conn.executescript(_SCHEMA_SQL)
            _upgrade_schema(conn)
            conn.commit()
        finally:
            conn.close()

        _SCHEMA_READY = True
        _SCHEMA_BACKEND = backend


def get_connection() -> DBConnection:
    backend = get_db_backend()
    init_db()
    if backend == DB_BACKEND_MYSQL:
        return DBConnection(backend, _open_mysql_connection())

    raw_conn = sqlite3.connect(get_db_path(), timeout=30)
    _configure_sqlite_connection(raw_conn)
    return DBConnection(backend, raw_conn)


def get_meta(conn: DBConnection, key: str) -> str | None:
    table_name = _meta_table_name_for_backend(getattr(conn, "backend", get_db_backend()))
    key_column = "key_name" if table_name == "app_meta" else "key"
    value_column = "value_text" if table_name == "app_meta" else "value"
    row = conn.execute(
        f"SELECT {value_column} AS meta_value FROM {table_name} WHERE {key_column} = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return str(row["meta_value"])


def set_meta(conn: DBConnection, key: str, value: str) -> None:
    table_name = _meta_table_name_for_backend(getattr(conn, "backend", get_db_backend()))
    key_column = "key_name" if table_name == "app_meta" else "key"
    value_column = "value_text" if table_name == "app_meta" else "value"
    exists = conn.execute(
        f"SELECT 1 AS present FROM {table_name} WHERE {key_column} = ?",
        (key,),
    ).fetchone()
    if exists is None:
        conn.execute(
            f"INSERT INTO {table_name}({key_column}, {value_column}) VALUES(?, ?)",
            (key, value),
        )
        return
    conn.execute(
        f"UPDATE {table_name} SET {value_column} = ? WHERE {key_column} = ?",
        (value, key),
    )
