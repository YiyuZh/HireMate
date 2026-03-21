"""JD store backed by SQLite."""

from __future__ import annotations

from typing import Any

from .db import get_connection, json_dumps, json_loads, now_str
from .role_profiles import build_default_scoring_config

_DEFAULT_PROFILE_NAME = "AI产品经理 / 大模型产品经理"


def _default_scoring_config() -> dict[str, Any]:
    return build_default_scoring_config(_DEFAULT_PROFILE_NAME)


def _safe_openings(value: Any, fallback: int = 0) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return max(0, int(fallback or 0))


def _decode_scoring_config(payload: str | None) -> dict[str, Any]:
    data = json_loads(payload, _default_scoring_config())
    return data if isinstance(data, dict) else _default_scoring_config()


def _prefer_existing(existing_value: Any, new_value: str | None = None) -> str:
    current = str(existing_value or "").strip()
    if current:
        return current
    return str(new_value or "").strip()


def save_jd(
    title: str,
    jd_text: str,
    openings: int = 0,
    created_by_name: str = "",
    created_by_email: str = "",
    created_by_user_id: str = "",
    updated_by_user_id: str = "",
    updated_by_name: str = "",
    updated_by_email: str = "",
) -> None:
    clean_title = (title or "").strip()
    clean_text = (jd_text or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空。")
    if not clean_text:
        raise ValueError("JD 内容不能为空。")

    conn = get_connection()
    try:
        existing = conn.execute(
            """
            SELECT jobs.created_at, jobs.openings,
                   jobs.created_by_user_id, jobs.created_by_name, jobs.created_by_email,
                   jobs.updated_by_user_id, jobs.updated_by_name, jobs.updated_by_email,
                   configs.scoring_config_json
            FROM jobs
            LEFT JOIN job_scoring_configs AS configs
              ON configs.job_title = jobs.title
            WHERE jobs.title = ?
            """,
            (clean_title,),
        ).fetchone()

        created_at = str(existing["created_at"]) if existing else now_str()
        current_openings = int(existing["openings"]) if existing else 0
        openings_num = _safe_openings(openings, current_openings)
        scoring_config = _decode_scoring_config(existing["scoring_config_json"] if existing else None)

        creator_user_id = _prefer_existing(existing["created_by_user_id"] if existing else "", created_by_user_id)
        creator_name = _prefer_existing(existing["created_by_name"] if existing else "", created_by_name)
        creator_email = _prefer_existing(existing["created_by_email"] if existing else "", created_by_email)

        updater_user_id = str(updated_by_user_id or created_by_user_id or "").strip()
        updater_name = str(updated_by_name or created_by_name or "").strip()
        updater_email = str(updated_by_email or created_by_email or "").strip()
        if existing:
            updater_user_id = updater_user_id or str(existing["updated_by_user_id"] or "")
            updater_name = updater_name or str(existing["updated_by_name"] or "")
            updater_email = updater_email or str(existing["updated_by_email"] or "")

        updated_at = now_str()
        with conn:
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO jobs(
                        title, jd_text, openings,
                        created_by_user_id, created_by_name, created_by_email,
                        updated_by_user_id, updated_by_name, updated_by_email,
                        created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_title,
                        clean_text,
                        openings_num,
                        creator_user_id,
                        creator_name,
                        creator_email,
                        updater_user_id,
                        updater_name,
                        updater_email,
                        created_at,
                        updated_at,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET jd_text = ?,
                        openings = ?,
                        created_by_user_id = ?,
                        created_by_name = ?,
                        created_by_email = ?,
                        updated_by_user_id = ?,
                        updated_by_name = ?,
                        updated_by_email = ?,
                        updated_at = ?
                    WHERE title = ?
                    """,
                    (
                        clean_text,
                        openings_num,
                        creator_user_id,
                        creator_name,
                        creator_email,
                        updater_user_id,
                        updater_name,
                        updater_email,
                        updated_at,
                        clean_title,
                    ),
                )

            if existing is None or existing["scoring_config_json"] is None:
                conn.execute(
                    """
                    INSERT INTO job_scoring_configs(job_title, scoring_config_json, updated_at)
                    VALUES(?, ?, ?)
                    """,
                    (clean_title, json_dumps(scoring_config), updated_at),
                )
    finally:
        conn.close()


def list_jds() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT title FROM jobs ORDER BY title ASC").fetchall()
        return [str(row["title"]) for row in rows]
    finally:
        conn.close()


def list_jd_records() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT jobs.title, jobs.jd_text, jobs.updated_at, jobs.openings,
                   jobs.created_by_user_id, jobs.created_by_name, jobs.created_by_email,
                   jobs.updated_by_user_id, jobs.updated_by_name, jobs.updated_by_email,
                   configs.scoring_config_json
            FROM jobs
            LEFT JOIN job_scoring_configs AS configs
              ON configs.job_title = jobs.title
            ORDER BY jobs.title ASC
            """
        ).fetchall()
        return [
            {
                "title": str(row["title"]),
                "text": str(row["jd_text"] or ""),
                "updated_at": str(row["updated_at"] or "-"),
                "openings": int(row["openings"] or 0),
                "created_by_user_id": str(row["created_by_user_id"] or ""),
                "created_by_name": str(row["created_by_name"] or ""),
                "created_by_email": str(row["created_by_email"] or ""),
                "updated_by_user_id": str(row["updated_by_user_id"] or ""),
                "updated_by_name": str(row["updated_by_name"] or ""),
                "updated_by_email": str(row["updated_by_email"] or ""),
                "scoring_config": _decode_scoring_config(row["scoring_config_json"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def load_jd(title: str) -> str:
    clean_title = (title or "").strip()
    if not clean_title:
        return ""

    conn = get_connection()
    try:
        row = conn.execute("SELECT jd_text FROM jobs WHERE title = ?", (clean_title,)).fetchone()
        if row is None:
            return ""
        return str(row["jd_text"] or "")
    finally:
        conn.close()


def update_jd(
    title: str,
    jd_text: str,
    openings: int | None = None,
    created_by_name: str = "",
    created_by_email: str = "",
    created_by_user_id: str = "",
    updated_by_user_id: str = "",
    updated_by_name: str = "",
    updated_by_email: str = "",
) -> None:
    clean_title = (title or "").strip()
    clean_text = (jd_text or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空。")
    if not clean_text:
        raise ValueError("JD 内容不能为空。")

    conn = get_connection()
    try:
        existing = conn.execute(
            """
            SELECT created_at, openings,
                   created_by_user_id, created_by_name, created_by_email,
                   updated_by_user_id, updated_by_name, updated_by_email
            FROM jobs
            WHERE title = ?
            """,
            (clean_title,),
        ).fetchone()
        if existing is None:
            raise ValueError("JD 不存在，无法更新。")

        openings_num = _safe_openings(openings, int(existing["openings"] or 0)) if openings is not None else int(existing["openings"] or 0)
        creator_user_id = _prefer_existing(existing["created_by_user_id"], created_by_user_id)
        creator_name = _prefer_existing(existing["created_by_name"], created_by_name)
        creator_email = _prefer_existing(existing["created_by_email"], created_by_email)

        updater_user_id = str(updated_by_user_id or created_by_user_id or "").strip() or str(existing["updated_by_user_id"] or "")
        updater_name = str(updated_by_name or created_by_name or "").strip() or str(existing["updated_by_name"] or "")
        updater_email = str(updated_by_email or created_by_email or "").strip() or str(existing["updated_by_email"] or "")

        with conn:
            conn.execute(
                """
                UPDATE jobs
                SET jd_text = ?,
                    openings = ?,
                    created_by_user_id = ?,
                    created_by_name = ?,
                    created_by_email = ?,
                    updated_by_user_id = ?,
                    updated_by_name = ?,
                    updated_by_email = ?,
                    updated_at = ?
                WHERE title = ?
                """,
                (
                    clean_text,
                    openings_num,
                    creator_user_id,
                    creator_name,
                    creator_email,
                    updater_user_id,
                    updater_name,
                    updater_email,
                    now_str(),
                    clean_title,
                ),
            )
    finally:
        conn.close()


def upsert_jd_openings(
    title: str,
    openings: int,
    updated_by_user_id: str = "",
    updated_by_name: str = "",
    updated_by_email: str = "",
) -> None:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空。")

    conn = get_connection()
    try:
        existing = conn.execute(
            """
            SELECT openings, updated_by_user_id, updated_by_name, updated_by_email
            FROM jobs
            WHERE title = ?
            """,
            (clean_title,),
        ).fetchone()
        if existing is None:
            raise ValueError("JD 不存在，无法更新空缺人数。")

        with conn:
            conn.execute(
                """
                UPDATE jobs
                SET openings = ?,
                    updated_by_user_id = ?,
                    updated_by_name = ?,
                    updated_by_email = ?,
                    updated_at = ?
                WHERE title = ?
                """,
                (
                    _safe_openings(openings, int(existing["openings"] or 0)),
                    str(updated_by_user_id or existing["updated_by_user_id"] or ""),
                    str(updated_by_name or existing["updated_by_name"] or ""),
                    str(updated_by_email or existing["updated_by_email"] or ""),
                    now_str(),
                    clean_title,
                ),
            )
    finally:
        conn.close()


def load_jd_scoring_config(title: str) -> dict[str, Any]:
    clean_title = (title or "").strip()
    if not clean_title:
        return _default_scoring_config()

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT scoring_config_json FROM job_scoring_configs WHERE job_title = ?",
            (clean_title,),
        ).fetchone()
        if row is None:
            return _default_scoring_config()
        return _decode_scoring_config(row["scoring_config_json"])
    finally:
        conn.close()


def upsert_jd_scoring_config(title: str, scoring_config: dict) -> None:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空。")

    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM jobs WHERE title = ?", (clean_title,)).fetchone()
        if exists is None:
            raise ValueError("JD 不存在，无法保存评分配置。")

        payload = scoring_config if isinstance(scoring_config, dict) else _default_scoring_config()
        updated_at = now_str()
        with conn:
            existing_config = conn.execute(
                "SELECT 1 FROM job_scoring_configs WHERE job_title = ? LIMIT 1",
                (clean_title,),
            ).fetchone()
            if existing_config is None:
                conn.execute(
                    """
                    INSERT INTO job_scoring_configs(job_title, scoring_config_json, updated_at)
                    VALUES(?, ?, ?)
                    """,
                    (clean_title, json_dumps(payload), updated_at),
                )
            else:
                conn.execute(
                    """
                    UPDATE job_scoring_configs
                    SET scoring_config_json = ?,
                        updated_at = ?
                    WHERE job_title = ?
                    """,
                    (json_dumps(payload), updated_at, clean_title),
                )
            conn.execute(
                "UPDATE jobs SET updated_at = ? WHERE title = ?",
                (updated_at, clean_title),
            )
    finally:
        conn.close()


def delete_jd(title: str) -> None:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空。")

    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM jobs WHERE title = ?", (clean_title,)).fetchone()
        if exists is None:
            raise ValueError("JD 不存在，无法删除。")

        with conn:
            conn.execute("DELETE FROM jobs WHERE title = ?", (clean_title,))
    finally:
        conn.close()
