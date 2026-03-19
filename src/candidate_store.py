"""岗位级候选池存储（SQLite 版）。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .sqlite_store import get_connection, json_dumps, json_loads, now_str


def _safe_candidate_id(value: Any) -> str:
    candidate_id = str(value or "").strip()
    if candidate_id:
        return candidate_id
    return f"cand-{uuid4().hex[:8]}"


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


def _build_candidate_snapshot(candidate_row, batch_created_at: str) -> dict[str, Any]:
    row_payload = _decode_dict(candidate_row["row_json"])
    detail_payload = _decode_dict(candidate_row["detail_json"])
    extract_info = _decode_dict(candidate_row["extract_info_json"], detail_payload.get("extract_info") or {})
    scores = _decode_dict(candidate_row["scores_json"], detail_payload.get("score_details") or {})

    manual_decision = str(candidate_row["manual_decision"] or "")
    manual_note = str(candidate_row["manual_note"] or "")
    manual_priority = str(
        candidate_row["manual_priority"]
        or row_payload.get("处理优先级")
        or detail_payload.get("manual_priority")
        or "普通"
    )
    updated_at = str(candidate_row["updated_at"] or batch_created_at or "")

    if manual_decision:
        row_payload["人工最终结论"] = manual_decision
    if manual_note:
        row_payload["人工备注"] = manual_note
    row_payload["处理优先级"] = manual_priority

    if "score_details" not in detail_payload and scores:
        detail_payload["score_details"] = scores
    if "extract_info" not in detail_payload and extract_info:
        detail_payload["extract_info"] = extract_info
    detail_payload["manual_decision"] = manual_decision
    detail_payload["manual_note"] = manual_note
    detail_payload["manual_priority"] = manual_priority
    detail_payload["updated_at"] = updated_at

    return {
        "candidate_id": str(candidate_row["candidate_id"] or ""),
        "screening_result": str(candidate_row["screening_result"] or row_payload.get("初筛结论") or ""),
        "risk_level": str(candidate_row["risk_level"] or row_payload.get("风险等级") or "unknown"),
        "scores": scores,
        "review_summary": str(candidate_row["review_summary"] or row_payload.get("审核摘要") or ""),
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
        manual_decision = str(row["manual_decision"] or "").strip()
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
        candidate_pool = str(row_payload.get("候选池") or row["candidate_pool"] or "").strip()
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


def save_candidate_batch(jd_title: str, rows: list[dict], details: dict[str, dict]) -> str:
    """保存一次批量初筛结果，返回 batch_id。"""
    batch_id = f"batch-{uuid4().hex}"
    clean_jd_title = (jd_title or "").strip() or "未命名岗位"
    created_at = now_str()

    safe_rows = [row for row in (rows or []) if isinstance(row, dict)]
    safe_details = details if isinstance(details, dict) else {}

    pass_count = sum(1 for row in safe_rows if row.get("初筛结论") == "推荐进入下一轮")
    review_count = sum(1 for row in safe_rows if row.get("初筛结论") == "建议人工复核")
    reject_count = sum(1 for row in safe_rows if row.get("初筛结论") == "暂不推荐")

    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO candidate_batches(
                    batch_id, jd_title, created_at, total_resumes, candidate_count, pass_count, review_count, reject_count
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    clean_jd_title,
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
                detail_payload = safe_details.get(candidate_id, {})
                if not isinstance(detail_payload, dict):
                    detail_payload = {}
                extract_info = detail_payload.get("extract_info")
                if not isinstance(extract_info, dict):
                    extract_info = {}
                scores = detail_payload.get("score_details")
                if not isinstance(scores, dict):
                    scores = {}

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
                        batch_id,
                        candidate_id,
                        str(row.get("姓名") or detail_payload.get("parsed_resume", {}).get("name") or ""),
                        str(row.get("文件名") or extract_info.get("file_name") or ""),
                        str(row.get("解析状态") or extract_info.get("parse_status") or ""),
                        str(row.get("初筛结论") or ""),
                        str(row.get("风险等级") or "unknown"),
                        str(row.get("候选池") or ""),
                        str(row.get("人工最终结论") or detail_payload.get("manual_decision") or ""),
                        str(row.get("人工备注") or detail_payload.get("manual_note") or ""),
                        str(row.get("处理优先级") or detail_payload.get("manual_priority") or "普通"),
                        str(row.get("审核摘要") or ""),
                        json_dumps(scores),
                        json_dumps(extract_info),
                        json_dumps(row),
                        json_dumps(detail_payload),
                        created_at,
                        str(detail_payload.get("updated_at") or created_at),
                    ),
                )
        return batch_id
    finally:
        conn.close()


def list_jd_titles() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT jd_title FROM candidate_batches ORDER BY jd_title ASC"
        ).fetchall()
        return [str(row["jd_title"] or "未命名岗位") for row in rows]
    finally:
        conn.close()


def list_batches_by_jd(jd_title: str) -> list[dict]:
    clean_title = (jd_title or "").strip()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT batch_id, jd_title, created_at, candidate_count, total_resumes, pass_count, review_count, reject_count
            FROM candidate_batches
            WHERE jd_title = ?
            ORDER BY created_at DESC, batch_id DESC
            """,
            (clean_title,),
        ).fetchall()
        return [
            {
                "batch_id": str(row["batch_id"] or ""),
                "jd_title": str(row["jd_title"] or ""),
                "created_at": str(row["created_at"] or ""),
                "candidate_count": int(row["candidate_count"] or 0),
                "total_resumes": int(row["total_resumes"] or row["candidate_count"] or 0),
                "pass_count": int(row["pass_count"] or 0),
                "review_count": int(row["review_count"] or 0),
                "reject_count": int(row["reject_count"] or 0),
            }
            for row in rows
        ]
    finally:
        conn.close()


