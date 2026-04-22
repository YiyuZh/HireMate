from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from backend.api.schemas import (
    AiApplyRequest,
    AiGenerateRequest,
    AiRevertRequest,
    BulkActionRequest,
    CandidateDetailResponse,
    ManualDecisionRequest,
    ManualNoteRequest,
    ManualPriorityRequest,
    MessageResponse,
    WorkbenchResponse,
)
from backend.api.viewmodels import build_candidate_detail, build_workbench_response
from backend.core.deps import get_current_user, verify_csrf
from backend.services import workbench_service


router = APIRouter(tags=["workbench"])

POOL_FILTER_MAP = {
    "pending_review": "待复核候选人",
    "passed": "通过候选人",
    "rejected": "淘汰候选人",
    "待复核候选人": "待复核候选人",
    "通过候选人": "通过候选人",
    "淘汰候选人": "淘汰候选人",
}

QUICK_FILTER_MAP = {
    "all": "全部",
    "unreviewed": "仅看未人工处理",
    "high_priority": "仅看高优先级",
    "ai_not_generated": "仅看 AI 建议未生成",
    "ai_generated_not_applied": "仅看 AI 建议已生成但未应用",
    "ocr_weak": "仅看 OCR 弱质量 / OCR 能力缺失",
    "high_risk_pending_review": "仅看高风险且待复核",
    "self_locked": "仅看我处理中",
    "locked_by_other": "仅看他人锁定",
    "unlocked": "仅看未领取",
    "全部": "全部",
    "仅看未人工处理": "仅看未人工处理",
    "仅看高优先级": "仅看高优先级",
    "仅看 AI 建议未生成": "仅看 AI 建议未生成",
    "仅看 AI 建议已生成但未应用": "仅看 AI 建议已生成但未应用",
    "仅看 OCR 弱质量 / OCR 能力缺失": "仅看 OCR 弱质量 / OCR 能力缺失",
    "仅看高风险且待复核": "仅看高风险且待复核",
    "仅看我处理中": "仅看我处理中",
    "仅看他人锁定": "仅看他人锁定",
    "仅看未领取": "仅看未领取",
}

RISK_FILTER_MAP = {
    "all": "全部",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "unknown": "unknown",
    "全部": "全部",
}

SORT_MAP = {
    "priority_desc": "处理优先级（高到低）",
    "priority_asc": "处理优先级（低到高）",
    "risk_desc": "风险等级（高到低）",
    "risk_asc": "风险等级（低到高）",
    "处理优先级（高到低）": "处理优先级（高到低）",
    "处理优先级（低到高）": "处理优先级（低到高）",
    "风险等级（高到低）": "风险等级（高到低）",
    "风险等级（低到高）": "风险等级（低到高）",
}

PRIORITY_MAP = {
    "high": "高",
    "medium": "中",
    "normal": "普通",
    "low": "低",
    "高": "高",
    "中": "中",
    "普通": "普通",
    "低": "低",
}

DECISION_MAP = {
    "passed": "通过",
    "pending_review": "待复核",
    "rejected": "淘汰",
    "通过": "通过",
    "待复核": "待复核",
    "淘汰": "淘汰",
}


