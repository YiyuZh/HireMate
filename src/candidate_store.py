"""Candidate batch store backed by SQLite."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from .db import get_connection, json_dumps, json_loads, now_str

ROW_KEY_NAME = "姓名"
ROW_KEY_FILE_NAME = "文件名"
ROW_KEY_PARSE_STATUS = "解析状态"
ROW_KEY_SCREENING_RESULT = "初筛结论"
ROW_KEY_RISK_LEVEL = "风险等级"
ROW_KEY_CANDIDATE_POOL = "候选池"
ROW_KEY_MANUAL_DECISION = "人工最终结论"
ROW_KEY_MANUAL_NOTE = "人工备注"
ROW_KEY_MANUAL_PRIORITY = "处理优先级"
ROW_KEY_REVIEW_SUMMARY = "审核摘要"

ROW_KEY_LOCK_STATUS = "锁定状态"
ROW_KEY_LOCK_OWNER = "锁定人"
ROW_KEY_LOCK_EXPIRES_AT = "锁过期时间"

LOCK_STATUS_UNLOCKED = "unlocked"
LOCK_STATUS_LOCKED = "locked"
LOCK_REASON_WORKSPACE_CLAIM = "workspace_claim"
DEFAULT_LOCK_TTL_MINUTES = 30


def _safe_candidate_id(value: Any) -> str:
    candidate_id = str(value or "").strip()
    if candidate_id:
        return candidate_id
    return f"cand-{uuid4().hex[:8]}"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _manual_pool_label(decision: str) -> str:
    mapping = {
        "通过": "通过候选人",
        "待复核": "待复核候选人",
        "淘汰": "淘汰候选人",
    }
    return mapping.get((decision or "").strip(), "")


def _decode_dict(payload: str | None, default: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = default if isinstance(default, dict) else {}
    data = json_loads(payload, fallback)
    return data if isinstance(data, dict) else dict(fallback)


def _candidate_state_payload(*, manual_decision: str, manual_note: str, manual_priority: str) -> dict[str, Any]:
    return {
        "manual_decision": _safe_text(manual_decision),
        "manual_note": str(manual_note or ""),
        "manual_priority": _safe_text(manual_priority) or "普通",
    }


def _determine_action_type(changed_fields: list[str]) -> str:
    unique_fields = list(dict.fromkeys(changed_fields))
    if unique_fields == ["manual_note"]:
        return "manual_note_updated"
    if unique_fields == ["manual_priority"]:
        return "manual_priority_updated"
    if unique_fields == ["manual_decision"]:
        return "manual_decision_updated"
    return "candidate_manual_review_updated"


def _append_candidate_action_log(
    conn,
    *,
    batch_id: str,
    candidate_id: str,
    review_id: str,
    jd_title: str,
    action_type: str,
    operator_user_id: str,
    operator_name: str,
    operator_email: str,
    before_payload: dict[str, Any],
    after_payload: dict[str, Any],
    extra_payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO candidate_action_logs(
            action_id, batch_id, candidate_id, review_id, jd_title, action_type,
            operator_user_id, operator_name, operator_email,
            before_json, after_json, extra_json, created_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"action-{uuid4().hex}",
            _safe_text(batch_id),
            _safe_text(candidate_id),
            _safe_text(review_id),
            _safe_text(jd_title),
            _safe_text(action_type),
            _safe_text(operator_user_id),
            _safe_text(operator_name),
            _safe_text(operator_email),
            json_dumps(before_payload if isinstance(before_payload, dict) else {}),
            json_dumps(after_payload if isinstance(after_payload, dict) else {}),
            json_dumps(extra_payload if isinstance(extra_payload, dict) else {}),
            now_str(),
        ),
    )


def _parse_ts(value: Any) -> datetime | None:
    raw = _safe_text(value)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _lock_expires_at(ttl_minutes: int) -> str:
    ttl = max(1, _safe_int(ttl_minutes, DEFAULT_LOCK_TTL_MINUTES))
    return (datetime.now() + timedelta(minutes=ttl)).strftime("%Y-%m-%d %H:%M:%S")


def _lock_is_expired(lock_status: str, lock_expires_at: str) -> bool:
    if (lock_status or "").strip().lower() != LOCK_STATUS_LOCKED:
        return False
    expires_at = _parse_ts(lock_expires_at)
    if expires_at is None:
        return True
    return expires_at <= datetime.now()


def _normalize_lock_state_from_row(candidate_row) -> dict[str, Any]:
    if candidate_row is None:
        return {
            "batch_id": "",
            "candidate_id": "",
            "lock_status": LOCK_STATUS_UNLOCKED,
            "lock_owner_user_id": "",
            "lock_owner_name": "",
            "lock_owner_email": "",
            "lock_acquired_at": "",
            "lock_expires_at": "",
            "lock_last_heartbeat_at": "",
            "lock_reason": "",
            "is_expired": False,
            "is_locked_effective": False,
        }

    raw_status = _safe_text(candidate_row["lock_status"]).lower() or LOCK_STATUS_UNLOCKED
    lock_status = raw_status if raw_status in {LOCK_STATUS_UNLOCKED, LOCK_STATUS_LOCKED} else LOCK_STATUS_UNLOCKED
    lock_owner_user_id = _safe_text(candidate_row["lock_owner_user_id"])
    lock_owner_name = _safe_text(candidate_row["lock_owner_name"])
    lock_owner_email = _safe_text(candidate_row["lock_owner_email"])
    lock_acquired_at = _safe_text(candidate_row["lock_acquired_at"])
    lock_expires_at = _safe_text(candidate_row["lock_expires_at"])
    lock_last_heartbeat_at = _safe_text(candidate_row["lock_last_heartbeat_at"])
    lock_reason = _safe_text(candidate_row["lock_reason"])
    is_expired = _lock_is_expired(lock_status, lock_expires_at)
    is_locked_effective = lock_status == LOCK_STATUS_LOCKED and not is_expired and bool(lock_owner_user_id)

    return {
        "batch_id": _safe_text(candidate_row["batch_id"]),
        "candidate_id": _safe_text(candidate_row["candidate_id"]),
        "lock_status": LOCK_STATUS_LOCKED if is_locked_effective else LOCK_STATUS_UNLOCKED,
        "lock_owner_user_id": lock_owner_user_id,
        "lock_owner_name": lock_owner_name,
        "lock_owner_email": lock_owner_email,
        "lock_acquired_at": lock_acquired_at,
        "lock_expires_at": lock_expires_at,
        "lock_last_heartbeat_at": lock_last_heartbeat_at,
        "lock_reason": lock_reason,
        "is_expired": is_expired,
        "is_locked_effective": is_locked_effective,
    }


def _lock_owner_label(lock_state: dict[str, Any]) -> str:
    return _safe_text(lock_state.get("lock_owner_name")) or _safe_text(lock_state.get("lock_owner_email")) or "-"


def _lock_badge_label(lock_state: dict[str, Any], operator_user_id: str = "") -> str:
    if not bool(lock_state.get("is_locked_effective")):
        return "未领取"
    if operator_user_id and _safe_text(lock_state.get("lock_owner_user_id")) == _safe_text(operator_user_id):
        return "我处理中"
    return "他人锁定"


def _apply_lock_state_to_detail(detail_payload: dict[str, Any], lock_state: dict[str, Any]) -> None:
    detail_payload["lock_status"] = _safe_text(lock_state.get("lock_status")) or LOCK_STATUS_UNLOCKED
    detail_payload["lock_owner_user_id"] = _safe_text(lock_state.get("lock_owner_user_id"))
    detail_payload["lock_owner_name"] = _safe_text(lock_state.get("lock_owner_name"))
    detail_payload["lock_owner_email"] = _safe_text(lock_state.get("lock_owner_email"))
    detail_payload["lock_acquired_at"] = _safe_text(lock_state.get("lock_acquired_at"))
    detail_payload["lock_expires_at"] = _safe_text(lock_state.get("lock_expires_at"))
    detail_payload["lock_last_heartbeat_at"] = _safe_text(lock_state.get("lock_last_heartbeat_at"))
    detail_payload["lock_reason"] = _safe_text(lock_state.get("lock_reason"))
    detail_payload["is_expired"] = bool(lock_state.get("is_expired"))
    detail_payload["is_locked_effective"] = bool(lock_state.get("is_locked_effective"))


def _apply_lock_state_to_row(
    row_payload: dict[str, Any],
    lock_state: dict[str, Any],
    *,
    operator_user_id: str = "",
) -> None:
    row_payload[ROW_KEY_LOCK_STATUS] = _lock_badge_label(lock_state, operator_user_id)
    row_payload[ROW_KEY_LOCK_OWNER] = _lock_owner_label(lock_state)
    row_payload[ROW_KEY_LOCK_EXPIRES_AT] = _safe_text(lock_state.get("lock_expires_at")) or "-"


def _fetch_candidate_row(conn, batch_id: str, candidate_id: str):
    return conn.execute(
        """
        SELECT *
        FROM candidate_rows
        WHERE batch_id = ? AND candidate_id = ?
        """,
        (batch_id, candidate_id),
    ).fetchone()


def _write_lock_fields(
    conn,
    *,
    batch_id: str,
    candidate_id: str,
    lock_status: str,
    lock_owner_user_id: str,
    lock_owner_name: str,
    lock_owner_email: str,
    lock_acquired_at: str,
    lock_expires_at: str,
    lock_last_heartbeat_at: str,
    lock_reason: str,
) -> None:
    conn.execute(
        """
        UPDATE candidate_rows
        SET lock_status = ?,
            lock_owner_user_id = ?,
            lock_owner_name = ?,
            lock_owner_email = ?,
            lock_acquired_at = ?,
            lock_expires_at = ?,
            lock_last_heartbeat_at = ?,
            lock_reason = ?,
            updated_at = ?
        WHERE batch_id = ? AND candidate_id = ?
        """,
        (
            lock_status,
            lock_owner_user_id,
            lock_owner_name,
            lock_owner_email,
            lock_acquired_at,
            lock_expires_at,
            lock_last_heartbeat_at,
            lock_reason,
            now_str(),
            batch_id,
            candidate_id,
        ),
    )


def _maybe_refresh_owned_lock(conn, *, batch_id: str, candidate_id: str, operator_user_id: str, ttl_minutes: int) -> None:
    owner_id = _safe_text(operator_user_id)
    if not owner_id:
        return
    row = _fetch_candidate_row(conn, batch_id, candidate_id)
    lock_state = _normalize_lock_state_from_row(row)
    if not bool(lock_state.get("is_locked_effective")):
        return
    if _safe_text(lock_state.get("lock_owner_user_id")) != owner_id:
        return
    _write_lock_fields(
        conn,
        batch_id=batch_id,
        candidate_id=candidate_id,
        lock_status=LOCK_STATUS_LOCKED,
        lock_owner_user_id=owner_id,
        lock_owner_name=_safe_text(lock_state.get("lock_owner_name")),
        lock_owner_email=_safe_text(lock_state.get("lock_owner_email")),
        lock_acquired_at=_safe_text(lock_state.get("lock_acquired_at")) or now_str(),
        lock_expires_at=_lock_expires_at(ttl_minutes),
        lock_last_heartbeat_at=now_str(),
        lock_reason=_safe_text(lock_state.get("lock_reason")) or LOCK_REASON_WORKSPACE_CLAIM,
    )


def _can_user_operate_with_lock_state(
    lock_state: dict[str, Any],
    *,
    operator_user_id: str,
    is_admin: bool = False,
) -> bool:
    if not bool(lock_state.get("is_locked_effective")):
        return True
    if is_admin:
        return True
    return _safe_text(lock_state.get("lock_owner_user_id")) == _safe_text(operator_user_id)


def _build_candidate_snapshot(candidate_row, batch_created_at: str) -> dict[str, Any]:
    row_payload = _decode_dict(candidate_row["row_json"])
    detail_payload = _decode_dict(candidate_row["detail_json"])
    extract_info = _decode_dict(candidate_row["extract_info_json"], detail_payload.get("extract_info") or {})
    scores = _decode_dict(candidate_row["scores_json"], detail_payload.get("score_details") or {})
    lock_state = _normalize_lock_state_from_row(candidate_row)

    manual_decision = _safe_text(candidate_row["manual_decision"])
    manual_note = str(candidate_row["manual_note"] or "")
    manual_priority = (
        _safe_text(candidate_row["manual_priority"])
        or _safe_text(row_payload.get(ROW_KEY_MANUAL_PRIORITY))
        or _safe_text(detail_payload.get("manual_priority"))
        or "普通"
    )
    updated_at = _safe_text(candidate_row["updated_at"]) or _safe_text(batch_created_at)

    if manual_decision:
        row_payload[ROW_KEY_MANUAL_DECISION] = manual_decision
    if manual_note:
        row_payload[ROW_KEY_MANUAL_NOTE] = manual_note
    row_payload[ROW_KEY_MANUAL_PRIORITY] = manual_priority

    if "score_details" not in detail_payload and scores:
        detail_payload["score_details"] = scores
    if "extract_info" not in detail_payload and extract_info:
        detail_payload["extract_info"] = extract_info
    detail_payload["manual_decision"] = manual_decision
    detail_payload["manual_note"] = manual_note
    detail_payload["manual_priority"] = manual_priority
    detail_payload["updated_at"] = updated_at
    detail_payload["created_by_user_id"] = _safe_text(candidate_row["created_by_user_id"])
    detail_payload["created_by_name"] = _safe_text(candidate_row["created_by_name"])
    detail_payload["created_by_email"] = _safe_text(candidate_row["created_by_email"])
    detail_payload["last_operated_by_user_id"] = _safe_text(candidate_row["last_operated_by_user_id"])
    detail_payload["last_operated_by_name"] = _safe_text(candidate_row["last_operated_by_name"])
    detail_payload["last_operated_by_email"] = _safe_text(candidate_row["last_operated_by_email"])
    detail_payload["last_operated_at"] = _safe_text(candidate_row["last_operated_at"])
    _apply_lock_state_to_detail(detail_payload, lock_state)
    _apply_lock_state_to_row(row_payload, lock_state)

    return {
        "candidate_id": _safe_text(candidate_row["candidate_id"]),
        "screening_result": _safe_text(candidate_row["screening_result"]) or _safe_text(row_payload.get(ROW_KEY_SCREENING_RESULT)),
        "risk_level": _safe_text(candidate_row["risk_level"]) or _safe_text(row_payload.get(ROW_KEY_RISK_LEVEL)) or "unknown",
        "scores": scores,
        "review_summary": _safe_text(candidate_row["review_summary"]) or _safe_text(row_payload.get(ROW_KEY_REVIEW_SUMMARY)),
        "extract_info": extract_info,
        "manual_decision": manual_decision,
        "manual_note": manual_note,
        "manual_priority": manual_priority,
        "updated_at": updated_at,
        "row": row_payload,
        "detail": detail_payload,
    }


def _recount_batch_pools(conn, batch_id: str) -> None:
    rows = conn.execute(
        """
        SELECT manual_decision, candidate_pool, row_json
        FROM candidate_rows
        WHERE batch_id = ?
        ORDER BY id ASC
        """,
        (batch_id,),
    ).fetchall()

    pass_count = 0
    review_count = 0
    reject_count = 0
    for row in rows:
        manual_decision = _safe_text(row["manual_decision"])
        if manual_decision == "通过":
            pass_count += 1
            continue
        if manual_decision == "待复核":
            review_count += 1
            continue
        if manual_decision == "淘汰":
            reject_count += 1
            continue

        row_payload = _decode_dict(row["row_json"])
        candidate_pool = _safe_text(row_payload.get(ROW_KEY_CANDIDATE_POOL)) or _safe_text(row["candidate_pool"])
        if candidate_pool == "通过候选人":
            pass_count += 1
        elif candidate_pool == "待复核候选人":
            review_count += 1
        elif candidate_pool == "淘汰候选人":
            reject_count += 1

    conn.execute(
        """
        UPDATE candidate_batches
        SET candidate_count = ?,
            pass_count = ?,
            review_count = ?,
            reject_count = ?
        WHERE batch_id = ?
        """,
        (len(rows), pass_count, review_count, reject_count, batch_id),
    )


def save_candidate_batch(
    jd_title: str,
    rows: list[dict],
    details: dict[str, dict],
    created_by_user_id: str = "",
    created_by_name: str = "",
    created_by_email: str = "",
) -> str:
    batch_id = f"batch-{uuid4().hex}"
    clean_jd_title = _safe_text(jd_title) or "未命名岗位"
    created_at = now_str()
    creator_user_id = _safe_text(created_by_user_id)
    creator_name = _safe_text(created_by_name)
    creator_email = _safe_text(created_by_email)

    safe_rows = [row for row in (rows or []) if isinstance(row, dict)]
    safe_details = details if isinstance(details, dict) else {}

    pass_count = sum(1 for row in safe_rows if _safe_text(row.get(ROW_KEY_CANDIDATE_POOL)) == "通过候选人")
    review_count = sum(1 for row in safe_rows if _safe_text(row.get(ROW_KEY_CANDIDATE_POOL)) == "待复核候选人")
    reject_count = sum(1 for row in safe_rows if _safe_text(row.get(ROW_KEY_CANDIDATE_POOL)) == "淘汰候选人")

    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO candidate_batches(
                    batch_id, jd_title, created_by_user_id, created_by_name, created_by_email, created_at,
                    total_resumes, candidate_count, pass_count, review_count, reject_count
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    clean_jd_title,
                    creator_user_id,
                    creator_name,
                    creator_email,
                    created_at,
                    len(safe_rows),
                    len(safe_rows),
                    pass_count,
                    review_count,
                    reject_count,
                ),
            )

            for row in safe_rows:
                candidate_id = _safe_candidate_id(row.get("candidate_id"))
                row["candidate_id"] = candidate_id
                row.setdefault(ROW_KEY_LOCK_STATUS, "未领取")
                row.setdefault(ROW_KEY_LOCK_OWNER, "-")
                row.setdefault(ROW_KEY_LOCK_EXPIRES_AT, "-")

                detail_payload = safe_details.get(candidate_id, {})
                if not isinstance(detail_payload, dict):
                    detail_payload = {}
                safe_details[candidate_id] = detail_payload

                detail_payload.setdefault("lock_status", LOCK_STATUS_UNLOCKED)
                detail_payload.setdefault("lock_owner_user_id", "")
                detail_payload.setdefault("lock_owner_name", "")
                detail_payload.setdefault("lock_owner_email", "")
                detail_payload.setdefault("lock_acquired_at", "")
                detail_payload.setdefault("lock_expires_at", "")
                detail_payload.setdefault("lock_last_heartbeat_at", "")
                detail_payload.setdefault("lock_reason", "")
                detail_payload.setdefault("is_locked_effective", False)
                detail_payload.setdefault("is_expired", False)

                extract_info = detail_payload.get("extract_info")
                if not isinstance(extract_info, dict):
                    extract_info = {}
                scores = detail_payload.get("score_details")
                if not isinstance(scores, dict):
                    scores = {}

                screening_result = _safe_text(row.get(ROW_KEY_SCREENING_RESULT))
                risk_level = _safe_text(row.get(ROW_KEY_RISK_LEVEL)) or "unknown"
                candidate_pool = _safe_text(row.get(ROW_KEY_CANDIDATE_POOL))
                review_id = _safe_text(detail_payload.get("review_id"))
                updated_at = _safe_text(detail_payload.get("updated_at")) or created_at

                conn.execute(
                    """
                    INSERT INTO candidate_rows(
                        batch_id, candidate_id, candidate_name, source_name, parse_status, screening_result,
                        risk_level, candidate_pool, manual_decision, manual_note, manual_priority, review_summary,
                        scores_json, extract_info_json, row_json, detail_json,
                        created_by_user_id, created_by_name, created_by_email,
                        last_operated_by_user_id, last_operated_by_name, last_operated_by_email, last_operated_at,
                        created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        candidate_id,
                        _safe_text(row.get(ROW_KEY_NAME)) or _safe_text((detail_payload.get("parsed_resume") or {}).get("name")),
                        _safe_text(row.get(ROW_KEY_FILE_NAME)) or _safe_text(extract_info.get("file_name")),
                        _safe_text(row.get(ROW_KEY_PARSE_STATUS)) or _safe_text(extract_info.get("parse_status")),
                        screening_result,
                        risk_level,
                        candidate_pool,
                        _safe_text(row.get(ROW_KEY_MANUAL_DECISION)) or _safe_text(detail_payload.get("manual_decision")),
                        str(row.get(ROW_KEY_MANUAL_NOTE) or detail_payload.get("manual_note") or ""),
                        _safe_text(row.get(ROW_KEY_MANUAL_PRIORITY)) or _safe_text(detail_payload.get("manual_priority")) or "普通",
                        _safe_text(row.get(ROW_KEY_REVIEW_SUMMARY)),
                        json_dumps(scores),
                        json_dumps(extract_info),
                        json_dumps(row),
                        json_dumps(detail_payload),
                        creator_user_id,
                        creator_name,
                        creator_email,
                        "",
                        "",
                        "",
                        "",
                        created_at,
                        updated_at,
                    ),
                )

                _append_candidate_action_log(
                    conn,
                    batch_id=batch_id,
                    candidate_id=candidate_id,
                    review_id=review_id,
                    jd_title=clean_jd_title,
                    action_type="batch_candidate_created",
                    operator_user_id=creator_user_id,
                    operator_name=creator_name,
                    operator_email=creator_email,
                    before_payload={},
                    after_payload={
                        "candidate_id": candidate_id,
                        "screening_result": screening_result,
                        "risk_level": risk_level,
                        "candidate_pool": candidate_pool,
                    },
                    extra_payload={"source": "batch_screening"},
                )
        return batch_id
    finally:
        conn.close()


