export const poolLabels = {
  pending_review: "待复核候选人",
  passed: "通过候选人",
  rejected: "淘汰候选人"
};

export const decisionLabels = {
  unreviewed: "未处理",
  passed: "通过",
  pending_review: "待复核",
  rejected: "淘汰"
};

export const priorityLabels = {
  high: "高",
  medium: "中",
  normal: "普通",
  low: "低"
};

export const parseStatusLabels = {
  ok: "正常识别",
  weak: "弱质量识别",
  ocr_missing: "OCR 能力缺失",
  read_failed: "读取失败",
  unknown: "未知"
};

export function normalizeJobs(payload) {
  const items = Array.isArray(payload) ? payload : [];
  return items.map((item) => ({
    title: item?.title || "",
    jdTextPreview: item?.jd_text_preview || "",
    openings: Number(item?.openings || 0),
    updatedAt: item?.updated_at || "",
    createdByName: item?.created_by_name || "",
    createdByEmail: item?.created_by_email || "",
    latestBatch: normalizeBatchSummary(item?.latest_batch),
    aiDefaults: normalizeAiDefaults(item?.ai_defaults)
  }));
}

export function normalizeJobDetail(payload) {
  return {
    title: payload?.title || "",
    jdText: payload?.jd_text || "",
    openings: Number(payload?.openings || 0),
    scoringConfig: payload?.scoring_config || {},
    aiDefaults: normalizeAiDefaults(payload?.ai_defaults),
    batches: Array.isArray(payload?.batches) ? payload.batches.map(normalizeBatchSummary) : []
  };
}

export function normalizePrecheckItems(payload) {
  const items = Array.isArray(payload) ? payload : [];
  return items.map((item) => ({
    fileName: item?.file_name || "",
    method: item?.method || "-",
    quality: item?.quality || "weak",
    qualityLabel: item?.quality_label || "较弱",
    message: item?.message || "",
    parseStatus: item?.parse_status || "unknown",
    parseStatusLabel: item?.parse_status_label || parseStatusLabels[item?.parse_status] || "未知",
    canEnterBatchScreening: Boolean(item?.can_enter_batch_screening),
    weak: Boolean(item?.weak),
    ocrMissing: Boolean(item?.ocr_missing)
  }));
}

export function normalizeWorkbench(payload) {
  return {
    batchSummary: normalizeBatchSummary(payload?.batch_summary),
    rows: Array.isArray(payload?.rows) ? payload.rows.map(normalizeWorkbenchRow) : []
  };
}

export function normalizeCandidateDetail(payload) {
  return {
    batchSummary: normalizeBatchSummary(payload?.batch_summary),
    identity: normalizeIdentity(payload?.identity),
    analysis: normalizeAnalysis(payload?.analysis),
    evidence: normalizeEvidence(payload?.evidence),
    risk: normalizeRisk(payload?.risk),
    manualReview: normalizeManualReview(payload?.manual_review),
    aiReview: normalizeAiReview(payload?.ai_review),
    lockState: normalizeLockState(payload?.lock_state)
  };
}

export function normalizeAdminHealth(payload) {
  return {
    database: {
      backend: payload?.database?.backend || "",
      ok: Boolean(payload?.database?.ok),
      usersCount: Number(payload?.database?.users_count || 0),
      jobsCount: Number(payload?.database?.jobs_count || 0),
      batchesCount: Number(payload?.database?.batches_count || 0)
    },
    ocr: {
      imageOcrAvailable: Boolean(payload?.ocr?.image_ocr_available),
      pdfOcrFallbackAvailable: Boolean(payload?.ocr?.pdf_ocr_fallback_available),
      details: payload?.ocr?.details || {}
    },
    latestAiCall: {
      provider: payload?.latest_ai_call?.provider || "",
      model: payload?.latest_ai_call?.model || "",
      apiBase: payload?.latest_ai_call?.api_base || "",
      source: payload?.latest_ai_call?.source || "",
      reason: payload?.latest_ai_call?.reason || "",
      envDetected: Boolean(payload?.latest_ai_call?.env_detected || payload?.latest_ai_call?.api_key_env_detected)
    }
  };
}

