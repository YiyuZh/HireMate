from __future__ import annotations

from typing import Any

from backend.api.schemas import (
    AdminSystemHealthResponse,
    AdminUserRowResponse,
    AiDefaultsResponse,
    AiReviewResponse,
    AiScoreAdjustmentResponse,
    BatchCreateResponse,
    BatchCreateSummaryResponse,
    BatchSummaryResponse,
    CandidateAnalysisResponse,
    CandidateDetailResponse,
    CandidateEvidenceResponse,
    CandidateIdentityResponse,
    CandidateRiskResponse,
    EvidenceItemResponse,
    JobDetailResponse,
    JobSummaryResponse,
    LatestAiCallResponse,
    LockStateResponse,
    ManualReviewResponse,
    OcrHealthResponse,
    PrecheckItemResponse,
    RuntimeConfigResponse,
    WorkbenchResponse,
    WorkbenchRowResponse,
    DatabaseHealthResponse,
)


POOL_CODE_BY_LABEL = {
    "待复核候选人": "pending_review",
    "通过候选人": "passed",
    "淘汰候选人": "rejected",
}
POOL_LABEL_BY_CODE = {value: key for key, value in POOL_CODE_BY_LABEL.items()}

DECISION_CODE_BY_LABEL = {
    "": "unreviewed",
    "未处理": "unreviewed",
    "通过": "passed",
    "待复核": "pending_review",
    "淘汰": "rejected",
}
DECISION_LABEL_BY_CODE = {
    "unreviewed": "未处理",
    "passed": "通过",
    "pending_review": "待复核",
    "rejected": "淘汰",
}

PRIORITY_CODE_BY_LABEL = {
    "高": "high",
    "中": "medium",
    "普通": "normal",
    "低": "low",
}
PRIORITY_LABEL_BY_CODE = {value: key for key, value in PRIORITY_CODE_BY_LABEL.items()}

SCREENING_CODE_BY_LABEL = {
    "推荐进入下一轮": "advance",
    "建议人工复核": "review",
    "暂不推荐": "reject",
}
SCREENING_LABEL_BY_CODE = {value: key for key, value in SCREENING_CODE_BY_LABEL.items()}

PARSE_CODE_BY_LABEL = {
    "正常识别": "ok",
    "弱质量识别": "weak",
    "OCR能力缺失": "ocr_missing",
    "读取失败": "read_failed",
}
PARSE_LABEL_BY_CODE = {value: key for key, value in PARSE_CODE_BY_LABEL.items()}


