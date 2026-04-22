from __future__ import annotations

from typing import Any

from src.candidate_store import (
    acquire_candidate_lock,
    can_user_operate_candidate,
    get_candidate_lock_state,
    list_batches_by_jd,
    load_batch,
    load_latest_batch_by_jd,
    release_candidate_lock,
    upsert_candidate_manual_review,
)
from src.review_store import upsert_manual_review
from src.v2_workspace import filter_by_risk, rows_to_csv_bytes, search_by_name, sort_rows

from backend.services.ai_review_service import (
    _build_ai_review_metadata,
    apply_ai_suggestions_to_candidate,
    clear_ai_application_state_for_candidate,
    generate_ai_for_candidate,
    revert_ai_application_for_candidate,
)


def _current_candidate_pool(row: dict[str, Any]) -> str:
    manual = str(row.get("人工最终结论") or "").strip()
    if manual == "通过":
        return "通过候选人"
    if manual == "待复核":
        return "待复核候选人"
    if manual == "淘汰":
        return "淘汰候选人"
    return str(row.get("候选池") or "待复核候选人")


def _is_ocr_missing_message(message: str) -> bool:
    msg = (message or "").lower()
    keywords = ["未启用图片 ocr", "ocr 不可用", "未安装", "未启用 ocr", "pdf ocr 需要", "图片 ocr 需要", "tesseract", "poppler", "pdfinfo", "pdftoppm"]
    return any(k in msg for k in keywords)


def _resolve_parse_status(quality: str, message: str) -> str:
    if _is_ocr_missing_message(message):
        return "OCR能力缺失"
    if (quality or "").lower() == "ok":
        return "正常识别"
    return "弱质量识别"


def _workspace_candidate_flags(row: dict[str, Any], detail: dict[str, Any] | None, operator: dict[str, Any]) -> dict[str, Any]:
    safe_detail = detail if isinstance(detail, dict) else {}
    extract_info = safe_detail.get("extract_info") if isinstance(safe_detail.get("extract_info"), dict) else {}
    quality = str(extract_info.get("quality") or row.get("提取质量") or "")
    message = str(extract_info.get("message") or row.get("提取说明") or "")
    parse_status = str(row.get("解析状态") or extract_info.get("parse_status") or "")
    if not parse_status and (quality or message):
        parse_status = _resolve_parse_status(quality, message)
    risk_level = str(row.get("风险等级") or ((safe_detail.get("risk_result") or {}).get("risk_level") if isinstance(safe_detail.get("risk_result"), dict) else "unknown") or "unknown").lower()
    manual_decision = str(safe_detail.get("manual_decision") or row.get("人工最终结论") or "").strip()
    manual_priority = str(safe_detail.get("manual_priority") or row.get("处理优先级") or "普通").strip() or "普通"
    ai_status = str(safe_detail.get("ai_review_status") or "not_generated")
    ai_generated = ai_status in {"ready", "outdated"}
    ocr_missing = parse_status == "OCR能力缺失" or _is_ocr_missing_message(message)
    ocr_weak = parse_status == "弱质量识别" or ((quality or "").lower() == "weak" and not ocr_missing)
    lock_owner_user_id = str(safe_detail.get("lock_owner_user_id") or "").strip()
    is_locked_effective = bool(safe_detail.get("is_locked_effective"))
    self_locked = is_locked_effective and lock_owner_user_id == str(operator.get("user_id") or "")
    locked_by_other = is_locked_effective and not self_locked
    return {
        "manual_processed": bool(manual_decision),
        "manual_priority": manual_priority,
        "ai_status": ai_status,
        "ai_generated": ai_generated,
        "ai_applied": bool(safe_detail.get("ai_applied") or safe_detail.get("ai_applied_actions")),
        "risk_level": risk_level,
        "ocr_weak": ocr_weak,
        "ocr_missing": ocr_missing,
        "current_pool": _current_candidate_pool(row),
        "self_locked": self_locked,
        "locked_by_other": locked_by_other,
        "unlocked": not is_locked_effective,
    }