export function normalizeAdminUsers(payload) {
  const items = Array.isArray(payload) ? payload : [];
  return items.map((item) => ({
    userId: item?.user_id || "",
    email: item?.email || "",
    name: item?.name || "",
    isAdmin: Boolean(item?.is_admin),
    isActive: Boolean(item?.is_active),
    createdAt: item?.created_at || "",
    lastLoginAt: item?.last_login_at || ""
  }));
}

export function normalizeBatchSummary(payload) {
  return {
    batchId: payload?.batch_id || "",
    jdTitle: payload?.jd_title || "",
    createdAt: payload?.created_at || "",
    totalResumes: Number(payload?.total_resumes || 0),
    passCount: Number(payload?.pass_count || 0),
    reviewCount: Number(payload?.review_count || 0),
    rejectCount: Number(payload?.reject_count || 0)
  };
}

export function normalizeAiDefaults(payload) {
  return {
    enableAiReviewer: Boolean(payload?.enable_ai_reviewer),
    provider: payload?.provider || "openai",
    model: payload?.model || "",
    apiBase: payload?.api_base || "",
    capabilities: payload?.capabilities || {}
  };
}

export function normalizeWorkbenchRow(item) {
  return {
    candidateId: item?.candidate_id || "",
    name: item?.name || "",
    fileName: item?.file_name || "",
    pool: item?.pool || "pending_review",
    poolLabel: item?.pool_label || poolLabels[item?.pool] || "待复核候选人",
    autoScreeningResult: item?.auto_screening_result || "",
    autoScreeningResultLabel: item?.auto_screening_result_label || "",
    riskLevel: item?.risk_level || "unknown",
    manualDecision: item?.manual_decision || "unreviewed",
    manualDecisionLabel: item?.manual_decision_label || decisionLabels[item?.manual_decision] || "未处理",
    priority: item?.priority || "normal",
    priorityLabel: item?.priority_label || priorityLabels[item?.priority] || "普通",
    parseStatus: item?.parse_status || "unknown",
    parseStatusLabel: item?.parse_status_label || parseStatusLabels[item?.parse_status] || "未知",
    reviewSummary: item?.review_summary || "",
    extractMethod: item?.extract_method || "",
    extractQuality: item?.extract_quality || "",
    lockState: normalizeLockState(item?.lock_state)
  };
}

function normalizeIdentity(item) {
  return {
    candidateId: item?.candidate_id || "",
    name: item?.name || "",
    fileName: item?.file_name || "",
    pool: item?.pool || "pending_review",
    poolLabel: item?.pool_label || poolLabels[item?.pool] || "待复核候选人",
    autoScreeningResult: item?.auto_screening_result || "",
    autoScreeningResultLabel: item?.auto_screening_result_label || "",
    manualDecision: item?.manual_decision || "unreviewed",
    manualDecisionLabel: item?.manual_decision_label || decisionLabels[item?.manual_decision] || "未处理",
    priority: item?.priority || "normal",
    priorityLabel: item?.priority_label || priorityLabels[item?.priority] || "普通",
    parseStatus: item?.parse_status || "unknown",
    parseStatusLabel: item?.parse_status_label || parseStatusLabels[item?.parse_status] || "未知",
    reviewSummary: item?.review_summary || ""
  };
}

function normalizeAnalysis(item) {
  return {
    analysisMode: item?.analysis_mode || "normal",
    ocrConfidence: Number(item?.ocr_confidence || 0),
    structureConfidence: Number(item?.structure_confidence || 0),
    parseConfidence: Number(item?.parse_confidence || 0),
    candidateProfile: item?.candidate_profile || {},
    groundingSummary: item?.grounding_summary || {},
    evidenceTrace: normalizeEvidenceItems(item?.evidence_trace),
    claimCandidates: Array.isArray(item?.claim_candidates) ? item.claim_candidates : [],
    abstainReasons: Array.isArray(item?.abstain_reasons) ? item.abstain_reasons : [],
    extractMethod: item?.extract_method || "",
    extractQuality: item?.extract_quality || "",
    extractMessage: item?.extract_message || ""
  };
}

function normalizeEvidence(item) {
  return {
    summarySnippets: normalizeEvidenceItems(item?.summary_snippets),
    positiveEvidence: normalizeEvidenceItems(item?.positive_evidence),
    counterEvidence: normalizeEvidenceItems(item?.counter_evidence),
    missingInfoPoints: normalizeEvidenceItems(item?.missing_info_points),
    timelineRisks: normalizeEvidenceItems(item?.timeline_risks)
  };
}

