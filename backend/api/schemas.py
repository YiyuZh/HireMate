from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    ok: bool = True
    message: str = ""


class UserOut(BaseModel):
    user_id: str
    email: str
    name: str
    is_active: bool
    is_admin: bool
    created_at: str = ""
    updated_at: str = ""
    last_login_at: str = ""


class AuthResponse(BaseModel):
    user: UserOut
    csrf_token: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class JobWriteRequest(BaseModel):
    title: str
    jd_text: str
    openings: int = 0
    scoring_config: dict[str, Any] = Field(default_factory=dict)


class JobUpdateRequest(BaseModel):
    jd_text: str
    openings: int | None = None
    scoring_config: dict[str, Any] = Field(default_factory=dict)


class RuntimeConnectionRequest(BaseModel):
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    purpose: str = "generic"


class ManualNoteRequest(BaseModel):
    note: str = ""


class ManualPriorityRequest(BaseModel):
    priority: str = "普通"


class ManualDecisionRequest(BaseModel):
    decision: str
    note: str = ""


class AiGenerateRequest(BaseModel):
    force_refresh: bool = False


class AiApplyRequest(BaseModel):
    apply_evidence: bool = False
    apply_timeline: bool = False
    apply_risk: bool = False
    apply_scores: bool = False


class AiRevertRequest(BaseModel):
    full_restore: bool = True


class BulkActionRequest(BaseModel):
    action: str
    candidate_ids: list[str] = Field(default_factory=list)
    value: str = ""


class AdminUserCreateRequest(BaseModel):
    email: str
    name: str
    password: str
    is_admin: bool = False


class AdminPasswordResetRequest(BaseModel):
    new_password: str


class AdminFlagRequest(BaseModel):
    value: bool


class LockStateResponse(BaseModel):
    is_locked_effective: bool = False
    self_locked: bool = False
    locked_by_other: bool = False
    owner_user_id: str = ""
    owner_name: str = ""
    owner_email: str = ""
    expires_at: str = ""
    display_name: str = "未领取"


class BatchSummaryResponse(BaseModel):
    batch_id: str = ""
    jd_title: str = ""
    created_at: str = ""
    total_resumes: int = 0
    pass_count: int = 0
    review_count: int = 0
    reject_count: int = 0


class AiDefaultsResponse(BaseModel):
    enable_ai_reviewer: bool = False
    provider: str = "openai"
    model: str = ""
    api_base: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)


class JobSummaryResponse(BaseModel):
    title: str
    jd_text_preview: str = ""
    openings: int = 0
    updated_at: str = ""
    created_by_name: str = ""
    created_by_email: str = ""
    latest_batch: BatchSummaryResponse = Field(default_factory=BatchSummaryResponse)
    ai_defaults: AiDefaultsResponse = Field(default_factory=AiDefaultsResponse)


class JobDetailResponse(BaseModel):
    title: str
    jd_text: str = ""
    openings: int = 0
    scoring_config: dict[str, Any] = Field(default_factory=dict)
    ai_defaults: AiDefaultsResponse = Field(default_factory=AiDefaultsResponse)
    batches: list[BatchSummaryResponse] = Field(default_factory=list)


class PrecheckItemResponse(BaseModel):
    file_name: str = ""
    method: str = "-"
    quality: str = "weak"
    quality_label: str = "较弱"
    message: str = ""
    parse_status: str = "unknown"
    parse_status_label: str = "未知"
    can_enter_batch_screening: bool = False
    weak: bool = False
    ocr_missing: bool = False


class BatchCreateSummaryResponse(BaseModel):
    success_count: int = 0
    failed_files: list[dict[str, Any]] = Field(default_factory=list)
    skipped_files: list[dict[str, Any]] = Field(default_factory=list)
    weak_files: list[str] = Field(default_factory=list)
    ocr_missing_files: list[str] = Field(default_factory=list)


class RuntimeConfigResponse(BaseModel):
    enable_ai_reviewer: bool = False
    provider: str = "openai"
    model: str = ""
    api_base: str = ""
    api_key_mode: str = "env_name"
    api_key_env_name: str = ""
    auto_generate_for_new_batch: bool = False


class BatchCreateResponse(BaseModel):
    batch_id: str
    summary: BatchCreateSummaryResponse
    batch_summary: BatchSummaryResponse
    runtime_config: RuntimeConfigResponse


class WorkbenchRowResponse(BaseModel):
    candidate_id: str
    name: str = ""
    file_name: str = ""
    pool: str = "pending_review"
    pool_label: str = "待复核候选人"
    auto_screening_result: str = ""
    auto_screening_result_label: str = ""
    risk_level: str = "unknown"
    manual_decision: str = "unreviewed"
    manual_decision_label: str = "未处理"
    priority: str = "normal"
    priority_label: str = "普通"
    parse_status: str = "unknown"
    parse_status_label: str = "未知"
    review_summary: str = ""
    extract_method: str = ""
    extract_quality: str = ""
    lock_state: LockStateResponse = Field(default_factory=LockStateResponse)


class WorkbenchResponse(BaseModel):
    batch_summary: BatchSummaryResponse
    rows: list[WorkbenchRowResponse] = Field(default_factory=list)