def _apply_workspace_quick_filter(rows: list[dict[str, Any]], details: dict[str, dict[str, Any]], quick_filter: str, operator: dict[str, Any]) -> list[dict[str, Any]]:
    selected_filter = str(quick_filter or "全部").strip() or "全部"
    if selected_filter == "全部":
        return rows
    filtered: list[dict[str, Any]] = []
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        flags = _workspace_candidate_flags(row, details.get(candidate_id), operator)
        if selected_filter == "仅看未人工处理" and not flags["manual_processed"]:
            filtered.append(row)
        elif selected_filter == "仅看高优先级" and flags["manual_priority"] == "高":
            filtered.append(row)
        elif selected_filter == "仅看 AI 建议未生成" and flags["ai_status"] == "not_generated":
            filtered.append(row)
        elif selected_filter == "仅看 AI 建议已生成但未应用" and flags["ai_generated"] and not flags["ai_applied"]:
            filtered.append(row)
        elif selected_filter == "仅看 OCR 弱质量 / OCR 能力缺失" and (flags["ocr_weak"] or flags["ocr_missing"]):
            filtered.append(row)
        elif selected_filter == "仅看高风险且待复核" and flags["risk_level"] == "high" and flags["current_pool"] == "待复核候选人":
            filtered.append(row)
        elif selected_filter == "仅看我处理中" and flags["self_locked"]:
            filtered.append(row)
        elif selected_filter == "仅看他人锁定" and flags["locked_by_other"]:
            filtered.append(row)
        elif selected_filter == "仅看未领取" and flags["unlocked"]:
            filtered.append(row)
    return filtered


def _load_payload(jd_title: str = "", batch_id: str = "") -> dict[str, Any]:
    if batch_id:
        payload = load_batch(batch_id)
    elif jd_title:
        payload = load_latest_batch_by_jd(jd_title)
    else:
        payload = None
    if payload is None:
        raise ValueError("未找到可用批次。")
    return payload


def get_workbench(
    *,
    operator: dict[str, Any],
    jd_title: str = "",
    batch_id: str = "",
    pool: str = "待复核候选人",
    quick_filter: str = "全部",
    search: str = "",
    risk: str = "全部",
    sort: str = "处理优先级（高到低）",
) -> dict[str, Any]:
    payload = _load_payload(jd_title=jd_title, batch_id=batch_id)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    filtered_rows = [row for row in rows if _current_candidate_pool(row) == pool]
    filtered_rows = _apply_workspace_quick_filter(filtered_rows, details, quick_filter, operator)
    filtered_rows = search_by_name(filtered_rows, search)
    filtered_rows = filter_by_risk(filtered_rows, risk)
    if sort == "风险等级（高到低）":
        filtered_rows = sort_rows(filtered_rows, "风险等级", descending=True)
    elif sort == "风险等级（低到高）":
        filtered_rows = sort_rows(filtered_rows, "风险等级", descending=False)
    else:
        filtered_rows = sort_rows(filtered_rows, sort)
    return {
        "batch": {
            "batch_id": payload.get("batch_id"),
            "jd_title": payload.get("jd_title"),
            "created_at": payload.get("created_at"),
            "total_resumes": payload.get("total_resumes"),
            "pass_count": payload.get("pass_count"),
            "review_count": payload.get("review_count"),
            "reject_count": payload.get("reject_count"),
            "batches": list_batches_by_jd(str(payload.get("jd_title") or "")),
        },
        "rows": filtered_rows,
        "all_rows": rows,
        "details": details,
    }


def get_candidate_detail(*, batch_id: str, candidate_id: str) -> dict[str, Any]:
    payload = _load_payload(batch_id=batch_id)
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    detail = details.get(candidate_id)
    row = next((item for item in rows if str(item.get("candidate_id") or "") == candidate_id), None)
    if not isinstance(detail, dict) or not isinstance(row, dict):
        raise ValueError("候选人不存在")
    return {"row": row, "detail": detail, "batch": payload}