def _text(value: Any, default: str = "") -> str:
    return str(value or default).strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _get_any(payload: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in payload:
            value = payload.get(key)
            if value not in (None, ""):
                return value
    return default


def _code_from_label(label: str, mapping: dict[str, str], default: str) -> str:
    clean = _text(label)
    return mapping.get(clean, default)


def _label_from_code(code: str, mapping: dict[str, str], default: str) -> str:
    clean = _text(code)
    return mapping.get(clean, default)


def _lock_state(detail: dict[str, Any], operator: dict[str, Any] | None = None) -> LockStateResponse:
    safe_operator = _dict(operator)
    owner_user_id = _text(detail.get("lock_owner_user_id"))
    owner_name = _text(detail.get("lock_owner_name"))
    owner_email = _text(detail.get("lock_owner_email"))
    is_locked_effective = bool(detail.get("is_locked_effective"))
    self_locked = is_locked_effective and owner_user_id == _text(safe_operator.get("user_id"))
    return LockStateResponse(
        is_locked_effective=is_locked_effective,
        self_locked=self_locked,
        locked_by_other=is_locked_effective and not self_locked,
        owner_user_id=owner_user_id,
        owner_name=owner_name,
        owner_email=owner_email,
        expires_at=_text(detail.get("lock_expires_at")),
        display_name=owner_name or owner_email or "未领取",
    )


def _batch_summary(payload: dict[str, Any]) -> BatchSummaryResponse:
    safe = _dict(payload)
    return BatchSummaryResponse(
        batch_id=_text(safe.get("batch_id")),
        jd_title=_text(safe.get("jd_title")),
        created_at=_text(safe.get("created_at")),
        total_resumes=int(safe.get("total_resumes") or 0),
        pass_count=int(safe.get("pass_count") or 0),
        review_count=int(safe.get("review_count") or 0),
        reject_count=int(safe.get("reject_count") or 0),
    )


def build_batch_summary(payload: dict[str, Any]) -> BatchSummaryResponse:
    return _batch_summary(payload)


def _ai_defaults(scoring_config: dict[str, Any]) -> AiDefaultsResponse:
    reviewer = _dict(_dict(scoring_config).get("ai_reviewer"))
    return AiDefaultsResponse(
        enable_ai_reviewer=bool(reviewer.get("enable_ai_reviewer")),
        provider=_text(reviewer.get("provider"), "openai"),
        model=_text(reviewer.get("model")),
        api_base=_text(reviewer.get("api_base")),
        capabilities=_dict(reviewer.get("capabilities")),
    )


def build_job_summary(payload: dict[str, Any]) -> JobSummaryResponse:
    safe = _dict(payload)
    return JobSummaryResponse(
        title=_text(safe.get("title")),
        jd_text_preview=_text(safe.get("jd_text"))[:320],
        openings=int(safe.get("openings") or 0),
        updated_at=_text(safe.get("updated_at")),
        created_by_name=_text(safe.get("created_by_name")),
        created_by_email=_text(safe.get("created_by_email")),
        latest_batch=_batch_summary(_dict(safe.get("latest_batch"))),
        ai_defaults=_ai_defaults(_dict(safe.get("scoring_config"))),
    )


def build_job_detail(payload: dict[str, Any]) -> JobDetailResponse:
    safe = _dict(payload)
    scoring_config = _dict(safe.get("scoring_config"))
    return JobDetailResponse(
        title=_text(safe.get("title")),
        jd_text=_text(safe.get("jd_text")),
        openings=int(safe.get("openings") or 0),
        scoring_config=scoring_config,
        ai_defaults=_ai_defaults(scoring_config),
        batches=[_batch_summary(item) for item in _list(safe.get("batches"))],
    )


def build_precheck_item(payload: dict[str, Any]) -> PrecheckItemResponse:
    safe = _dict(payload)
    file_name = _text(_get_any(safe, "file_name", "文件名"))
    method = _text(_get_any(safe, "method", "提取方式"), "-")
    quality_label = _text(_get_any(safe, "quality_label", "提取质量"), "较弱")
    quality = _text(safe.get("quality")) or ("ok" if quality_label == "正常" else "weak")
    parse_status_label = _text(_get_any(safe, "parse_status_label", "解析状态"), "未知")
    parse_status = _text(safe.get("parse_status")) or _code_from_label(parse_status_label, PARSE_CODE_BY_LABEL, "unknown")
    can_enter = bool(
        safe.get("can_enter_batch_screening")
        if "can_enter_batch_screening" in safe
        else str(_get_any(safe, "是否可进入批量初筛", default="")).startswith("是")
    )
    ocr_missing = parse_status == "ocr_missing" or "OCR" in parse_status_label and "缺失" in parse_status_label
    return PrecheckItemResponse(
        file_name=file_name,
        method=method,
        quality=quality,
        quality_label=quality_label,
        message=_text(_get_any(safe, "message", "提取说明")),
        parse_status=parse_status,
        parse_status_label=parse_status_label,
        can_enter_batch_screening=can_enter,
        weak=quality != "ok",
        ocr_missing=ocr_missing,
    )


def _runtime_config(payload: dict[str, Any]) -> RuntimeConfigResponse:
    safe = _dict(payload)
    return RuntimeConfigResponse(
        enable_ai_reviewer=bool(safe.get("enable_ai_reviewer")),
        provider=_text(safe.get("provider"), "openai"),
        model=_text(safe.get("model")),
        api_base=_text(safe.get("api_base")),
        api_key_mode=_text(safe.get("api_key_mode"), "env_name"),
        api_key_env_name=_text(safe.get("api_key_env_name")),
        auto_generate_for_new_batch=bool(safe.get("auto_generate_for_new_batch")),
    )


def build_batch_create_response(payload: dict[str, Any]) -> BatchCreateResponse:
    safe = _dict(payload)
    summary = _dict(safe.get("summary"))
    return BatchCreateResponse(
        batch_id=_text(safe.get("batch_id")),
        summary=BatchCreateSummaryResponse(
            success_count=int(summary.get("success_count") or 0),
            failed_files=_list(summary.get("failed_files")),
            skipped_files=_list(summary.get("skipped_files")),
            weak_files=[_text(item) for item in _list(summary.get("weak_files"))],
            ocr_missing_files=[_text(item) for item in _list(summary.get("ocr_missing_files"))],
        ),
        batch_summary=_batch_summary(_dict(safe.get("batch"))),
        runtime_config=_runtime_config(_dict(safe.get("batch_ai_reviewer_runtime"))),
    )


def _identity_from_row(row: dict[str, Any], detail: dict[str, Any]) -> CandidateIdentityResponse:
    pool_label = _text(_get_any(row, "候选池"), "待复核候选人")
    pool_code = _code_from_label(pool_label, POOL_CODE_BY_LABEL, "pending_review")
    auto_label = _text(_get_any(row, "初筛结论"))
    manual_label = _text(detail.get("manual_decision") or _get_any(row, "人工最终结论") or "未处理")
    priority_label = _text(detail.get("manual_priority") or _get_any(row, "处理优先级") or "普通")
    parse_label = _text(_get_any(row, "解析状态", default=_dict(detail.get("extract_info")).get("parse_status")), "未知")
    return CandidateIdentityResponse(
        candidate_id=_text(row.get("candidate_id")),
        name=_text(_get_any(row, "姓名", default=_dict(detail.get("parsed_resume")).get("name"))),
        file_name=_text(_get_any(row, "文件名", default=_dict(_dict(detail).get("extract_info")).get("file_name"))),
        pool=pool_code,
        pool_label=pool_label or _label_from_code(pool_code, POOL_LABEL_BY_CODE, "待复核候选人"),
        auto_screening_result=_code_from_label(auto_label, SCREENING_CODE_BY_LABEL, "unknown"),
        auto_screening_result_label=auto_label,
        manual_decision=_code_from_label(manual_label, DECISION_CODE_BY_LABEL, "unreviewed"),
        manual_decision_label=manual_label or "未处理",
        priority=_code_from_label(priority_label, PRIORITY_CODE_BY_LABEL, "normal"),
        priority_label=priority_label or "普通",
        parse_status=_code_from_label(parse_label, PARSE_CODE_BY_LABEL, "unknown"),
        parse_status_label=parse_label or "未知",
        review_summary=_text(_get_any(row, "审核摘要", default=detail.get("review_summary"))),
    )


def _evidence_item(item: Any) -> EvidenceItemResponse:
    if isinstance(item, str):
        return EvidenceItemResponse(text=_text(item), raw_text=_text(item))
    safe = _dict(item)
    tags = [str(tag).strip() for tag in _list(safe.get("tags")) if str(tag).strip()]
    if not tags and _text(safe.get("tag")):
        tags = [_text(safe.get("tag"))]
    text = _text(safe.get("display_text") or safe.get("text") or safe.get("value") or safe.get("reason"))
    raw_text = _text(safe.get("raw_text") or text)
    return EvidenceItemResponse(
        evidence_id=_text(safe.get("evidence_id")),
        source=_text(safe.get("source")),
        text=text,
        raw_text=raw_text,
        label=_text(safe.get("label")),
        tags=tags,
        is_low_readability=bool(safe.get("is_low_readability")),
        support_status=_text(safe.get("support_status")),
        grounded_confidence=float(safe.get("grounded_confidence") or 0.0),
        needs_manual_check=bool(safe.get("needs_manual_check")),
    )


def _evidence_list(items: Any) -> list[EvidenceItemResponse]:
    return [_evidence_item(item) for item in _list(items)]


def build_workbench_row(row: dict[str, Any], detail: dict[str, Any] | None, operator: dict[str, Any] | None = None) -> WorkbenchRowResponse:
    safe_row = _dict(row)
    safe_detail = _dict(detail)
    identity = _identity_from_row(safe_row, safe_detail)
    extract_info = _dict(safe_detail.get("extract_info"))
    return WorkbenchRowResponse(
        candidate_id=identity.candidate_id,
        name=identity.name,
        file_name=identity.file_name,
        pool=identity.pool,
        pool_label=identity.pool_label,
        auto_screening_result=identity.auto_screening_result,
        auto_screening_result_label=identity.auto_screening_result_label,
        risk_level=_text(_get_any(safe_row, "风险等级", default=_dict(safe_detail.get("risk_result")).get("risk_level")), "unknown"),
        manual_decision=identity.manual_decision,
        manual_decision_label=identity.manual_decision_label,
        priority=identity.priority,
        priority_label=identity.priority_label,
        parse_status=identity.parse_status,
        parse_status_label=identity.parse_status_label,
        review_summary=identity.review_summary,
        extract_method=_text(extract_info.get("method")),
        extract_quality=_text(extract_info.get("quality")),
        lock_state=_lock_state(safe_detail, operator),
    )


def build_workbench_response(payload: dict[str, Any], operator: dict[str, Any] | None = None) -> WorkbenchResponse:
    safe = _dict(payload)
    details = _dict(safe.get("details"))
    rows = []
    for row in _list(safe.get("rows")):
        safe_row = _dict(row)
        candidate_id = _text(safe_row.get("candidate_id"))
        rows.append(build_workbench_row(safe_row, _dict(details.get(candidate_id)), operator))
    return WorkbenchResponse(
        batch_summary=_batch_summary(_dict(safe.get("batch"))),
        rows=rows,
    )


def build_candidate_detail(payload: dict[str, Any], operator: dict[str, Any] | None = None) -> CandidateDetailResponse:
    safe = _dict(payload)
    row = _dict(safe.get("row"))
    detail = _dict(safe.get("detail"))
    batch = _dict(safe.get("batch"))
    identity = _identity_from_row(row, detail)
    analysis_payload = _dict(detail.get("analysis_payload"))
    extract_info = _dict(detail.get("extract_info"))
    risk_result = _dict(detail.get("risk_result"))
    ai_suggestion = _dict(detail.get("ai_review_suggestion"))
    interview_plan = _dict(ai_suggestion.get("interview_plan"))
    score_adjustments = [
        AiScoreAdjustmentResponse(
            dimension=_text(item.get("dimension")),
            suggested_delta=int(item.get("suggested_delta") or 0),
            max_delta=int(item.get("max_delta") or 0),
            reason=_text(item.get("reason")),
            current_score=item.get("current_score"),
            support_status=_text(item.get("support_status")),
            supporting_evidence_ids=[_text(evidence_id) for evidence_id in _list(item.get("supporting_evidence_ids")) if _text(evidence_id)],
            opposing_evidence_ids=[_text(evidence_id) for evidence_id in _list(item.get("opposing_evidence_ids")) if _text(evidence_id)],
            grounded_confidence=float(item.get("grounded_confidence") or 0.0),
            needs_manual_check=bool(item.get("needs_manual_check")),
        )
        for item in _list(ai_suggestion.get("score_adjustments"))
        if isinstance(item, dict)
    ]
    return CandidateDetailResponse(
        batch_summary=_batch_summary(batch),
        identity=identity,
        analysis=CandidateAnalysisResponse(
            analysis_mode=_text(analysis_payload.get("analysis_mode"), "normal"),
            ocr_confidence=float(analysis_payload.get("ocr_confidence") or 0.0),
            structure_confidence=float(analysis_payload.get("structure_confidence") or 0.0),
            parse_confidence=float(analysis_payload.get("parse_confidence") or 0.0),
            candidate_profile=_dict(analysis_payload.get("candidate_profile")),
            grounding_summary=_dict(analysis_payload.get("grounding_summary")),
            evidence_trace=_evidence_list(analysis_payload.get("evidence_trace")),
            claim_candidates=[_dict(item) for item in _list(analysis_payload.get("claim_candidates")) if isinstance(item, dict)],
            abstain_reasons=[_text(item) for item in _list(analysis_payload.get("abstain_reasons")) if _text(item)],
            extract_method=_text(extract_info.get("method")),
            extract_quality=_text(extract_info.get("quality")),
            extract_message=_text(extract_info.get("message")),
        ),
        evidence=CandidateEvidenceResponse(
            summary_snippets=_evidence_list(detail.get("evidence_snippets")),
            positive_evidence=_evidence_list(analysis_payload.get("evidence_for") or detail.get("evidence_snippets")),
            counter_evidence=_evidence_list(analysis_payload.get("evidence_against") or risk_result.get("risk_points")),
            missing_info_points=_evidence_list(analysis_payload.get("missing_info_points")),
            timeline_risks=_evidence_list(analysis_payload.get("timeline_risks")),
        ),
        risk=CandidateRiskResponse(
            level=_text(risk_result.get("risk_level"), "unknown"),
            summary=_text(risk_result.get("risk_summary")),
            points=_evidence_list(risk_result.get("risk_points")),
            screening_reasons=[_text(item) for item in _list(_dict(detail.get("screening_result")).get("screening_reasons")) if _text(item)],
        ),
        manual_review=ManualReviewResponse(
            decision=identity.manual_decision,
            decision_label=identity.manual_decision_label,
            note=_text(detail.get("manual_note")),
            priority=identity.priority,
            priority_label=identity.priority_label,
        ),
        ai_review=AiReviewResponse(
            status=_text(detail.get("ai_review_status"), "not_generated"),
            source=_text(detail.get("ai_source")),
            model=_text(detail.get("ai_model")),
            generated_at=_text(detail.get("ai_generated_at")),
            error=_text(detail.get("ai_review_error")),
            review_summary=_text(ai_suggestion.get("review_summary") or detail.get("review_summary")),
            score_adjustments=score_adjustments,
            risk_adjustment=_dict(ai_suggestion.get("risk_adjustment")),
            recommended_action=_text(ai_suggestion.get("recommended_action"), "no_action"),
            recommended_action_detail=_dict(ai_suggestion.get("recommended_action_detail")),
            abstain_reasons=[_text(item) for item in _list(ai_suggestion.get("abstain_reasons")) if _text(item)],
            interview_questions=_list(interview_plan.get("interview_questions") or ai_suggestion.get("interview_questions")),
            focus_points=_list(interview_plan.get("focus_points") or ai_suggestion.get("focus_points")),
            applied_actions=[_text(item) for item in _list(detail.get("ai_applied_actions")) if _text(item)],
        ),
        lock_state=_lock_state(detail, operator),
    )


def build_admin_health(payload: dict[str, Any]) -> AdminSystemHealthResponse:
    safe = _dict(payload)
    database = _dict(safe.get("database"))
    ocr = _dict(safe.get("ocr"))
    latest_ai = _dict(safe.get("latest_ai_call"))
    return AdminSystemHealthResponse(
        database=DatabaseHealthResponse(
            backend=_text(database.get("backend")),
            ok=bool(database.get("ok")),
            users_count=int(database.get("users_count") or 0),
            jobs_count=int(database.get("jobs_count") or 0),
            batches_count=int(database.get("batches_count") or 0),
        ),
        ocr=OcrHealthResponse(
            image_ocr_available=bool(ocr.get("image_ocr_available")),
            pdf_ocr_fallback_available=bool(ocr.get("pdf_ocr_fallback_available")),
            details=ocr,
        ),
        latest_ai_call=LatestAiCallResponse(
            provider=_text(latest_ai.get("provider")),
            model=_text(latest_ai.get("model")),
            api_base=_text(latest_ai.get("api_base")),
            source=_text(latest_ai.get("source")),
            reason=_text(latest_ai.get("reason")),
            env_detected=bool(latest_ai.get("env_detected")),
        ),
    )


def build_admin_user_row(payload: dict[str, Any]) -> AdminUserRowResponse:
    safe = _dict(payload)
    return AdminUserRowResponse(
        user_id=_text(safe.get("user_id")),
        email=_text(safe.get("email")),
        name=_text(safe.get("name")),
        is_admin=bool(safe.get("is_admin")),
        is_active=bool(safe.get("is_active")),
        created_at=_text(safe.get("created_at")),
        last_login_at=_text(safe.get("last_login_at")),
    )
