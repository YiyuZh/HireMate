"""SQLite helpers for HireMate persistence."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .role_profiles import build_default_scoring_config

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "hiremate.db"
JD_JSON_PATH = DATA_DIR / "jd_store.json"
CANDIDATE_JSON_PATH = DATA_DIR / "candidate_pool_store.json"
REVIEW_JSON_PATH = DATA_DIR / "review_history.json"

_DEFAULT_PROFILE_NAME = "AI产品经理 / 大模型产品经理"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    title TEXT PRIMARY KEY,
    jd_text TEXT NOT NULL,
    openings INTEGER NOT NULL DEFAULT 0,
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
    manual_decision TEXT NOT NULL DEFAULT '',
    manual_note TEXT NOT NULL DEFAULT '',
    scores_json TEXT NOT NULL DEFAULT '{}',
    screening_reasons_json TEXT NOT NULL DEFAULT '[]',
    risk_points_json TEXT NOT NULL DEFAULT '[]',
    interview_summary TEXT NOT NULL DEFAULT '',
    evidence_snippets_json TEXT NOT NULL DEFAULT '[]',
    record_json TEXT NOT NULL DEFAULT '{}'
);

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
"""

_INIT_LOCK = threading.Lock()
_SCHEMA_READY = False
_MIGRATION_READY = False


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


def _default_scoring_config() -> dict[str, Any]:
    return build_default_scoring_config(_DEFAULT_PROFILE_NAME)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")


def ensure_database() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY and DB_PATH.exists():
        return

    with _INIT_LOCK:
        if _SCHEMA_READY and DB_PATH.exists():
            return

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            _configure_connection(conn)
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

        _SCHEMA_READY = True


def get_connection() -> sqlite3.Connection:
    ensure_database()
    ensure_legacy_migration()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    _configure_connection(conn)
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row["value"])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return copy.deepcopy(default)
    return json_loads(content, default)


def _table_has_rows(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table_name} LIMIT 1").fetchone()
    return row is not None


def _job_record_from_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        scoring_config = raw.get("scoring_config")
        if not isinstance(scoring_config, dict):
            scoring_config = _default_scoring_config()
        updated_at = str(raw.get("updated_at") or "").strip() or now_str()
        return {
            "text": str(raw.get("text") or "").strip(),
            "updated_at": updated_at,
            "openings": max(0, _safe_int(raw.get("openings"), 0)),
            "scoring_config": scoring_config,
        }

    return {
        "text": str(raw or "").strip(),
        "updated_at": now_str(),
        "openings": 0,
        "scoring_config": _default_scoring_config(),
    }


def _candidate_batch_from_json(item: dict[str, Any]) -> dict[str, Any]:
    batch_id = str(item.get("batch_id") or f"batch-{uuid4().hex}").strip()
    jd_title = str(item.get("jd_title") or "未命名岗位").strip() or "未命名岗位"
    created_at = str(item.get("created_at") or now_str()).strip() or now_str()

    candidates = item.get("candidates")
    if not isinstance(candidates, list):
        candidates = []

    normalized_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or f"cand-{uuid4().hex[:8]}").strip()
        if not candidate_id:
            candidate_id = f"cand-{uuid4().hex[:8]}"
        row_payload = candidate.get("row") if isinstance(candidate.get("row"), dict) else {}
        detail_payload = candidate.get("detail") if isinstance(candidate.get("detail"), dict) else {}
        normalized_candidates.append(
            {
                "candidate_id": candidate_id,
                "screening_result": str(candidate.get("screening_result") or row_payload.get("初筛结论") or ""),
                "risk_level": str(candidate.get("risk_level") or row_payload.get("风险等级") or "unknown"),
                "scores": candidate.get("scores") if isinstance(candidate.get("scores"), dict) else (detail_payload.get("score_details") or {}),
                "review_summary": str(candidate.get("review_summary") or row_payload.get("审核摘要") or ""),
                "extract_info": candidate.get("extract_info") if isinstance(candidate.get("extract_info"), dict) else (detail_payload.get("extract_info") or {}),
                "manual_decision": str(candidate.get("manual_decision") or row_payload.get("人工最终结论") or ""),
                "manual_note": str(candidate.get("manual_note") or ""),
                "manual_priority": str(candidate.get("manual_priority") or row_payload.get("处理优先级") or detail_payload.get("manual_priority") or "普通"),
                "updated_at": str(candidate.get("updated_at") or created_at or now_str()),
                "row": row_payload,
                "detail": detail_payload,
            }
        )

    return {
        "batch_id": batch_id,
        "jd_title": jd_title,
        "created_at": created_at,
        "total_resumes": max(0, _safe_int(item.get("total_resumes"), len(normalized_candidates))),
        "pass_count": max(0, _safe_int(item.get("pass_count"), sum(1 for candidate in normalized_candidates if candidate.get("screening_result") == "推荐进入下一轮"))),
        "review_count": max(0, _safe_int(item.get("review_count"), sum(1 for candidate in normalized_candidates if candidate.get("screening_result") == "建议人工复核"))),
        "reject_count": max(0, _safe_int(item.get("reject_count"), sum(1 for candidate in normalized_candidates if candidate.get("screening_result") == "暂不推荐"))),
        "candidates": normalized_candidates,
    }