def load_batch(batch_id: str) -> dict | None:
    key = (batch_id or "").strip()
    if not key:
        return None

    conn = get_connection()
    try:
        batch = conn.execute(
            """
            SELECT batch_id, jd_title, created_at, total_resumes, candidate_count, pass_count, review_count, reject_count
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
            snapshot = _build_candidate_snapshot(candidate_row, str(batch["created_at"] or ""))
            candidates.append(snapshot)
            rows.append(snapshot["row"])
            details[snapshot["candidate_id"]] = snapshot["detail"]

        return {
            "batch_id": str(batch["batch_id"] or ""),
            "jd_title": str(batch["jd_title"] or ""),
            "created_at": str(batch["created_at"] or ""),
            "total_resumes": int(batch["total_resumes"] or len(candidates)),
            "pass_count": int(batch["pass_count"] or 0),
            "review_count": int(batch["review_count"] or 0),
            "reject_count": int(batch["reject_count"] or 0),
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
    """删除指定批次，成功返回 True。"""
    bid = (batch_id or "").strip()
    if not bid:
        return False

    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM candidate_batches WHERE batch_id = ?", (bid,))
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_batches_by_jd(jd_title: str) -> int:
    """删除岗位下全部批次，返回删除数量。"""
    target = (jd_title or "").strip()
    if not target:
        return 0

    conn = get_connection()
    try:
        with conn:
            rows = conn.execute(
                "SELECT batch_id FROM candidate_batches WHERE jd_title = ?",
                (target,),
            ).fetchall()
            batch_ids = [str(row["batch_id"] or "") for row in rows]
            if not batch_ids:
                return 0
            conn.execute("DELETE FROM candidate_batches WHERE jd_title = ?", (target,))
        return len(batch_ids)
    finally:
        conn.close()


def upsert_candidate_manual_review(
    batch_id: str,
    candidate_id: str,
    manual_decision: str | None = None,
    manual_note: str | None = None,
    manual_priority: str | None = None,
) -> bool:
    """更新候选人在指定批次内的人工决策/备注。"""
    bid = (batch_id or "").strip()
    cid = (candidate_id or "").strip()
    if not bid or not cid:
        return False

    conn = get_connection()
    try:
        current = conn.execute(
            """
            SELECT *
            FROM candidate_rows
            WHERE batch_id = ? AND candidate_id = ?
            """,
            (bid, cid),
        ).fetchone()
        if current is None:
            return False

        row_payload = _decode_dict(current["row_json"])
        detail_payload = _decode_dict(current["detail_json"])

        next_manual_decision = str(current["manual_decision"] or "")
        next_manual_note = str(current["manual_note"] or "")
        next_manual_priority = str(
            current["manual_priority"] or row_payload.get("处理优先级") or detail_payload.get("manual_priority") or "普通"
        )

        if manual_decision is not None:
            next_manual_decision = str(manual_decision)
            row_payload["人工最终结论"] = next_manual_decision
            detail_payload["manual_decision"] = next_manual_decision
        if manual_note is not None:
            next_manual_note = str(manual_note)
            row_payload["人工备注"] = next_manual_note
            detail_payload["manual_note"] = next_manual_note
        if manual_priority is not None:
            next_manual_priority = str(manual_priority)
            row_payload["处理优先级"] = next_manual_priority
            detail_payload["manual_priority"] = next_manual_priority

        updated_at = now_str()
        detail_payload["updated_at"] = updated_at

        with conn:
            conn.execute(
                """
                UPDATE candidate_rows
                SET manual_decision = ?,
                    manual_note = ?,
                    manual_priority = ?,
                    row_json = ?,
                    detail_json = ?,
                    updated_at = ?
                WHERE batch_id = ? AND candidate_id = ?
                """,
                (
                    next_manual_decision,
                    next_manual_note,
                    next_manual_priority,
                    json_dumps(row_payload),
                    json_dumps(detail_payload),
                    updated_at,
                    bid,
                    cid,
                ),
            )
            _recount_batch_pools(conn, bid)
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    demo_rows = [{"candidate_id": "cand_1", "姓名": "张三", "初筛结论": "建议人工复核", "风险等级": "medium", "审核摘要": "需核验"}]
    demo_details = {"cand_1": {"score_details": {"综合推荐度": {"score": 3}}, "extract_info": {"method": "text", "quality": "ok", "message": "demo"}}}
    new_batch_id = save_candidate_batch("AI 产品经理实习生", demo_rows, demo_details)
    print("saved:", new_batch_id)
    print("titles:", list_jd_titles())
    print("batches:", list_batches_by_jd("AI 产品经理实习生")[:1])
    print("latest:", load_latest_batch_by_jd("AI 产品经理实习生"))