def claim_candidate(*, batch_id: str, candidate_id: str, operator: dict[str, Any]) -> dict[str, Any]:
    ok, lock_state = acquire_candidate_lock(
        batch_id,
        candidate_id,
        operator_user_id=str(operator["user_id"] or ""),
        operator_name=str(operator["name"] or ""),
        operator_email=str(operator["email"] or ""),
    )
    return {"ok": ok, "lock_state": lock_state}


def release_candidate(*, batch_id: str, candidate_id: str, operator: dict[str, Any], force: bool = False) -> dict[str, Any]:
    ok, message = release_candidate_lock(
        batch_id,
        candidate_id,
        operator_user_id=str(operator["user_id"] or ""),
        operator_name=str(operator["name"] or ""),
        operator_email=str(operator["email"] or ""),
        is_admin=bool(operator.get("is_admin")),
        force=force,
    )
    return {"ok": ok, "message": message, "lock_state": get_candidate_lock_state(batch_id, candidate_id)}


def update_manual_note(*, batch_id: str, candidate_id: str, note: str, operator: dict[str, Any]) -> bool:
    payload = _load_payload(batch_id=batch_id)
    detail = (payload.get("details") if isinstance(payload.get("details"), dict) else {}).get(candidate_id) or {}
    row = next((item for item in (payload.get("rows") or []) if str(item.get("candidate_id") or "") == candidate_id), {})
    review_id = str(detail.get("review_id") or "")
    ok = upsert_candidate_manual_review(
        batch_id=batch_id,
        candidate_id=candidate_id,
        manual_note=note,
        operator_user_id=operator["user_id"],
        operator_name=operator["name"],
        operator_email=operator["email"],
        review_id=review_id,
        jd_title=str(payload.get("jd_title") or ""),
        source="api_workbench",
        is_admin=bool(operator.get("is_admin")),
        enforce_lock=True,
    )
    if ok and review_id:
        upsert_manual_review(
            review_id=review_id,
            manual_note=note,
            reviewed_by_user_id=operator["user_id"],
            reviewed_by_name=operator["name"],
            reviewed_by_email=operator["email"],
            metadata_updates=_build_ai_review_metadata(detail),
        )
    return ok


def update_manual_priority(*, batch_id: str, candidate_id: str, priority: str, operator: dict[str, Any]) -> bool:
    payload = _load_payload(batch_id=batch_id)
    detail = (payload.get("details") if isinstance(payload.get("details"), dict) else {}).get(candidate_id) or {}
    review_id = str(detail.get("review_id") or "")
    ok = upsert_candidate_manual_review(
        batch_id=batch_id,
        candidate_id=candidate_id,
        manual_priority=priority,
        operator_user_id=operator["user_id"],
        operator_name=operator["name"],
        operator_email=operator["email"],
        review_id=review_id,
        jd_title=str(payload.get("jd_title") or ""),
        source="api_workbench",
        is_admin=bool(operator.get("is_admin")),
        enforce_lock=True,
    )
    if ok and review_id:
        upsert_manual_review(
            review_id=review_id,
            reviewed_by_user_id=operator["user_id"],
            reviewed_by_name=operator["name"],
            reviewed_by_email=operator["email"],
            metadata_updates=_build_ai_review_metadata(detail),
        )
    return ok


def update_manual_decision(*, batch_id: str, candidate_id: str, decision: str, note: str, operator: dict[str, Any]) -> bool:
    payload = _load_payload(batch_id=batch_id)
    detail = (payload.get("details") if isinstance(payload.get("details"), dict) else {}).get(candidate_id) or {}
    review_id = str(detail.get("review_id") or "")
    ok = upsert_candidate_manual_review(
        batch_id=batch_id,
        candidate_id=candidate_id,
        manual_decision=decision,
        manual_note=note,
        manual_priority=str(detail.get("manual_priority") or "普通"),
        operator_user_id=operator["user_id"],
        operator_name=operator["name"],
        operator_email=operator["email"],
        review_id=review_id,
        jd_title=str(payload.get("jd_title") or ""),
        source="api_workbench",
        is_admin=bool(operator.get("is_admin")),
        enforce_lock=True,
    )
    if ok and review_id:
        upsert_manual_review(
            review_id=review_id,
            manual_decision=decision,
            manual_note=note,
            reviewed_by_user_id=operator["user_id"],
            reviewed_by_name=operator["name"],
            reviewed_by_email=operator["email"],
            metadata_updates=_build_ai_review_metadata(detail),
        )
    return ok