function normalizeRisk(item) {
  return {
    level: item?.level || "unknown",
    summary: item?.summary || "",
    points: normalizeEvidenceItems(item?.points),
    screeningReasons: Array.isArray(item?.screening_reasons) ? item.screening_reasons : []
  };
}

function normalizeManualReview(item) {
  return {
    decision: item?.decision || "unreviewed",
    decisionLabel: item?.decision_label || "未处理",
    note: item?.note || "",
    priority: item?.priority || "normal",
    priorityLabel: item?.priority_label || "普通"
  };
}

function normalizeAiReview(item) {
  return {
    status: item?.status || "not_generated",
    source: item?.source || "",
    model: item?.model || "",
    generatedAt: item?.generated_at || "",
    error: item?.error || "",
    reviewSummary: item?.review_summary || "",
    scoreAdjustments: Array.isArray(item?.score_adjustments)
      ? item.score_adjustments.map((entry) => ({
          dimension: entry?.dimension || "",
          suggestedDelta: Number(entry?.suggested_delta || 0),
          maxDelta: Number(entry?.max_delta || 0),
          reason: entry?.reason || "",
          currentScore: entry?.current_score,
          supportStatus: entry?.support_status || "",
          supportingEvidenceIds: Array.isArray(entry?.supporting_evidence_ids) ? entry.supporting_evidence_ids : [],
          opposingEvidenceIds: Array.isArray(entry?.opposing_evidence_ids) ? entry.opposing_evidence_ids : [],
          groundedConfidence: Number(entry?.grounded_confidence || 0),
          needsManualCheck: Boolean(entry?.needs_manual_check)
        }))
      : [],
    riskAdjustment: normalizeGroundedDetail(item?.risk_adjustment),
    recommendedAction: item?.recommended_action || "no_action",
    recommendedActionDetail: normalizeGroundedDetail(item?.recommended_action_detail),
    abstainReasons: Array.isArray(item?.abstain_reasons) ? item.abstain_reasons : [],
    interviewQuestions: Array.isArray(item?.interview_questions) ? item.interview_questions : [],
    focusPoints: Array.isArray(item?.focus_points) ? item.focus_points : [],
    appliedActions: Array.isArray(item?.applied_actions) ? item.applied_actions : []
  };
}

function normalizeGroundedDetail(item) {
  return {
    reason: item?.reason || "",
    supportStatus: item?.support_status || "",
    supportingEvidenceIds: Array.isArray(item?.supporting_evidence_ids) ? item.supporting_evidence_ids : [],
    opposingEvidenceIds: Array.isArray(item?.opposing_evidence_ids) ? item.opposing_evidence_ids : [],
    groundedConfidence: Number(item?.grounded_confidence || 0),
    needsManualCheck: Boolean(item?.needs_manual_check),
    suggestedRiskLevel: item?.suggested_risk_level || ""
  };
}

export function normalizeLockState(item) {
  return {
    isLockedEffective: Boolean(item?.is_locked_effective),
    selfLocked: Boolean(item?.self_locked),
    lockedByOther: Boolean(item?.locked_by_other),
    ownerUserId: item?.owner_user_id || "",
    ownerName: item?.owner_name || "",
    ownerEmail: item?.owner_email || "",
    expiresAt: item?.expires_at || "",
    displayName: item?.display_name || "未领取"
  };
}

export function normalizeEvidenceItems(items) {
  const list = Array.isArray(items) ? items : [];
  return list.map((item) => ({
    evidenceId: item?.evidence_id || "",
    source: item?.source || "",
    text: item?.text || "",
    rawText: item?.raw_text || item?.text || "",
    label: item?.label || "",
    tags: Array.isArray(item?.tags) ? item.tags : [],
    isLowReadability: Boolean(item?.is_low_readability),
    supportStatus: item?.support_status || "",
    groundedConfidence: Number(item?.grounded_confidence || 0),
    needsManualCheck: Boolean(item?.needs_manual_check)
  }));
}

export function formatConfidence(value) {
  const num = Number(value || 0);
  if (Number.isNaN(num)) {
    return "-";
  }
  return `${Math.round(num * 100)}%`;
}

export function signedDelta(value) {
  const num = Number(value || 0);
  if (Number.isNaN(num)) {
    return String(value || 0);
  }
  return num > 0 ? `+${num}` : `${num}`;
}