class EvidenceItemResponse(BaseModel):
    evidence_id: str = ""
    source: str = ""
    text: str = ""
    raw_text: str = ""
    label: str = ""
    tags: list[str] = Field(default_factory=list)
    is_low_readability: bool = False
    support_status: str = ""
    grounded_confidence: float = 0.0
    needs_manual_check: bool = False


class CandidateIdentityResponse(BaseModel):
    candidate_id: str
    name: str = ""
    file_name: str = ""
    pool: str = "pending_review"
    pool_label: str = "待复核候选人"
    auto_screening_result: str = ""
    auto_screening_result_label: str = ""
    manual_decision: str = "unreviewed"
    manual_decision_label: str = "未处理"
    priority: str = "normal"
    priority_label: str = "普通"
    parse_status: str = "unknown"
    parse_status_label: str = "未知"
    review_summary: str = ""


class CandidateAnalysisResponse(BaseModel):
    analysis_mode: str = "normal"
    ocr_confidence: float = 0.0
    structure_confidence: float = 0.0
    parse_confidence: float = 0.0
    candidate_profile: dict[str, Any] = Field(default_factory=dict)
    grounding_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_trace: list[EvidenceItemResponse] = Field(default_factory=list)
    claim_candidates: list[dict[str, Any]] = Field(default_factory=list)
    abstain_reasons: list[str] = Field(default_factory=list)
    extract_method: str = ""
    extract_quality: str = ""
    extract_message: str = ""


class CandidateEvidenceResponse(BaseModel):
    summary_snippets: list[EvidenceItemResponse] = Field(default_factory=list)
    positive_evidence: list[EvidenceItemResponse] = Field(default_factory=list)
    counter_evidence: list[EvidenceItemResponse] = Field(default_factory=list)
    missing_info_points: list[EvidenceItemResponse] = Field(default_factory=list)
    timeline_risks: list[EvidenceItemResponse] = Field(default_factory=list)


class CandidateRiskResponse(BaseModel):
    level: str = "unknown"
    summary: str = ""
    points: list[EvidenceItemResponse] = Field(default_factory=list)
    screening_reasons: list[str] = Field(default_factory=list)


class ManualReviewResponse(BaseModel):
    decision: str = "unreviewed"
    decision_label: str = "未处理"
    note: str = ""
    priority: str = "normal"
    priority_label: str = "普通"


class AiScoreAdjustmentResponse(BaseModel):
    dimension: str = ""
    suggested_delta: int = 0
    max_delta: int = 0
    reason: str = ""
    current_score: int | float | str | None = None
    support_status: str = ""
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    opposing_evidence_ids: list[str] = Field(default_factory=list)
    grounded_confidence: float = 0.0
    needs_manual_check: bool = False


class AiGroundedDetailResponse(BaseModel):
    reason: str = ""
    support_status: str = ""
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    opposing_evidence_ids: list[str] = Field(default_factory=list)
    grounded_confidence: float = 0.0
    needs_manual_check: bool = False


class AiReviewResponse(BaseModel):
    status: str = "not_generated"
    source: str = ""
    model: str = ""
    generated_at: str = ""
    error: str = ""
    review_summary: str = ""
    score_adjustments: list[AiScoreAdjustmentResponse] = Field(default_factory=list)
    risk_adjustment: dict[str, Any] = Field(default_factory=dict)
    recommended_action: str = "no_action"
    recommended_action_detail: AiGroundedDetailResponse = Field(default_factory=AiGroundedDetailResponse)
    abstain_reasons: list[str] = Field(default_factory=list)
    interview_questions: list[Any] = Field(default_factory=list)
    focus_points: list[Any] = Field(default_factory=list)
    applied_actions: list[str] = Field(default_factory=list)


class CandidateDetailResponse(BaseModel):
    batch_summary: BatchSummaryResponse
    identity: CandidateIdentityResponse
    analysis: CandidateAnalysisResponse
    evidence: CandidateEvidenceResponse
    risk: CandidateRiskResponse
    manual_review: ManualReviewResponse
    ai_review: AiReviewResponse
    lock_state: LockStateResponse


class DatabaseHealthResponse(BaseModel):
    backend: str = ""
    ok: bool = False
    users_count: int = 0
    jobs_count: int = 0
    batches_count: int = 0


class OcrHealthResponse(BaseModel):
    image_ocr_available: bool = False
    pdf_ocr_fallback_available: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class LatestAiCallResponse(BaseModel):
    provider: str = ""
    model: str = ""
    api_base: str = ""
    source: str = ""
    reason: str = ""
    env_detected: bool = False


class AdminSystemHealthResponse(BaseModel):
    database: DatabaseHealthResponse = Field(default_factory=DatabaseHealthResponse)
    ocr: OcrHealthResponse = Field(default_factory=OcrHealthResponse)
    latest_ai_call: LatestAiCallResponse = Field(default_factory=LatestAiCallResponse)


class AdminUserRowResponse(BaseModel):
    user_id: str = ""
    email: str = ""
    name: str = ""
    is_admin: bool = False
    is_active: bool = False
    created_at: str = ""
    last_login_at: str = ""