def export_rows_csv(*, operator: dict[str, Any], jd_title: str = "", batch_id: str = "", pool: str = "待复核候选人", quick_filter: str = "全部", search: str = "", risk: str = "全部", sort: str = "处理优先级（高到低）") -> bytes:
    payload = get_workbench(
        operator=operator,
        jd_title=jd_title,
        batch_id=batch_id,
        pool=pool,
        quick_filter=quick_filter,
        search=search,
        risk=risk,
        sort=sort,
    )
    return rows_to_csv_bytes(payload.get("rows") or [])


def run_bulk_action(
    *,
    batch_id: str,
    candidate_ids: list[str],
    operator: dict[str, Any],
    action: str,
    value: str = "",
) -> dict[str, Any]:
    clean_ids = [str(item or "").strip() for item in candidate_ids if str(item or "").strip()]
    if not clean_ids:
        raise ValueError("candidate_ids is required")
    results: list[dict[str, Any]] = []
    for candidate_id in clean_ids:
        try:
            if action == "manual_pending":
                ok = update_manual_decision(
                    batch_id=batch_id,
                    candidate_id=candidate_id,
                    decision="待复核",
                    note="",
                    operator=operator,
                )
                results.append({"candidate_id": candidate_id, "ok": ok})
            elif action == "manual_reject":
                ok = update_manual_decision(
                    batch_id=batch_id,
                    candidate_id=candidate_id,
                    decision="淘汰",
                    note="",
                    operator=operator,
                )
                results.append({"candidate_id": candidate_id, "ok": ok})
            elif action == "set_priority":
                ok = update_manual_priority(
                    batch_id=batch_id,
                    candidate_id=candidate_id,
                    priority=value or "普通",
                    operator=operator,
                )
                results.append({"candidate_id": candidate_id, "ok": ok})
            elif action == "generate_ai":
                outcome = generate_ai_for_candidate(
                    batch_id=batch_id,
                    candidate_id=candidate_id,
                    operator=operator,
                    force_refresh=False,
                )
                results.append({"candidate_id": candidate_id, **outcome})
            else:
                raise ValueError(f"Unsupported bulk action: {action}")
        except Exception as exc:  # noqa: BLE001
            results.append({"candidate_id": candidate_id, "ok": False, "message": str(exc)})
    success_count = sum(1 for item in results if item.get("ok"))
    return {"success_count": success_count, "total": len(clean_ids), "results": results}


def generate_candidate_ai(*, batch_id: str, candidate_id: str, operator: dict[str, Any], force_refresh: bool = False) -> dict[str, Any]:
    return generate_ai_for_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator=operator,
        force_refresh=force_refresh,
    )


def apply_candidate_ai(
    *,
    batch_id: str,
    candidate_id: str,
    operator: dict[str, Any],
    apply_evidence: bool = False,
    apply_timeline: bool = False,
    apply_risk: bool = False,
    apply_scores: bool = False,
) -> dict[str, Any]:
    return apply_ai_suggestions_to_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator=operator,
        apply_evidence=apply_evidence,
        apply_timeline=apply_timeline,
        apply_risk=apply_risk,
        apply_scores=apply_scores,
    )


def revert_candidate_ai(*, batch_id: str, candidate_id: str, operator: dict[str, Any], full_restore: bool = True) -> dict[str, Any]:
    return revert_ai_application_for_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator=operator,
        full_restore=full_restore,
    )


def clear_candidate_ai(*, batch_id: str, candidate_id: str, operator: dict[str, Any]) -> dict[str, Any]:
    return clear_ai_application_state_for_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator=operator,
    )
