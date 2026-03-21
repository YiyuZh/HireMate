"""Migrate HireMate data from SQLite to MySQL."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.db import DEFAULT_DB_PATH, bootstrap_mysql_schema, get_connection, get_mysql_database_name


def _source_sqlite_path() -> Path:
    raw_path = str(os.getenv("HIREMATE_DB_PATH", DEFAULT_DB_PATH) or DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
    return Path(raw_path)


def _connect_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 AS present FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _sqlite_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _sqlite_has_table(conn, table_name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _read_sqlite_rows(
    conn: sqlite3.Connection,
    table_name: str,
    *,
    requested_columns: list[str],
    order_by: str = "",
) -> list[dict[str, Any]]:
    available_columns = _sqlite_columns(conn, table_name)
    if not available_columns:
        return []
    selected_columns = [column for column in requested_columns if column in available_columns]
    if not selected_columns:
        return []
    sql = f"SELECT {', '.join(selected_columns)} FROM {table_name}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    rows = conn.execute(sql).fetchall()
    return [{column: row[column] for column in selected_columns} for row in rows]


def _safe_text(value: Any, default: str = "") -> str:
    return str(value or default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _json_loads(payload: Any, default: Any) -> Any:
    if not payload:
        return default
    try:
        return json.loads(str(payload))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _normalize_compare_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return str(value)


def _rows_match(existing: dict[str, Any] | None, incoming: dict[str, Any], columns: list[str]) -> bool:
    if existing is None:
        return False
    for column in columns:
        if _normalize_compare_value(existing.get(column)) != _normalize_compare_value(incoming.get(column)):
            return False
    return True


def _build_insert_sql(table_name: str, columns: list[str]) -> str:
    column_sql = ", ".join(columns)
    placeholder_sql = ", ".join("?" for _ in columns)
    return f"INSERT INTO {table_name}({column_sql}) VALUES({placeholder_sql})"


def _build_select_existing_sql(table_name: str, select_columns: list[str], key_columns: list[str]) -> str:
    select_sql = ", ".join(select_columns)
    where_sql = " AND ".join(f"{column} = ?" for column in key_columns)
    return f"SELECT {select_sql} FROM {table_name} WHERE {where_sql} LIMIT 1"


def _build_update_sql(table_name: str, update_columns: list[str], key_columns: list[str]) -> str:
    set_sql = ", ".join(f"{column} = ?" for column in update_columns)
    where_sql = " AND ".join(f"{column} = ?" for column in key_columns)
    return f"UPDATE {table_name} SET {set_sql} WHERE {where_sql}"


def _upsert_rows(
    target_conn,
    *,
    table_name: str,
    rows: list[dict[str, Any]],
    key_columns: list[str],
    all_columns: list[str],
    insert_only_columns: list[str] | None = None,
) -> dict[str, int]:
    insert_only = set(insert_only_columns or [])
    compare_columns = [column for column in all_columns if column not in insert_only]
    update_columns = [column for column in compare_columns if column not in key_columns]
    select_existing_sql = _build_select_existing_sql(table_name, compare_columns, key_columns)
    insert_sql = _build_insert_sql(table_name, all_columns)
    update_sql = _build_update_sql(table_name, update_columns, key_columns) if update_columns else ""

    summary = {"read": len(rows), "inserted": 0, "updated": 0, "skipped": 0}
    for row in rows:
        key_values = tuple(row.get(column) for column in key_columns)
        existing = target_conn.execute(select_existing_sql, key_values).fetchone()
        if existing is None:
            insert_values = tuple(row.get(column) for column in all_columns)
            target_conn.execute(insert_sql, insert_values)
            summary["inserted"] += 1
            continue
        if _rows_match(existing, row, compare_columns):
            summary["skipped"] += 1
            continue
        if update_columns:
            update_values = tuple(row.get(column) for column in update_columns) + key_values
            target_conn.execute(update_sql, update_values)
        summary["updated"] += 1
    return summary


def _load_meta_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _read_sqlite_rows(conn, "meta", requested_columns=["key", "value"], order_by="key ASC")
    return [{"key_name": _safe_text(row.get("key")), "value_text": _safe_text(row.get("value"))} for row in rows]


def _load_user_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _read_sqlite_rows(
        conn,
        "users",
        requested_columns=["user_id", "email", "name", "password_hash", "is_active", "is_admin", "created_at", "updated_at", "last_login_at"],
        order_by="created_at ASC, email ASC",
    )
    result = []
    for row in rows:
        result.append(
            {
                "user_id": _safe_text(row.get("user_id")),
                "email": _safe_text(row.get("email")).lower(),
                "name": _safe_text(row.get("name")),
                "password_hash": _safe_text(row.get("password_hash")),
                "is_active": _safe_int(row.get("is_active"), 1),
                "is_admin": _safe_int(row.get("is_admin"), 0),
                "created_at": _safe_text(row.get("created_at")),
                "updated_at": _safe_text(row.get("updated_at")),
                "last_login_at": _safe_text(row.get("last_login_at")),
            }
        )
    return result


def _load_job_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _read_sqlite_rows(
        conn,
        "jobs",
        requested_columns=[
            "title",
            "jd_text",
            "openings",
            "created_by_user_id",
            "created_by_name",
            "created_by_email",
            "updated_by_user_id",
            "updated_by_name",
            "updated_by_email",
            "created_at",
            "updated_at",
        ],
        order_by="title ASC",
    )
    result = []
    for row in rows:
        result.append(
            {
                "title": _safe_text(row.get("title")),
                "jd_text": _safe_text(row.get("jd_text")),
                "openings": _safe_int(row.get("openings"), 0),
                "created_by_user_id": _safe_text(row.get("created_by_user_id")),
                "created_by_name": _safe_text(row.get("created_by_name")),
                "created_by_email": _safe_text(row.get("created_by_email")),
                "updated_by_user_id": _safe_text(row.get("updated_by_user_id")),
                "updated_by_name": _safe_text(row.get("updated_by_name")),
                "updated_by_email": _safe_text(row.get("updated_by_email")),
                "created_at": _safe_text(row.get("created_at")),
                "updated_at": _safe_text(row.get("updated_at")),
            }
        )
    return result


def _load_job_scoring_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _read_sqlite_rows(
        conn,
        "job_scoring_configs",
        requested_columns=["job_title", "scoring_config_json", "updated_at"],
        order_by="job_title ASC",
    )
    return [
        {
            "job_title": _safe_text(row.get("job_title")),
            "scoring_config_json": _safe_text(row.get("scoring_config_json"), "{}"),
            "updated_at": _safe_text(row.get("updated_at")),
        }
        for row in rows
    ]


def _load_review_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _read_sqlite_rows(
        conn,
        "reviews",
        requested_columns=[
            "id",
            "review_id",
            "timestamp",
            "updated_at",
            "jd_title",
            "resume_name",
            "resume_file",
            "auto_screening_result",
            "auto_risk_level",
            "reviewed_by_user_id",
            "reviewed_by_name",
            "reviewed_by_email",
            "manual_decision",
            "manual_note",
            "manual_priority",
            "scores_json",
            "screening_reasons_json",
            "risk_points_json",
            "interview_summary",
            "evidence_snippets_json",
            "record_json",
        ],
        order_by="id ASC",
    )
    result = []
    for row in rows:
        record_json = row.get("record_json")
        record_payload = _json_loads(record_json, {})
        manual_priority = _safe_text(row.get("manual_priority"))
        if not manual_priority and isinstance(record_payload, dict):
            manual_priority = _safe_text(record_payload.get("manual_priority"))
        review_id = _safe_text(row.get("review_id"))
        result.append(
            {
                "id": _safe_int(row.get("id"), 0),
                "review_id": review_id or None,
                "timestamp": _safe_text(row.get("timestamp")),
                "updated_at": _safe_text(row.get("updated_at")),
                "jd_title": _safe_text(row.get("jd_title")),
                "resume_name": _safe_text(row.get("resume_name")),
                "resume_file": _safe_text(row.get("resume_file")),
                "auto_screening_result": _safe_text(row.get("auto_screening_result")),
                "auto_risk_level": _safe_text(row.get("auto_risk_level"), "unknown"),
                "reviewed_by_user_id": _safe_text(row.get("reviewed_by_user_id")),
                "reviewed_by_name": _safe_text(row.get("reviewed_by_name")),
                "reviewed_by_email": _safe_text(row.get("reviewed_by_email")),
                "manual_decision": _safe_text(row.get("manual_decision")),
                "manual_note": _safe_text(row.get("manual_note")),
                "manual_priority": manual_priority,
                "scores_json": _safe_text(row.get("scores_json"), "{}"),
                "screening_reasons_json": _safe_text(row.get("screening_reasons_json"), "[]"),
                "risk_points_json": _safe_text(row.get("risk_points_json"), "[]"),
                "interview_summary": _safe_text(row.get("interview_summary")),
                "evidence_snippets_json": _safe_text(row.get("evidence_snippets_json"), "[]"),
                "record_json": _safe_text(record_json, "{}"),
            }
        )
    return result


def _load_candidate_batch_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _read_sqlite_rows(
        conn,
        "candidate_batches",
        requested_columns=[
            "batch_id",
            "jd_title",
            "created_by_user_id",
            "created_by_name",
            "created_by_email",
            "created_at",
            "total_resumes",
            "candidate_count",
            "pass_count",
            "review_count",
            "reject_count",
        ],
        order_by="created_at ASC, batch_id ASC",
    )
    result = []
    for row in rows:
        result.append(
            {
                "batch_id": _safe_text(row.get("batch_id")),
                "jd_title": _safe_text(row.get("jd_title")),
                "created_by_user_id": _safe_text(row.get("created_by_user_id")),
                "created_by_name": _safe_text(row.get("created_by_name")),
                "created_by_email": _safe_text(row.get("created_by_email")),
                "created_at": _safe_text(row.get("created_at")),
                "total_resumes": _safe_int(row.get("total_resumes"), 0),
                "candidate_count": _safe_int(row.get("candidate_count"), 0),
                "pass_count": _safe_int(row.get("pass_count"), 0),
                "review_count": _safe_int(row.get("review_count"), 0),
                "reject_count": _safe_int(row.get("reject_count"), 0),
            }
        )
    return result


def _load_candidate_row_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _read_sqlite_rows(
        conn,
        "candidate_rows",
        requested_columns=[
            "id",
            "batch_id",
            "candidate_id",
            "candidate_name",
            "source_name",
            "parse_status",
            "screening_result",
            "risk_level",
            "candidate_pool",
            "manual_decision",
            "manual_note",
            "manual_priority",
            "review_summary",
            "scores_json",
            "extract_info_json",
            "row_json",
            "detail_json",
            "created_by_user_id",
            "created_by_name",
            "created_by_email",
            "last_operated_by_user_id",
            "last_operated_by_name",
            "last_operated_by_email",
            "last_operated_at",
            "lock_status",
            "lock_owner_user_id",
            "lock_owner_name",
            "lock_owner_email",
            "lock_acquired_at",
            "lock_expires_at",
            "lock_last_heartbeat_at",
            "lock_reason",
            "created_at",
            "updated_at",
        ],
        order_by="id ASC",
    )
    result = []
    for row in rows:
        result.append(
            {
                "id": _safe_int(row.get("id"), 0),
                "batch_id": _safe_text(row.get("batch_id")),
                "candidate_id": _safe_text(row.get("candidate_id")),
                "candidate_name": _safe_text(row.get("candidate_name")),
                "source_name": _safe_text(row.get("source_name")),
                "parse_status": _safe_text(row.get("parse_status")),
                "screening_result": _safe_text(row.get("screening_result")),
                "risk_level": _safe_text(row.get("risk_level"), "unknown"),
                "candidate_pool": _safe_text(row.get("candidate_pool")),
                "manual_decision": _safe_text(row.get("manual_decision")),
                "manual_note": _safe_text(row.get("manual_note")),
                "manual_priority": _safe_text(row.get("manual_priority")),
                "review_summary": _safe_text(row.get("review_summary")),
                "scores_json": _safe_text(row.get("scores_json"), "{}"),
                "extract_info_json": _safe_text(row.get("extract_info_json"), "{}"),
                "row_json": _safe_text(row.get("row_json"), "{}"),
                "detail_json": _safe_text(row.get("detail_json"), "{}"),
                "created_by_user_id": _safe_text(row.get("created_by_user_id")),
                "created_by_name": _safe_text(row.get("created_by_name")),
                "created_by_email": _safe_text(row.get("created_by_email")),
                "last_operated_by_user_id": _safe_text(row.get("last_operated_by_user_id")),
                "last_operated_by_name": _safe_text(row.get("last_operated_by_name")),
                "last_operated_by_email": _safe_text(row.get("last_operated_by_email")),
                "last_operated_at": _safe_text(row.get("last_operated_at")),
                "lock_status": _safe_text(row.get("lock_status"), "unlocked"),
                "lock_owner_user_id": _safe_text(row.get("lock_owner_user_id")),
                "lock_owner_name": _safe_text(row.get("lock_owner_name")),
                "lock_owner_email": _safe_text(row.get("lock_owner_email")),
                "lock_acquired_at": _safe_text(row.get("lock_acquired_at")),
                "lock_expires_at": _safe_text(row.get("lock_expires_at")),
                "lock_last_heartbeat_at": _safe_text(row.get("lock_last_heartbeat_at")),
                "lock_reason": _safe_text(row.get("lock_reason")),
                "created_at": _safe_text(row.get("created_at")),
                "updated_at": _safe_text(row.get("updated_at")),
            }
        )
    return result


def _load_candidate_action_log_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _read_sqlite_rows(
        conn,
        "candidate_action_logs",
        requested_columns=[
            "id",
            "action_id",
            "batch_id",
            "candidate_id",
            "review_id",
            "jd_title",
            "action_type",
            "operator_user_id",
            "operator_name",
            "operator_email",
            "before_json",
            "after_json",
            "extra_json",
            "created_at",
        ],
        order_by="id ASC",
    )
    result = []
    for row in rows:
        result.append(
            {
                "id": _safe_int(row.get("id"), 0),
                "action_id": _safe_text(row.get("action_id")),
                "batch_id": _safe_text(row.get("batch_id")),
                "candidate_id": _safe_text(row.get("candidate_id")),
                "review_id": _safe_text(row.get("review_id")),
                "jd_title": _safe_text(row.get("jd_title")),
                "action_type": _safe_text(row.get("action_type")),
                "operator_user_id": _safe_text(row.get("operator_user_id")),
                "operator_name": _safe_text(row.get("operator_name")),
                "operator_email": _safe_text(row.get("operator_email")),
                "before_json": _safe_text(row.get("before_json"), "{}"),
                "after_json": _safe_text(row.get("after_json"), "{}"),
                "extra_json": _safe_text(row.get("extra_json"), "{}"),
                "created_at": _safe_text(row.get("created_at")),
            }
        )
    return result


def main() -> int:
    source_path = _source_sqlite_path()
    if not source_path.exists():
        print(f"SQLite source not found: {source_path}", file=sys.stderr)
        return 1

    os.environ["HIREMATE_DB_BACKEND"] = "mysql"
    try:
        bootstrap_mysql_schema()
    except Exception as exc:  # noqa: BLE001
        print(f"MySQL bootstrap failed before migration: {exc}", file=sys.stderr)
        return 1

    sqlite_conn = _connect_sqlite(source_path)
    target_conn = get_connection()
    started_at = time.perf_counter()
    summaries: list[tuple[str, dict[str, int]]] = []
    try:
        table_jobs = [
            (
                "app_meta",
                _load_meta_rows(sqlite_conn),
                ["key_name"],
                ["key_name", "value_text"],
                [],
            ),
            (
                "users",
                _load_user_rows(sqlite_conn),
                ["user_id"],
                ["user_id", "email", "name", "password_hash", "is_active", "is_admin", "created_at", "updated_at", "last_login_at"],
                [],
            ),
            (
                "jobs",
                _load_job_rows(sqlite_conn),
                ["title"],
                [
                    "title",
                    "jd_text",
                    "openings",
                    "created_by_user_id",
                    "created_by_name",
                    "created_by_email",
                    "updated_by_user_id",
                    "updated_by_name",
                    "updated_by_email",
                    "created_at",
                    "updated_at",
                ],
                [],
            ),
            (
                "job_scoring_configs",
                _load_job_scoring_rows(sqlite_conn),
                ["job_title"],
                ["job_title", "scoring_config_json", "updated_at"],
                [],
            ),
            (
                "reviews",
                _load_review_rows(sqlite_conn),
                ["id"],
                [
                    "id",
                    "review_id",
                    "timestamp",
                    "updated_at",
                    "jd_title",
                    "resume_name",
                    "resume_file",
                    "auto_screening_result",
                    "auto_risk_level",
                    "reviewed_by_user_id",
                    "reviewed_by_name",
                    "reviewed_by_email",
                    "manual_decision",
                    "manual_note",
                    "manual_priority",
                    "scores_json",
                    "screening_reasons_json",
                    "risk_points_json",
                    "interview_summary",
                    "evidence_snippets_json",
                    "record_json",
                ],
                ["id"],
            ),
            (
                "candidate_batches",
                _load_candidate_batch_rows(sqlite_conn),
                ["batch_id"],
                [
                    "batch_id",
                    "jd_title",
                    "created_by_user_id",
                    "created_by_name",
                    "created_by_email",
                    "created_at",
                    "total_resumes",
                    "candidate_count",
                    "pass_count",
                    "review_count",
                    "reject_count",
                ],
                [],
            ),
            (
                "candidate_rows",
                _load_candidate_row_rows(sqlite_conn),
                ["batch_id", "candidate_id"],
                [
                    "id",
                    "batch_id",
                    "candidate_id",
                    "candidate_name",
                    "source_name",
                    "parse_status",
                    "screening_result",
                    "risk_level",
                    "candidate_pool",
                    "manual_decision",
                    "manual_note",
                    "manual_priority",
                    "review_summary",
                    "scores_json",
                    "extract_info_json",
                    "row_json",
                    "detail_json",
                    "created_by_user_id",
                    "created_by_name",
                    "created_by_email",
                    "last_operated_by_user_id",
                    "last_operated_by_name",
                    "last_operated_by_email",
                    "last_operated_at",
                    "lock_status",
                    "lock_owner_user_id",
                    "lock_owner_name",
                    "lock_owner_email",
                    "lock_acquired_at",
                    "lock_expires_at",
                    "lock_last_heartbeat_at",
                    "lock_reason",
                    "created_at",
                    "updated_at",
                ],
                ["id"],
            ),
            (
                "candidate_action_logs",
                _load_candidate_action_log_rows(sqlite_conn),
                ["action_id"],
                [
                    "id",
                    "action_id",
                    "batch_id",
                    "candidate_id",
                    "review_id",
                    "jd_title",
                    "action_type",
                    "operator_user_id",
                    "operator_name",
                    "operator_email",
                    "before_json",
                    "after_json",
                    "extra_json",
                    "created_at",
                ],
                ["id"],
            ),
        ]

        with target_conn:
            for table_name, rows, key_columns, all_columns, insert_only in table_jobs:
                summary = _upsert_rows(
                    target_conn,
                    table_name=table_name,
                    rows=rows,
                    key_columns=key_columns,
                    all_columns=all_columns,
                    insert_only_columns=insert_only,
                )
                summaries.append((table_name, summary))
    except Exception as exc:  # noqa: BLE001
        target_conn.rollback()
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        sqlite_conn.close()
        target_conn.close()

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    print("SQLite -> MySQL migration completed.")
    print(f"source_sqlite={source_path}")
    print(f"target_mysql_db={get_mysql_database_name() or '(unknown)'}")
    total_read = total_inserted = total_updated = total_skipped = 0
    for table_name, summary in summaries:
        total_read += summary["read"]
        total_inserted += summary["inserted"]
        total_updated += summary["updated"]
        total_skipped += summary["skipped"]
        print(
            f"{table_name}: read={summary['read']} inserted={summary['inserted']} "
            f"updated={summary['updated']} skipped={summary['skipped']}"
        )
    print(
        f"total: read={total_read} inserted={total_inserted} "
        f"updated={total_updated} skipped={total_skipped} elapsed_ms={elapsed_ms}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
