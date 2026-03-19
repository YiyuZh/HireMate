"""审核结果存储（SQLite 版）。"""

from __future__ import annotations

from typing import Any

from .sqlite_store import get_connection, json_dumps, json_loads, now_str


def _normalize_review(record: dict) -> dict[str, Any]:
    """兼容旧记录字段，统一为可留痕结构。"""
    timestamp = str(record.get("timestamp") or now_str())
    auto_screening_result = str(record.get("auto_screening_result") or record.get("screening_result") or "")
    auto_risk_level = str(record.get("auto_risk_level") or record.get("risk_level") or "unknown")

    scores = record.get("scores")
    if not isinstance(scores, dict):
        scores = {}

    screening_reasons = record.get("screening_reasons")
    if not isinstance(screening_reasons, list):
        screening_reasons = []

    risk_points = record.get("risk_points")
    if not isinstance(risk_points, list):
        risk_points = []

    evidence_snippets = record.get("evidence_snippets")
    if not isinstance(evidence_snippets, list):
        evidence_snippets = []

    return {
        "review_id": str(record.get("review_id") or "").strip(),
        "timestamp": timestamp,
        "updated_at": str(record.get("updated_at") or timestamp),
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


def _decode_review_row(row) -> dict[str, Any]:
    base = json_loads(row["record_json"], {})
    if not isinstance(base, dict):
        base = {}

    base.update(
        {
            "review_id": str(row["review_id"] or ""),
            "timestamp": str(row["timestamp"] or ""),
            "updated_at": str(row["updated_at"] or row["timestamp"] or ""),
            "jd_title": str(row["jd_title"] or ""),
            "resume_name": str(row["resume_name"] or ""),
            "resume_file": str(row["resume_file"] or ""),
            "scores": json_loads(row["scores_json"], {}),
            "auto_screening_result": str(row["auto_screening_result"] or ""),
            "auto_risk_level": str(row["auto_risk_level"] or "unknown"),
            "manual_decision": str(row["manual_decision"] or ""),
            "manual_note": str(row["manual_note"] or ""),
            "screening_result": str(row["auto_screening_result"] or ""),
            "risk_level": str(row["auto_risk_level"] or "unknown"),
            "screening_reasons": json_loads(row["screening_reasons_json"], []),
            "risk_points": json_loads(row["risk_points_json"], []),
            "interview_summary": str(row["interview_summary"] or ""),
            "evidence_snippets": json_loads(row["evidence_snippets_json"], []),
        }
    )
    return _normalize_review(base)


def append_review(record: dict) -> None:
    """追加一条审核记录。"""
    normalized = _normalize_review(record)
    normalized["updated_at"] = now_str()

    conn = get_connection()
    try:
        with conn:
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
                    normalized["review_id"],
                    normalized["timestamp"],
                    normalized["updated_at"],
                    normalized["jd_title"],
                    normalized["resume_name"],
                    normalized["resume_file"],
                    normalized["auto_screening_result"],
                    normalized["auto_risk_level"],
                    normalized["manual_decision"],
                    normalized["manual_note"],
                    json_dumps(normalized.get("scores") or {}),
                    json_dumps(normalized.get("screening_reasons") or []),
                    json_dumps(normalized.get("risk_points") or []),
                    normalized["interview_summary"],
                    json_dumps(normalized.get("evidence_snippets") or []),
                    json_dumps(normalized),
                ),
            )
    finally:
        conn.close()


def upsert_manual_review(
    review_id: str,
    manual_decision: str | None = None,
    manual_note: str | None = None,
) -> bool:
    """按 review_id 写入人工决策/备注，成功返回 True。"""
    key = (review_id or "").strip()
    if not key:
        return False

    conn = get_connection()
    try:
        current = conn.execute(
            "SELECT * FROM reviews WHERE review_id = ?",
            (key,),
        ).fetchone()
        if current is None:
            return False

        normalized = _decode_review_row(current)
        if manual_decision is not None:
            normalized["manual_decision"] = str(manual_decision)
        if manual_note is not None:
            normalized["manual_note"] = str(manual_note)
        normalized["updated_at"] = now_str()

        with conn:
            conn.execute(
                """
                UPDATE reviews
                SET updated_at = ?,
                    manual_decision = ?,
                    manual_note = ?,
                    record_json = ?
                WHERE review_id = ?
                """,
                (
                    normalized["updated_at"],
                    normalized["manual_decision"],
                    normalized["manual_note"],
                    json_dumps(normalized),
                    key,
                ),
            )
        return True
    finally:
        conn.close()


def list_reviews(limit: int | None = None) -> list[dict]:
    """返回历史记录（按时间倒序）。"""
    conn = get_connection()
    try:
        sql = """
            SELECT *
            FROM reviews
            ORDER BY timestamp DESC, id DESC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)

        rows = conn.execute(sql, params).fetchall()
        return [_decode_review_row(row) for row in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    append_review(
        {
            "review_id": "demo-review-id",
            "timestamp": "2026-01-01 10:00:00",
            "jd_title": "AI 产品经理实习生",
            "resume_name": "张三",
            "scores": {"教育背景匹配度": 4},
            "auto_risk_level": "low",
            "auto_screening_result": "推荐进入下一轮",
        }
    )
    upsert_manual_review("demo-review-id", manual_decision="待复核", manual_note="需核验项目真实性")
    print(list_reviews(limit=3))
