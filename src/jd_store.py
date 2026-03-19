"""JD store backed by SQLite."""

from __future__ import annotations

from typing import Any

from .role_profiles import build_default_scoring_config
from .sqlite_store import get_connection, json_dumps, json_loads, now_str

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


def save_jd(title: str, jd_text: str, openings: int = 0) -> None:
    """保存或更新一条 JD。"""
    clean_title = (title or "").strip()
    clean_text = (jd_text or "").strip()

    if not clean_title:
        raise ValueError("JD 标题不能为空")
    if not clean_text:
        raise ValueError("JD 文本不能为空")

    conn = get_connection()
    try:
        existing = conn.execute(
            """
            SELECT jobs.created_at, jobs.openings, configs.scoring_config_json
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
        updated_at = now_str()

        with conn:
            conn.execute(
                """
                INSERT INTO jobs(title, jd_text, openings, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(title) DO UPDATE SET
                    jd_text = excluded.jd_text,
                    openings = excluded.openings,
                    updated_at = excluded.updated_at
                """,
                (clean_title, clean_text, openings_num, created_at, updated_at),
            )
            conn.execute(
                """
                INSERT INTO job_scoring_configs(job_title, scoring_config_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(job_title) DO NOTHING
                """,
                (clean_title, json_dumps(scoring_config), updated_at),
            )
    finally:
        conn.close()


def list_jds() -> list[str]:
    """返回所有已保存 JD 标题。"""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT title FROM jobs ORDER BY title ASC").fetchall()
        return [str(row["title"]) for row in rows]
    finally:
        conn.close()


def list_jd_records() -> list[dict[str, Any]]:
    """返回岗位库展示所需记录列表。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT jobs.title, jobs.jd_text, jobs.updated_at, jobs.openings, configs.scoring_config_json
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
                "scoring_config": _decode_scoring_config(row["scoring_config_json"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def load_jd(title: str) -> str:
    """按标题加载 JD 文本，不存在时返回空字符串。"""
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


def update_jd(title: str, jd_text: str, openings: int | None = None) -> None:
    """更新指定标题 JD（不存在时抛错）。"""
    clean_title = (title or "").strip()
    clean_text = (jd_text or "").strip()

    if not clean_title:
        raise ValueError("JD 标题不能为空")
    if not clean_text:
        raise ValueError("JD 文本不能为空")

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT created_at, openings FROM jobs WHERE title = ?",
            (clean_title,),
        ).fetchone()
        if existing is None:
            raise ValueError("JD 不存在，无法更新")

        openings_num = _safe_openings(openings, int(existing["openings"] or 0)) if openings is not None else int(existing["openings"] or 0)
        with conn:
            conn.execute(
                """
                UPDATE jobs
                SET jd_text = ?, openings = ?, updated_at = ?
                WHERE title = ?
                """,
                (clean_text, openings_num, now_str(), clean_title),
            )
    finally:
        conn.close()


def upsert_jd_openings(title: str, openings: int) -> None:
    """仅更新岗位空缺人数（不存在时抛错）。"""
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空")

    conn = get_connection()
    try:
        existing = conn.execute("SELECT openings FROM jobs WHERE title = ?", (clean_title,)).fetchone()
        if existing is None:
            raise ValueError("JD 不存在，无法更新空缺人数")

        with conn:
            conn.execute(
                """
                UPDATE jobs
                SET openings = ?, updated_at = ?
                WHERE title = ?
                """,
                (_safe_openings(openings, int(existing["openings"] or 0)), now_str(), clean_title),
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
        raise ValueError("JD 标题不能为空")

    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM jobs WHERE title = ?", (clean_title,)).fetchone()
        if exists is None:
            raise ValueError("JD 不存在，无法更新评分设置")

        payload = scoring_config if isinstance(scoring_config, dict) else _default_scoring_config()
        updated_at = now_str()
        with conn:
            conn.execute(
                """
                INSERT INTO job_scoring_configs(job_title, scoring_config_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(job_title) DO UPDATE SET
                    scoring_config_json = excluded.scoring_config_json,
                    updated_at = excluded.updated_at
                """,
                (clean_title, json_dumps(payload), updated_at),
            )
            conn.execute(
                "UPDATE jobs SET updated_at = ? WHERE title = ?",
                (updated_at, clean_title),
            )
    finally:
        conn.close()


def delete_jd(title: str) -> None:
    """删除指定标题 JD（不存在时抛错）。"""
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空")

    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM jobs WHERE title = ?", (clean_title,)).fetchone()
        if exists is None:
            raise ValueError("JD 不存在，无法删除")

        with conn:
            conn.execute("DELETE FROM jobs WHERE title = ?", (clean_title,))
    finally:
        conn.close()


if __name__ == "__main__":
    demo_title = "示例JD-产品经理实习生"
    demo_text = "岗位职责：参与需求分析、PRD撰写、跨团队协作。"

    save_jd(demo_title, demo_text)
    print("已保存标题：", list_jds())
    print("岗位记录：", list_jd_records())
    print("加载内容：", load_jd(demo_title))
    update_jd(demo_title, "岗位职责：更新后的JD内容")
    print("更新后内容：", load_jd(demo_title))
    delete_jd(demo_title)
    print("删除后标题：", list_jds())