def _review_record_from_json(record: dict[str, Any]) -> dict[str, Any]:
    timestamp = str(record.get("timestamp") or now_str()).strip() or now_str()
    auto_screening_result = str(record.get("auto_screening_result") or record.get("screening_result") or "")
    auto_risk_level = str(record.get("auto_risk_level") or record.get("risk_level") or "unknown")

    screening_reasons = record.get("screening_reasons")
    if not isinstance(screening_reasons, list):
        screening_reasons = []

    risk_points = record.get("risk_points")
    if not isinstance(risk_points, list):
        risk_points = []

    evidence_snippets = record.get("evidence_snippets")
    if not isinstance(evidence_snippets, list):
        evidence_snippets = []

    scores = record.get("scores")
    if not isinstance(scores, dict):
        scores = {}

    return {
        "review_id": str(record.get("review_id") or "").strip(),
        "timestamp": timestamp,
        "updated_at": str(record.get("updated_at") or timestamp).strip() or timestamp,
        "jd_title": str(record.get("jd_title") or ""),
        "resume_name": str(record.get("resume_name") or ""),
        "resume_file": str(record.get("resume_file") or ""),
        "scores": scores,
        "auto_screening_result": auto_screening_result,
        "auto_risk_level": auto_risk_level,
        "manual_decision": str(record.get("manual_decision") or ""),
        "manual_note": str(record.get("manual_note") or ""),
        "screening_result": auto_screening_result,
        "risk_level": auto_risk_level,
        "screening_reasons": screening_reasons,
        "risk_points": risk_points,
        "interview_summary": str(record.get("interview_summary") or ""),
        "evidence_snippets": evidence_snippets,
    }


def _should_skip_migration(conn: sqlite3.Connection, meta_key: str, table_name: str) -> bool:
    if get_meta(conn, meta_key) == "1":
        return True
    if _table_has_rows(conn, table_name):
        set_meta(conn, meta_key, "1")
        return True
    return False