def list_jd_titles() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT DISTINCT jd_title FROM candidate_batches ORDER BY jd_title ASC").fetchall()
        return [_safe_text(row["jd_title"]) or "未命名岗位" for row in rows]
    finally:
        conn.close()


def list_batches_by_jd(jd_title: str) -> list[dict]:
    clean_title = _safe_text(jd_title)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT batch_id, jd_title, created_by_user_id, created_by_name, created_by_email, created_at,
                   candidate_count, total_resumes, pass_count, review_count, reject_count
            FROM candidate_batches
            WHERE jd_title = ?
            ORDER BY created_at DESC, batch_id DESC
            """,
            (clean_title,),
        ).fetchall()
        return [
            {
                "batch_id": _safe_text(row["batch_id"]),
                "jd_title": _safe_text(row["jd_title"]),
                "created_by_user_id": _safe_text(row["created_by_user_id"]),
                "created_by_name": _safe_text(row["created_by_name"]),
                "created_by_email": _safe_text(row["created_by_email"]),
                "created_at": _safe_text(row["created_at"]),
                "candidate_count": _safe_int(row["candidate_count"], 0),
                "total_resumes": _safe_int(row["total_resumes"], _safe_int(row["candidate_count"], 0)),
                "pass_count": _safe_int(row["pass_count"], 0),
                "review_count": _safe_int(row["review_count"], 0),
                "reject_count": _safe_int(row["reject_count"], 0),
            }
            for row in rows
        ]
    finally:
        conn.close()


def load_batch(batch_id: str) -> dict | None:
    key = _safe_text(batch_id)
    if not key:
        return None

    conn = get_connection()
    try:
        batch = conn.execute(
            """
            SELECT batch_id, jd_title, created_by_user_id, created_by_name, created_by_email, created_at,
                   total_resumes, candidate_count, pass_count, review_count, reject_count
            FROM candidate_batches
            WHERE batch_id = ?
            """,
            (key,),
        ).fetchone()
        if batch is None:
            return None

        candidate_rows = conn.execute(
            """
            SELECT *
            FROM candidate_rows
            WHERE batch_id = ?
            ORDER BY id ASC
            """,
            (key,),
        ).fetchall()

        rows: list[dict] = []
        details: dict[str, dict] = {}
        candidates: list[dict] = []
        for candidate_row in candidate_rows:
            snapshot = _build_candidate_snapshot(candidate_row, _safe_text(batch["created_at"]))
            candidates.append(snapshot)
            rows.append(snapshot["row"])
            details[snapshot["candidate_id"]] = snapshot["detail"]

        return {
            "batch_id": _safe_text(batch["batch_id"]),
            "jd_title": _safe_text(batch["jd_title"]),
            "created_by_user_id": _safe_text(batch["created_by_user_id"]),
            "created_by_name": _safe_text(batch["created_by_name"]),
            "created_by_email": _safe_text(batch["created_by_email"]),
            "created_at": _safe_text(batch["created_at"]),
            "total_resumes": _safe_int(batch["total_resumes"], len(candidates)),
            "pass_count": _safe_int(batch["pass_count"], 0),
            "review_count": _safe_int(batch["review_count"], 0),
            "reject_count": _safe_int(batch["reject_count"], 0),
            "candidates": candidates,
            "rows": rows,
            "details": details,
        }
    finally:
        conn.close()


def load_latest_batch_by_jd(jd_title: str) -> dict | None:
    batches = list_batches_by_jd(jd_title)
    if not batches:
        return None
    return load_batch(batches[0].get("batch_id", ""))


def delete_batch(batch_id: str) -> bool:
    bid = _safe_text(batch_id)
    if not bid:
        return False

    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM candidate_batches WHERE batch_id = ?", (bid,))
        return bool(cursor.rowcount)
    finally:
        conn.close()


def delete_batches_by_jd(jd_title: str) -> int:
    target = _safe_text(jd_title)
    if not target:
        return 0

    conn = get_connection()
    try:
        with conn:
            rows = conn.execute("SELECT batch_id FROM candidate_batches WHERE jd_title = ?", (target,)).fetchall()
            batch_ids = [_safe_text(row["batch_id"]) for row in rows]
            if not batch_ids:
                return 0
            conn.execute("DELETE FROM candidate_batches WHERE jd_title = ?", (target,))
        return len(batch_ids)
    finally:
        conn.close()


def get_candidate_lock_state(batch_id: str, candidate_id: str) -> dict[str, Any] | None:
    bid = _safe_text(batch_id)
    cid = _safe_text(candidate_id)
    if not bid or not cid:
        return None

    conn = get_connection()
    try:
        row = _fetch_candidate_row(conn, bid, cid)
        if row is None:
            return None
        return _normalize_lock_state_from_row(row)
    finally:
        conn.close()


def acquire_candidate_lock(
    batch_id: str,
    candidate_id: str,
    *,
    operator_user_id: str,
    operator_name: str,
    operator_email: str,
    ttl_minutes: int = DEFAULT_LOCK_TTL_MINUTES,
    force: bool = False,
) -> tuple[bool, dict[str, Any]]:
    bid = _safe_text(batch_id)
    cid = _safe_text(candidate_id)
    user_id = _safe_text(operator_user_id)
    if not bid or not cid:
        return False, {"reason": "invalid_target"}
    if not user_id:
        return False, {"reason": "invalid_operator"}

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _fetch_candidate_row(conn, bid, cid)
        if row is None:
            conn.rollback()
            return False, {"reason": "not_found", "batch_id": bid, "candidate_id": cid}

        lock_state = _normalize_lock_state_from_row(row)
        owner_user_id = _safe_text(lock_state.get("lock_owner_user_id"))
        if lock_state["is_locked_effective"] and owner_user_id and owner_user_id != user_id and not force:
            conn.rollback()
            lock_state["reason"] = "locked_by_other"
            return False, lock_state

        now_value = now_str()
        acquired_at = now_value
        if lock_state["is_locked_effective"] and owner_user_id == user_id:
            acquired_at = _safe_text(lock_state.get("lock_acquired_at")) or now_value

        _write_lock_fields(
            conn,
            batch_id=bid,
            candidate_id=cid,
            lock_status=LOCK_STATUS_LOCKED,
            lock_owner_user_id=user_id,
            lock_owner_name=_safe_text(operator_name),
            lock_owner_email=_safe_text(operator_email),
            lock_acquired_at=acquired_at,
            lock_expires_at=_lock_expires_at(ttl_minutes),
            lock_last_heartbeat_at=now_value,
            lock_reason=LOCK_REASON_WORKSPACE_CLAIM,
        )

        updated_row = _fetch_candidate_row(conn, bid, cid)
        new_state = _normalize_lock_state_from_row(updated_row)
        previous_review_id = _safe_text(_decode_dict(row["detail_json"]).get("review_id"))
        if owner_user_id != user_id:
            _append_candidate_action_log(
                conn,
                batch_id=bid,
                candidate_id=cid,
                review_id=previous_review_id,
                jd_title="",
                action_type="candidate_lock_acquired",
                operator_user_id=user_id,
                operator_name=_safe_text(operator_name),
                operator_email=_safe_text(operator_email),
                before_payload={"lock": lock_state},
                after_payload={"lock": new_state},
                extra_payload={"force": bool(force), "source": "workspace_claim"},
            )
        conn.commit()
        return True, new_state
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def refresh_candidate_lock(
    batch_id: str,
    candidate_id: str,
    *,
    operator_user_id: str,
    ttl_minutes: int = DEFAULT_LOCK_TTL_MINUTES,
    force: bool = False,
) -> bool:
    bid = _safe_text(batch_id)
    cid = _safe_text(candidate_id)
    user_id = _safe_text(operator_user_id)
    if not bid or not cid or not user_id:
        return False

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _fetch_candidate_row(conn, bid, cid)
        if row is None:
            conn.rollback()
            return False

        lock_state = _normalize_lock_state_from_row(row)
        if not bool(lock_state.get("is_locked_effective")):
            conn.rollback()
            return False

        owner_user_id = _safe_text(lock_state.get("lock_owner_user_id"))
        if owner_user_id != user_id and not force:
            conn.rollback()
            return False

        _write_lock_fields(
            conn,
            batch_id=bid,
            candidate_id=cid,
            lock_status=LOCK_STATUS_LOCKED,
            lock_owner_user_id=owner_user_id,
            lock_owner_name=_safe_text(lock_state.get("lock_owner_name")),
            lock_owner_email=_safe_text(lock_state.get("lock_owner_email")),
            lock_acquired_at=_safe_text(lock_state.get("lock_acquired_at")) or now_str(),
            lock_expires_at=_lock_expires_at(ttl_minutes),
            lock_last_heartbeat_at=now_str(),
            lock_reason=_safe_text(lock_state.get("lock_reason")) or LOCK_REASON_WORKSPACE_CLAIM,
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_candidate_lock(
    batch_id: str,
    candidate_id: str,
    *,
    operator_user_id: str,
    operator_name: str = "",
    operator_email: str = "",
    is_admin: bool = False,
    force: bool = False,
) -> tuple[bool, str]:
    bid = _safe_text(batch_id)
    cid = _safe_text(candidate_id)
    user_id = _safe_text(operator_user_id)
    operator_name_safe = _safe_text(operator_name)
    operator_email_safe = _safe_text(operator_email)
    if not bid or not cid or not user_id:
        return False, "锁目标或操作人无效。"

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _fetch_candidate_row(conn, bid, cid)
        if row is None:
            conn.rollback()
            return False, "未找到对应候选人。"

        lock_state = _normalize_lock_state_from_row(row)
        has_lock_metadata = bool(
            _safe_text(row["lock_owner_user_id"])
            or _safe_text(row["lock_acquired_at"])
            or _safe_text(row["lock_expires_at"])
            or _safe_text(row["lock_last_heartbeat_at"])
        )
        owner_user_id = _safe_text(lock_state.get("lock_owner_user_id"))
        if bool(lock_state.get("is_locked_effective")):
            can_release = owner_user_id == user_id or (is_admin and force)
            if not can_release:
                conn.rollback()
                return False, "当前候选人已被其他 HR 锁定，暂不可释放。"

        _write_lock_fields(
            conn,
            batch_id=bid,
            candidate_id=cid,
            lock_status=LOCK_STATUS_UNLOCKED,
            lock_owner_user_id="",
            lock_owner_name="",
            lock_owner_email="",
            lock_acquired_at="",
            lock_expires_at="",
            lock_last_heartbeat_at="",
            lock_reason="",
        )

        _append_candidate_action_log(
            conn,
            batch_id=bid,
            candidate_id=cid,
            review_id=_safe_text(_decode_dict(row["detail_json"]).get("review_id")),
            jd_title="",
            action_type="candidate_lock_force_released" if (is_admin and force and owner_user_id and owner_user_id != user_id) else "candidate_lock_released",
            operator_user_id=user_id,
            operator_name=operator_name_safe,
            operator_email=operator_email_safe,
            before_payload={"lock": lock_state},
            after_payload={"lock": _normalize_lock_state_from_row(_fetch_candidate_row(conn, bid, cid))},
            extra_payload={"force": bool(force), "source": "workspace_release"},
        )
        conn.commit()

        if not bool(lock_state.get("is_locked_effective")):
            if has_lock_metadata:
                return True, "当前锁记录已清理。"
            return True, "当前候选人已无有效锁。"
        if is_admin and force and owner_user_id and owner_user_id != user_id:
            return True, "已管理员强制解锁。"
        return True, "已释放当前候选人的协作锁。"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def can_user_operate_candidate(
    batch_id: str,
    candidate_id: str,
    *,
    operator_user_id: str,
    is_admin: bool = False,
) -> tuple[bool, dict[str, Any]]:
    lock_state = get_candidate_lock_state(batch_id, candidate_id)
    if lock_state is None:
        return False, {"reason": "not_found", "batch_id": _safe_text(batch_id), "candidate_id": _safe_text(candidate_id)}
    allowed = _can_user_operate_with_lock_state(
        lock_state,
        operator_user_id=operator_user_id,
        is_admin=is_admin,
    )
    return allowed, lock_state


def list_batch_candidate_lock_states(batch_id: str) -> list[dict[str, Any]]:
    bid = _safe_text(batch_id)
    if not bid:
        return []

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM candidate_rows
            WHERE batch_id = ?
            ORDER BY id ASC
            """,
            (bid,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            lock_state = _normalize_lock_state_from_row(row)
            result.append(
                {
                    "batch_id": bid,
                    "candidate_id": _safe_text(row["candidate_id"]),
                    "candidate_name": _safe_text(row["candidate_name"]),
                    "source_name": _safe_text(row["source_name"]),
                    "lock_status": _safe_text(lock_state.get("lock_status")) or LOCK_STATUS_UNLOCKED,
                    "lock_owner_user_id": _safe_text(lock_state.get("lock_owner_user_id")),
                    "lock_owner_name": _safe_text(lock_state.get("lock_owner_name")),
                    "lock_owner_email": _safe_text(lock_state.get("lock_owner_email")),
                    "lock_acquired_at": _safe_text(lock_state.get("lock_acquired_at")),
                    "lock_expires_at": _safe_text(lock_state.get("lock_expires_at")),
                    "lock_last_heartbeat_at": _safe_text(lock_state.get("lock_last_heartbeat_at")),
                    "lock_reason": _safe_text(lock_state.get("lock_reason")),
                    "is_expired": bool(lock_state.get("is_expired")),
                    "is_locked_effective": bool(lock_state.get("is_locked_effective")),
                    "has_lock_metadata": bool(
                        _safe_text(row["lock_owner_user_id"])
                        or _safe_text(row["lock_acquired_at"])
                        or _safe_text(row["lock_expires_at"])
                        or _safe_text(row["lock_last_heartbeat_at"])
                    ),
                }
            )
        return result
    finally:
        conn.close()


def list_recent_lock_events(
    batch_id: str,
    *,
    limit: int = 30,
    force_only: bool = False,
) -> list[dict[str, Any]]:
    bid = _safe_text(batch_id)
    if not bid:
        return []

    try:
        limit_value = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit_value = 30

    query = """
        SELECT action_id,
               batch_id,
               candidate_id,
               review_id,
               jd_title,
               action_type,
               operator_user_id,
               operator_name,
               operator_email,
               before_json,
               after_json,
               extra_json,
               created_at
        FROM candidate_action_logs
        WHERE batch_id = ?
          AND action_type IN ('candidate_lock_acquired', 'candidate_lock_released', 'candidate_lock_force_released')
    """
    params: list[Any] = [bid]
    if force_only:
        query += " AND action_type = 'candidate_lock_force_released'"
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit_value)

    conn = get_connection()
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "action_id": _safe_text(row["action_id"]),
                    "batch_id": _safe_text(row["batch_id"]),
                    "candidate_id": _safe_text(row["candidate_id"]),
                    "review_id": _safe_text(row["review_id"]),
                    "jd_title": _safe_text(row["jd_title"]),
                    "action_type": _safe_text(row["action_type"]),
                    "operator_user_id": _safe_text(row["operator_user_id"]),
                    "operator_name": _safe_text(row["operator_name"]),
                    "operator_email": _safe_text(row["operator_email"]),
                    "before_json": _decode_dict(row["before_json"]),
                    "after_json": _decode_dict(row["after_json"]),
                    "extra_json": _decode_dict(row["extra_json"]),
                    "created_at": _safe_text(row["created_at"]),
                }
            )
        return result
    finally:
        conn.close()