def _map_value(value: str, mapping: dict[str, str], default: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return default
    return mapping.get(clean, clean if clean in mapping.values() else default)


@router.get("/workbench", response_model=WorkbenchResponse)
def get_workbench(
    jd_title: str = "",
    batch_id: str = "",
    pool: str = "pending_review",
    quick_filter: str = "all",
    search: str = "",
    risk: str = "all",
    sort: str = "priority_desc",
    user: dict = Depends(get_current_user),
) -> WorkbenchResponse:
    try:
        payload = workbench_service.get_workbench(
            operator=user,
            jd_title=jd_title,
            batch_id=batch_id,
            pool=_map_value(pool, POOL_FILTER_MAP, "待复核候选人"),
            quick_filter=_map_value(quick_filter, QUICK_FILTER_MAP, "全部"),
            search=search,
            risk=_map_value(risk, RISK_FILTER_MAP, "全部"),
            sort=_map_value(sort, SORT_MAP, "处理优先级（高到低）"),
        )
        return build_workbench_response(payload, user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/candidates/{candidate_id}", response_model=CandidateDetailResponse)
def get_candidate_detail(
    candidate_id: str,
    batch_id: str = Query(...),
    user: dict = Depends(get_current_user),
) -> CandidateDetailResponse:
    try:
        payload = workbench_service.get_candidate_detail(batch_id=batch_id, candidate_id=candidate_id)
        return build_candidate_detail(payload, user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/claim", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def claim_candidate(candidate_id: str, batch_id: str = Query(...), user: dict = Depends(get_current_user)) -> MessageResponse:
    payload = workbench_service.claim_candidate(batch_id=batch_id, candidate_id=candidate_id, operator=user)
    return MessageResponse(
        ok=bool(payload.get("ok")),
        message="Candidate claimed" if payload.get("ok") else "Candidate is already locked",
    )


@router.post("/candidates/{candidate_id}/release", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def release_candidate(
    candidate_id: str,
    batch_id: str = Query(...),
    force: bool = False,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    payload = workbench_service.release_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator=user,
        force=force,
    )
    return MessageResponse(ok=bool(payload.get("ok")), message=str(payload.get("message") or ""))


@router.post("/candidates/{candidate_id}/manual-note", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def update_manual_note(
    candidate_id: str,
    payload: ManualNoteRequest,
    batch_id: str = Query(...),
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    ok = workbench_service.update_manual_note(
        batch_id=batch_id,
        candidate_id=candidate_id,
        note=payload.note,
        operator=user,
    )
    return MessageResponse(ok=ok, message="Manual note updated" if ok else "Manual note update failed")


@router.post("/candidates/{candidate_id}/manual-priority", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def update_manual_priority(
    candidate_id: str,
    payload: ManualPriorityRequest,
    batch_id: str = Query(...),
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    ok = workbench_service.update_manual_priority(
        batch_id=batch_id,
        candidate_id=candidate_id,
        priority=_map_value(payload.priority, PRIORITY_MAP, "普通"),
        operator=user,
    )
    return MessageResponse(ok=ok, message="Manual priority updated" if ok else "Manual priority update failed")


@router.post("/candidates/{candidate_id}/manual-decision", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def update_manual_decision(
    candidate_id: str,
    payload: ManualDecisionRequest,
    batch_id: str = Query(...),
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    ok = workbench_service.update_manual_decision(
        batch_id=batch_id,
        candidate_id=candidate_id,
        decision=_map_value(payload.decision, DECISION_MAP, "待复核"),
        note=payload.note,
        operator=user,
    )
    return MessageResponse(ok=ok, message="Manual decision updated" if ok else "Manual decision update failed")


@router.post("/candidates/{candidate_id}/ai/generate", dependencies=[Depends(verify_csrf)])
def generate_ai(
    candidate_id: str,
    payload: AiGenerateRequest,
    batch_id: str = Query(...),
    user: dict = Depends(get_current_user),
) -> dict:
    return workbench_service.generate_candidate_ai(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator=user,
        force_refresh=payload.force_refresh,
    )


@router.post("/candidates/{candidate_id}/ai/apply", dependencies=[Depends(verify_csrf)])
def apply_ai(
    candidate_id: str,
    payload: AiApplyRequest,
    batch_id: str = Query(...),
    user: dict = Depends(get_current_user),
) -> dict:
    return workbench_service.apply_candidate_ai(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator=user,
        apply_evidence=payload.apply_evidence,
        apply_timeline=payload.apply_timeline,
        apply_risk=payload.apply_risk,
        apply_scores=payload.apply_scores,
    )


@router.post("/candidates/{candidate_id}/ai/revert", dependencies=[Depends(verify_csrf)])
def revert_ai(
    candidate_id: str,
    payload: AiRevertRequest,
    batch_id: str = Query(...),
    user: dict = Depends(get_current_user),
) -> dict:
    return workbench_service.revert_candidate_ai(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator=user,
        full_restore=payload.full_restore,
    )


@router.post("/candidates/{candidate_id}/ai/clear", dependencies=[Depends(verify_csrf)])
def clear_ai(candidate_id: str, batch_id: str = Query(...), user: dict = Depends(get_current_user)) -> dict:
    return workbench_service.clear_candidate_ai(batch_id=batch_id, candidate_id=candidate_id, operator=user)


@router.post("/workbench/bulk-actions", dependencies=[Depends(verify_csrf)])
def workbench_bulk_actions(
    payload: BulkActionRequest,
    batch_id: str = Query(...),
    user: dict = Depends(get_current_user),
) -> dict:
    value = payload.value
    if payload.action == "set_priority":
        value = _map_value(payload.value, PRIORITY_MAP, "普通")
    return workbench_service.run_bulk_action(
        batch_id=batch_id,
        candidate_ids=payload.candidate_ids,
        operator=user,
        action=payload.action,
        value=value,
    )


@router.get("/exports/candidates.csv", response_class=Response)
def export_candidates_csv(
    jd_title: str = "",
    batch_id: str = "",
    pool: str = "pending_review",
    quick_filter: str = "all",
    search: str = "",
    risk: str = "all",
    sort: str = "priority_desc",
    user: dict = Depends(get_current_user),
) -> bytes:
    csv_bytes = workbench_service.export_rows_csv(
        operator=user,
        jd_title=jd_title,
        batch_id=batch_id,
        pool=_map_value(pool, POOL_FILTER_MAP, "待复核候选人"),
        quick_filter=_map_value(quick_filter, QUICK_FILTER_MAP, "全部"),
        search=search,
        risk=_map_value(risk, RISK_FILTER_MAP, "全部"),
        sort=_map_value(sort, SORT_MAP, "处理优先级（高到低）"),
    )
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="hiremate-workbench.csv"'},
    )