def _migrate_jobs_if_needed(conn: sqlite3.Connection) -> None:
    meta_key = "migration.jobs.json.v1"
    if _should_skip_migration(conn, meta_key, "jobs"):
        return

    data = _read_json_file(JD_JSON_PATH, {})
    if not isinstance(data, dict):
        set_meta(conn, meta_key, "1")
        return

    for title, raw in data.items():
        clean_title = str(title or "").strip()
        if not clean_title:
            continue
        record = _job_record_from_json(raw)
        updated_at = str(record.get("updated_at") or now_str())
        conn.execute(
            """
            INSERT INTO jobs(title, jd_text, openings, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(title) DO UPDATE SET
                jd_text = excluded.jd_text,
                openings = excluded.openings,
                updated_at = excluded.updated_at
            """,
            (
                clean_title,
                str(record.get("text") or ""),
                max(0, _safe_int(record.get("openings"), 0)),
                updated_at,
                updated_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO job_scoring_configs(job_title, scoring_config_json, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(job_title) DO UPDATE SET
                scoring_config_json = excluded.scoring_config_json,
                updated_at = excluded.updated_at
            """,
            (clean_title, json_dumps(record.get("scoring_config") or _default_scoring_config()), updated_at),
        )

    set_meta(conn, meta_key, "1")


def _migrate_candidate_batches_if_needed(conn: sqlite3.Connection) -> None:
    meta_key = "migration.candidate_batches.json.v1"
    if _should_skip_migration(conn, meta_key, "candidate_batches"):
        return

    data = _read_json_file(CANDIDATE_JSON_PATH, [])
    if not isinstance(data, list):
        set_meta(conn, meta_key, "1")
        return

    for item in data:
        if not isinstance(item, dict):
            continue
        batch = _candidate_batch_from_json(item)
        conn.execute(
            """
            INSERT INTO candidate_batches(
                batch_id, jd_title, created_at, total_resumes, candidate_count, pass_count, review_count, reject_count
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(batch_id) DO UPDATE SET
                jd_title = excluded.jd_title,
                created_at = excluded.created_at,
                total_resumes = excluded.total_resumes,
                candidate_count = excluded.candidate_count,
                pass_count = excluded.pass_count,
                review_count = excluded.review_count,
                reject_count = excluded.reject_count
            """,
            (
                batch["batch_id"],
                batch["jd_title"],
                batch["created_at"],
                batch["total_resumes"],
                len(batch["candidates"]),
                batch["pass_count"],
                batch["review_count"],
                batch["reject_count"],
            ),
        )
        conn.execute("DELETE FROM candidate_rows WHERE batch_id = ?", (batch["batch_id"],))
        for candidate in batch["candidates"]:
            row_payload = candidate.get("row") if isinstance(candidate.get("row"), dict) else {}
            detail_payload = candidate.get("detail") if isinstance(candidate.get("detail"), dict) else {}
            extract_info = candidate.get("extract_info") if isinstance(candidate.get("extract_info"), dict) else {}
            scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
            conn.execute(
                """
                INSERT INTO candidate_rows(
                    batch_id, candidate_id, candidate_name, source_name, parse_status, screening_result,
                    risk_level, candidate_pool, manual_decision, manual_note, manual_priority, review_summary,
                    scores_json, extract_info_json, row_json, detail_json, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["batch_id"],
                    str(candidate.get("candidate_id") or f"cand-{uuid4().hex[:8]}"),
                    str(row_payload.get("姓名") or detail_payload.get("parsed_resume", {}).get("name") or ""),
                    str(row_payload.get("文件名") or extract_info.get("file_name") or ""),
                    str(row_payload.get("解析状态") or extract_info.get("parse_status") or ""),
                    str(candidate.get("screening_result") or ""),
                    str(candidate.get("risk_level") or "unknown"),
                    str(row_payload.get("候选池") or ""),
                    str(candidate.get("manual_decision") or ""),
                    str(candidate.get("manual_note") or ""),
                    str(candidate.get("manual_priority") or "普通"),
                    str(candidate.get("review_summary") or ""),
                    json_dumps(scores),
                    json_dumps(extract_info),
                    json_dumps(row_payload),
                    json_dumps(detail_payload),
                    batch["created_at"],
                    str(candidate.get("updated_at") or batch["created_at"]),
                ),
            )

    set_meta(conn, meta_key, "1")


def _migrate_reviews_if_needed(conn: sqlite3.Connection) -> None:
    meta_key = "migration.reviews.json.v1"
    if _should_skip_migration(conn, meta_key, "reviews"):
        return

    data = _read_json_file(REVIEW_JSON_PATH, [])
    if not isinstance(data, list):
        set_meta(conn, meta_key, "1")
        return

    for item in data:
        if not isinstance(item, dict):
            continue
        record = _review_record_from_json(item)
        conn.execute(
            """
            INSERT OR REPLACE INTO reviews(
                review_id, timestamp, updated_at, jd_title, resume_name, resume_file,
                auto_screening_result, auto_risk_level, manual_decision, manual_note,
                scores_json, screening_reasons_json, risk_points_json, interview_summary,
                evidence_snippets_json, record_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["review_id"],
                record["timestamp"],
                record["updated_at"],
                record["jd_title"],
                record["resume_name"],
                record["resume_file"],
                record["auto_screening_result"],
                record["auto_risk_level"],
                record["manual_decision"],
                record["manual_note"],
                json_dumps(record.get("scores") or {}),
                json_dumps(record.get("screening_reasons") or []),
                json_dumps(record.get("risk_points") or []),
                record["interview_summary"],
                json_dumps(record.get("evidence_snippets") or []),
                json_dumps(record),
            ),
        )

    set_meta(conn, meta_key, "1")


def ensure_legacy_migration() -> None:
    global _MIGRATION_READY
    if _MIGRATION_READY and DB_PATH.exists():
        return

    with _INIT_LOCK:
        if _MIGRATION_READY and DB_PATH.exists():
            return

        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            _configure_connection(conn)
            conn.executescript(_SCHEMA_SQL)
            _migrate_jobs_if_needed(conn)
            _migrate_candidate_batches_if_needed(conn)
            _migrate_reviews_if_needed(conn)
            conn.commit()
        finally:
            conn.close()

        _MIGRATION_READY = True