def cleanup_expired_candidate_locks(
    batch_id: str,
    *,
    operator_user_id: str,
    operator_name: str = "",
    operator_email: str = "",
    is_admin: bool = False,
) -> int:
    bid = _safe_text(batch_id)
    operator_id = _safe_text(operator_user_id)
    if not bid or not operator_id or not is_admin:
        return 0

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT *
            FROM candidate_rows
            WHERE batch_id = ?
            ORDER BY id ASC
            """,
            (bid,),
        ).fetchall()
        cleaned_count = 0
        for row in rows:
            lock_state = _normalize_lock_state_from_row(row)
            has_lock_metadata = bool(
                _safe_text(row["lock_owner_user_id"])
                or _safe_text(row["lock_acquired_at"])
                or _safe_text(row["lock_expires_at"])
                or _safe_text(row["lock_last_heartbeat_at"])
            )
            should_clear = bool(lock_state.get("is_expired")) or (
                has_lock_metadata and not bool(lock_state.get("is_locked_effective"))
            )
            if not should_clear:
                continue

            _write_lock_fields(
                conn,
                batch_id=bid,
                candidate_id=_safe_text(row["candidate_id"]),
                lock_status=LOCK_STATUS_UNLOCKED,
                lock_owner_user_id="",
                lock_owner_name="",
                lock_owner_email="",
                lock_acquired_at="",
                lock_expires_at="",
                lock_last_heartbeat_at="",
                lock_reason="",
            )
            _append_candidate_action_log(
                conn,
                batch_id=bid,
                candidate_id=_safe_text(row["candidate_id"]),
                review_id=_safe_text(_decode_dict(row["detail_json"]).get("review_id")),
                jd_title="",
                action_type="candidate_lock_force_released",
                operator_user_id=operator_id,
                operator_name=_safe_text(operator_name),
                operator_email=_safe_text(operator_email),
                before_payload={"lock": lock_state},
                after_payload={"lock": _normalize_lock_state_from_row(_fetch_candidate_row(conn, bid, _safe_text(row["candidate_id"])))},
                extra_payload={"force": True, "source": "admin_expired_cleanup"},
            )
            cleaned_count += 1

        conn.commit()
        return cleaned_count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_candidate_manual_review(
    batch_id: str,
    candidate_id: str,
    manual_decision: str | None = None,
    manual_note: str | None = None,
    manual_priority: str | None = None,
    operator_user_id: str = "",
    operator_name: str = "",
    operator_email: str = "",
    review_id: str = "",
    jd_title: str = "",
    source: str = "workspace",
    is_admin: bool = False,
    enforce_lock: bool = True,
) -> bool:
    bid = _safe_text(batch_id)
    cid = _safe_text(candidate_id)
    if not bid or not cid:
        return False

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _fetch_candidate_row(conn, bid, cid)
        if current is None:
            conn.rollback()
            return False

        lock_state = _normalize_lock_state_from_row(current)
        if enforce_lock and not _can_user_operate_with_lock_state(
            lock_state,
            operator_user_id=operator_user_id,
            is_admin=is_admin,
        ):
            conn.rollback()
            return False

        row_payload = _decode_dict(current["row_json"])
        detail_payload = _decode_dict(current["detail_json"])
        current_state = _candidate_state_payload(
            manual_decision=_safe_text(current["manual_decision"]),
            manual_note=str(current["manual_note"] or ""),
            manual_priority=(
                _safe_text(current["manual_priority"])
                or _safe_text(row_payload.get(ROW_KEY_MANUAL_PRIORITY))
                or _safe_text(detail_payload.get("manual_priority"))
                or "普通"
            ),
        )
        next_state = dict(current_state)
        changed_fields: list[str] = []

        if manual_decision is not None:
            value = _safe_text(manual_decision)
            if value != current_state["manual_decision"]:
                changed_fields.append("manual_decision")
            next_state["manual_decision"] = value
            detail_payload["manual_decision"] = value
            row_payload[ROW_KEY_MANUAL_DECISION] = value
            manual_pool = _manual_pool_label(value)
            if manual_pool:
                row_payload[ROW_KEY_CANDIDATE_POOL] = manual_pool
        if manual_note is not None:
            value = str(manual_note or "")
            if value != current_state["manual_note"]:
                changed_fields.append("manual_note")
            next_state["manual_note"] = value
            detail_payload["manual_note"] = value
            row_payload[ROW_KEY_MANUAL_NOTE] = value
        if manual_priority is not None:
            value = _safe_text(manual_priority) or "普通"
            if value != current_state["manual_priority"]:
                changed_fields.append("manual_priority")
            next_state["manual_priority"] = value
            detail_payload["manual_priority"] = value
            row_payload[ROW_KEY_MANUAL_PRIORITY] = value

        if not changed_fields:
            conn.rollback()
            return True

        updated_at = now_str()
        last_operated_at = updated_at
        manual_pool = _manual_pool_label(next_state["manual_decision"])
        next_candidate_pool = manual_pool or _safe_text(row_payload.get(ROW_KEY_CANDIDATE_POOL)) or _safe_text(current["candidate_pool"])

        detail_payload["manual_decision"] = next_state["manual_decision"]
        detail_payload["manual_note"] = next_state["manual_note"]
        detail_payload["manual_priority"] = next_state["manual_priority"]
        detail_payload["updated_at"] = updated_at
        detail_payload["last_operated_by_user_id"] = _safe_text(operator_user_id)
        detail_payload["last_operated_by_name"] = _safe_text(operator_name)
        detail_payload["last_operated_by_email"] = _safe_text(operator_email)
        detail_payload["last_operated_at"] = last_operated_at
        _apply_lock_state_to_detail(detail_payload, lock_state)

        row_payload[ROW_KEY_MANUAL_DECISION] = next_state["manual_decision"]
        row_payload[ROW_KEY_MANUAL_NOTE] = next_state["manual_note"]
        row_payload[ROW_KEY_MANUAL_PRIORITY] = next_state["manual_priority"]
        if next_candidate_pool:
            row_payload[ROW_KEY_CANDIDATE_POOL] = next_candidate_pool
        _apply_lock_state_to_row(row_payload, lock_state)

        conn.execute(
            """
            UPDATE candidate_rows
            SET manual_decision = ?,
                manual_note = ?,
                manual_priority = ?,
                candidate_pool = ?,
                row_json = ?,
                detail_json = ?,
                last_operated_by_user_id = ?,
                last_operated_by_name = ?,
                last_operated_by_email = ?,
                last_operated_at = ?,
                updated_at = ?
            WHERE batch_id = ? AND candidate_id = ?
            """,
            (
                next_state["manual_decision"],
                next_state["manual_note"],
                next_state["manual_priority"],
                next_candidate_pool,
                json_dumps(row_payload),
                json_dumps(detail_payload),
                _safe_text(operator_user_id),
                _safe_text(operator_name),
                _safe_text(operator_email),
                last_operated_at,
                updated_at,
                bid,
                cid,
            ),
        )
        _maybe_refresh_owned_lock(
            conn,
            batch_id=bid,
            candidate_id=cid,
            operator_user_id=operator_user_id,
            ttl_minutes=DEFAULT_LOCK_TTL_MINUTES,
        )
        _recount_batch_pools(conn, bid)
        _append_candidate_action_log(
            conn,
            batch_id=bid,
            candidate_id=cid,
            review_id=_safe_text(review_id),
            jd_title=_safe_text(jd_title),
            action_type=_determine_action_type(changed_fields),
            operator_user_id=_safe_text(operator_user_id),
            operator_name=_safe_text(operator_name),
            operator_email=_safe_text(operator_email),
            before_payload=current_state,
            after_payload=next_state,
            extra_payload={
                "changed_fields": changed_fields,
                "source": _safe_text(source) or "workspace",
            },
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def persist_candidate_snapshot(
    batch_id: str,
    candidate_id: str,
    row_payload: dict[str, Any],
    detail_payload: dict[str, Any],
    *,
    operator_user_id: str = "",
    operator_name: str = "",
    operator_email: str = "",
    is_admin: bool = False,
    enforce_lock: bool = True,
) -> bool:
    bid = _safe_text(batch_id)
    cid = _safe_text(candidate_id)
    if not bid or not cid:
        return False
    if not isinstance(row_payload, dict) or not isinstance(detail_payload, dict):
        return False

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _fetch_candidate_row(conn, bid, cid)
        if current is None:
            conn.rollback()
            return False

        lock_state = _normalize_lock_state_from_row(current)
        if enforce_lock and not _can_user_operate_with_lock_state(
            lock_state,
            operator_user_id=operator_user_id,
            is_admin=is_admin,
        ):
            conn.rollback()
            return False

        extract_info = detail_payload.get("extract_info")
        if not isinstance(extract_info, dict):
            extract_info = {}
        scores = detail_payload.get("score_details")
        if not isinstance(scores, dict):
            scores = {}

        manual_decision = (
            _safe_text(row_payload.get(ROW_KEY_MANUAL_DECISION))
            or _safe_text(detail_payload.get("manual_decision"))
            or _safe_text(current["manual_decision"])
        )
        manual_note = str(
            row_payload.get(ROW_KEY_MANUAL_NOTE)
            or detail_payload.get("manual_note")
            or current["manual_note"]
            or ""
        )
        manual_priority = (
            _safe_text(row_payload.get(ROW_KEY_MANUAL_PRIORITY))
            or _safe_text(detail_payload.get("manual_priority"))
            or _safe_text(current["manual_priority"])
            or "普通"
        )
        screening_result = (
            _safe_text(row_payload.get(ROW_KEY_SCREENING_RESULT))
            or _safe_text((detail_payload.get("screening_result") or {}).get("screening_result"))
            or _safe_text(current["screening_result"])
        )
        risk_result = detail_payload.get("risk_result")
        if not isinstance(risk_result, dict):
            risk_result = {}
        risk_level = (
            _safe_text(row_payload.get(ROW_KEY_RISK_LEVEL))
            or _safe_text(risk_result.get("risk_level"))
            or _safe_text(current["risk_level"])
            or "unknown"
        )
        review_summary = (
            _safe_text(row_payload.get(ROW_KEY_REVIEW_SUMMARY))
            or _safe_text(detail_payload.get("review_summary"))
            or _safe_text(current["review_summary"])
        )
        candidate_pool = _safe_text(row_payload.get(ROW_KEY_CANDIDATE_POOL)) or _safe_text(current["candidate_pool"])
        updated_at = now_str()

        detail_payload["manual_decision"] = manual_decision
        detail_payload["manual_note"] = manual_note
        detail_payload["manual_priority"] = manual_priority
        detail_payload["updated_at"] = updated_at
        detail_payload["review_summary"] = review_summary
        detail_payload["created_by_user_id"] = _safe_text(current["created_by_user_id"])
        detail_payload["created_by_name"] = _safe_text(current["created_by_name"])
        detail_payload["created_by_email"] = _safe_text(current["created_by_email"])
        detail_payload["last_operated_by_user_id"] = _safe_text(current["last_operated_by_user_id"])
        detail_payload["last_operated_by_name"] = _safe_text(current["last_operated_by_name"])
        detail_payload["last_operated_by_email"] = _safe_text(current["last_operated_by_email"])
        detail_payload["last_operated_at"] = _safe_text(current["last_operated_at"])
        _apply_lock_state_to_detail(detail_payload, lock_state)

        row_payload[ROW_KEY_MANUAL_DECISION] = manual_decision
        row_payload[ROW_KEY_MANUAL_NOTE] = manual_note
        row_payload[ROW_KEY_MANUAL_PRIORITY] = manual_priority
        row_payload[ROW_KEY_SCREENING_RESULT] = screening_result
        row_payload[ROW_KEY_RISK_LEVEL] = risk_level
        row_payload[ROW_KEY_REVIEW_SUMMARY] = review_summary
        row_payload[ROW_KEY_CANDIDATE_POOL] = candidate_pool
        _apply_lock_state_to_row(row_payload, lock_state)

        conn.execute(
            """
            UPDATE candidate_rows
            SET candidate_name = ?,
                source_name = ?,
                parse_status = ?,
                screening_result = ?,
                risk_level = ?,
                candidate_pool = ?,
                manual_decision = ?,
                manual_note = ?,
                manual_priority = ?,
                review_summary = ?,
                scores_json = ?,
                extract_info_json = ?,
                row_json = ?,
                detail_json = ?,
                updated_at = ?
            WHERE batch_id = ? AND candidate_id = ?
            """,
            (
                _safe_text(row_payload.get(ROW_KEY_NAME))
                or _safe_text((detail_payload.get("parsed_resume") or {}).get("name"))
                or _safe_text(current["candidate_name"]),
                _safe_text(row_payload.get(ROW_KEY_FILE_NAME))
                or _safe_text(extract_info.get("file_name"))
                or _safe_text(current["source_name"]),
                _safe_text(row_payload.get(ROW_KEY_PARSE_STATUS))
                or _safe_text(extract_info.get("parse_status"))
                or _safe_text(current["parse_status"]),
                screening_result,
                risk_level,
                candidate_pool,
                manual_decision,
                manual_note,
                manual_priority,
                review_summary,
                json_dumps(scores),
                json_dumps(extract_info),
                json_dumps(row_payload),
                json_dumps(detail_payload),
                updated_at,
                bid,
                cid,
            ),
        )
        _maybe_refresh_owned_lock(
            conn,
            batch_id=bid,
            candidate_id=cid,
            operator_user_id=operator_user_id,
            ttl_minutes=DEFAULT_LOCK_TTL_MINUTES,
        )
        _recount_batch_pools(conn, bid)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
