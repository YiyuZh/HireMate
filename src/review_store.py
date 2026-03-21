"""Review record store backed by SQLite."""

from __future__ import annotations

from typing import Any

from .db import get_connection, json_dumps, json_loads, now_str


def _normalize_review(record: dict) -> dict[str, Any]:
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
        "reviewed_by_user_id": str(record.get("reviewed_by_user_id") or ""),
        "reviewed_by_name": str(record.get("reviewed_by_name") or ""),
        "reviewed_by_email": str(record.get("reviewed_by_email") or ""),
        "manual_decision": str(record.get("manual_decision") or ""),
        "manual_note": str(record.get("manual_note") or ""),
        "manual_priority": str(record.get("manual_priority") or ""),
        "screening_result": auto_screening_result,
        "risk_level": auto_risk_level,
        "screening_reasons": screening_reasons,
        "risk_points": risk_points,
        "interview_summary": str(record.get("interview_summary") or ""),
        "evidence_snippets": evidence_snippets,
        "ai_applied": bool(record.get("ai_applied", False)),
        "ai_applied_actions": record.get("ai_applied_actions") if isinstance(record.get("ai_applied_actions"), list) else [],
        "ai_applied_by_name": str(record.get("ai_applied_by_name") or ""),
        "ai_applied_by_email": str(record.get("ai_applied_by_email") or ""),
        "ai_applied_at": str(record.get("ai_applied_at") or ""),
        "ai_source": str(record.get("ai_source") or ""),
        "ai_mode": str(record.get("ai_mode") or ""),
        "ai_model": str(record.get("ai_model") or ""),
        "ai_review_status": str(record.get("ai_review_status") or ""),
        "ai_input_hash": str(record.get("ai_input_hash") or ""),
        "ai_prompt_version": str(record.get("ai_prompt_version") or ""),
        "ai_generated_latency_ms": int(record.get("ai_generated_latency_ms") or 0),
        "ai_generation_reason": str(record.get("ai_generation_reason") or ""),
        "ai_refresh_reason": str(record.get("ai_refresh_reason") or ""),
        "ai_generated_at": str(record.get("ai_generated_at") or ""),
        "ai_generated_by_name": str(record.get("ai_generated_by_name") or ""),
        "ai_generated_by_email": str(record.get("ai_generated_by_email") or ""),
        "ai_review_error": str(record.get("ai_review_error") or ""),
        "ai_review_summary_snapshot": str(record.get("ai_review_summary_snapshot") or ""),
        "ai_score_adjustments_snapshot": (
            record.get("ai_score_adjustments_snapshot")
            if isinstance(record.get("ai_score_adjustments_snapshot"), list)
            else []
        ),
        "ai_risk_adjustment_snapshot": (
            record.get("ai_risk_adjustment_snapshot")
            if isinstance(record.get("ai_risk_adjustment_snapshot"), dict)
            else {}
        ),
        "ai_reverted": bool(record.get("ai_reverted", False)),
        "ai_reverted_actions": record.get("ai_reverted_actions") if isinstance(record.get("ai_reverted_actions"), list) else [],
        "ai_reverted_at": str(record.get("ai_reverted_at") or ""),
        "ai_reverted_by_name": str(record.get("ai_reverted_by_name") or ""),
        "ai_reverted_by_email": str(record.get("ai_reverted_by_email") or ""),
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
            "reviewed_by_user_id": str(row["reviewed_by_user_id"] or ""),
            "reviewed_by_name": str(row["reviewed_by_name"] or ""),
            "reviewed_by_email": str(row["reviewed_by_email"] or ""),
            "manual_decision": str(row["manual_decision"] or ""),
            "manual_note": str(row["manual_note"] or ""),
            "manual_priority": str(row["manual_priority"] or ""),
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
    normalized = _normalize_review(record)
    normalized["updated_at"] = now_str()
    review_id = str(normalized.get("review_id") or "").strip()

    conn = get_connection()
    try:
        review_id_db = review_id or ("" if getattr(conn, "backend", "sqlite") == "sqlite" else None)
        existing = None
        if review_id:
            existing = conn.execute(
                "SELECT id FROM reviews WHERE review_id = ? LIMIT 1",
                (review_id,),
            ).fetchone()

        with conn:
            payload = (
                review_id_db,
                normalized["timestamp"],
                normalized["updated_at"],
                normalized["jd_title"],
                normalized["resume_name"],
                normalized["resume_file"],
                normalized["auto_screening_result"],
                normalized["auto_risk_level"],
                normalized["reviewed_by_user_id"],
                normalized["reviewed_by_name"],
                normalized["reviewed_by_email"],
                normalized["manual_decision"],
                normalized["manual_note"],
                normalized["manual_priority"],
                json_dumps(normalized.get("scores") or {}),
                json_dumps(normalized.get("screening_reasons") or []),
                json_dumps(normalized.get("risk_points") or []),
                normalized["interview_summary"],
                json_dumps(normalized.get("evidence_snippets") or []),
                json_dumps(normalized),
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO reviews(
                        review_id, timestamp, updated_at, jd_title, resume_name, resume_file,
                        auto_screening_result, auto_risk_level,
                        reviewed_by_user_id, reviewed_by_name, reviewed_by_email,
                        manual_decision, manual_note, manual_priority,
                        scores_json, screening_reasons_json, risk_points_json, interview_summary,
                        evidence_snippets_json, record_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
            else:
                conn.execute(
                    """
                    UPDATE reviews
                    SET timestamp = ?,
                        updated_at = ?,
                        jd_title = ?,
                        resume_name = ?,
                        resume_file = ?,
                        auto_screening_result = ?,
                        auto_risk_level = ?,
                        reviewed_by_user_id = ?,
                        reviewed_by_name = ?,
                        reviewed_by_email = ?,
                        manual_decision = ?,
                        manual_note = ?,
                        manual_priority = ?,
                        scores_json = ?,
                        screening_reasons_json = ?,
                        risk_points_json = ?,
                        interview_summary = ?,
                        evidence_snippets_json = ?,
                        record_json = ?
                    WHERE review_id = ?
                    """,
                    (
                        normalized["timestamp"],
                        normalized["updated_at"],
                        normalized["jd_title"],
                        normalized["resume_name"],
                        normalized["resume_file"],
                        normalized["auto_screening_result"],
                        normalized["auto_risk_level"],
                        normalized["reviewed_by_user_id"],
                        normalized["reviewed_by_name"],
                        normalized["reviewed_by_email"],
                        normalized["manual_decision"],
                        normalized["manual_note"],
                        normalized["manual_priority"],
                        json_dumps(normalized.get("scores") or {}),
                        json_dumps(normalized.get("screening_reasons") or []),
                        json_dumps(normalized.get("risk_points") or []),
                        normalized["interview_summary"],
                        json_dumps(normalized.get("evidence_snippets") or []),
                        json_dumps(normalized),
                        review_id,
                    ),
                )
    finally:
        conn.close()


def upsert_manual_review(
    review_id: str,
    manual_decision: str | None = None,
    manual_note: str | None = None,
    reviewed_by_user_id: str | None = None,
    reviewed_by_name: str | None = None,
    reviewed_by_email: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
) -> bool:
    key = (review_id or "").strip()
    if not key:
        return False

    conn = get_connection()
    try:
        current = conn.execute("SELECT * FROM reviews WHERE review_id = ?", (key,)).fetchone()
        if current is None:
            return False

        normalized = _decode_review_row(current)
        if manual_decision is not None:
            normalized["manual_decision"] = str(manual_decision)
        if manual_note is not None:
            normalized["manual_note"] = str(manual_note)
        if reviewed_by_user_id is not None:
            normalized["reviewed_by_user_id"] = str(reviewed_by_user_id)
        if reviewed_by_name is not None:
            normalized["reviewed_by_name"] = str(reviewed_by_name)
        if reviewed_by_email is not None:
            normalized["reviewed_by_email"] = str(reviewed_by_email)
        if isinstance(metadata_updates, dict):
            normalized.update(metadata_updates)
        normalized["updated_at"] = now_str()

        with conn:
            conn.execute(
                """
                UPDATE reviews
                SET updated_at = ?,
                    auto_screening_result = ?,
                    auto_risk_level = ?,
                    reviewed_by_user_id = ?,
                    reviewed_by_name = ?,
                    reviewed_by_email = ?,
                    manual_decision = ?,
                    manual_note = ?,
                    manual_priority = ?,
                    scores_json = ?,
                    screening_reasons_json = ?,
                    risk_points_json = ?,
                    interview_summary = ?,
                    evidence_snippets_json = ?,
                    record_json = ?
                WHERE review_id = ?
                """,
                (
                    normalized["updated_at"],
                    normalized["auto_screening_result"],
                    normalized["auto_risk_level"],
                    normalized["reviewed_by_user_id"],
                    normalized["reviewed_by_name"],
                    normalized["reviewed_by_email"],
                    normalized["manual_decision"],
                    normalized["manual_note"],
                    normalized["manual_priority"],
                    json_dumps(normalized.get("scores") or {}),
                    json_dumps(normalized.get("screening_reasons") or []),
                    json_dumps(normalized.get("risk_points") or []),
                    normalized["interview_summary"],
                    json_dumps(normalized.get("evidence_snippets") or []),
                    json_dumps(normalized),
                    key,
                ),
            )
        return True
    finally:
        conn.close()


def list_reviews(limit: int | None = None) -> list[dict]:
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
