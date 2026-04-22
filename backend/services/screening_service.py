from __future__ import annotations

import io
from copy import deepcopy
from typing import Any
from uuid import uuid4

from src.analysis_pipeline import run_analysis_pipeline
from src.ai_reviewer import get_latest_ai_call_status
from src.candidate_store import delete_batch, load_batch, save_candidate_batch
from src.interviewer import build_interview_plan
from src.jd_parser import parse_jd
from src.jd_store import load_jd_scoring_config
from src.resume_loader import check_ocr_capabilities, load_resume_file
from src.resume_parser import normalize_resume_ocr_text, parse_resume
from src.review_store import append_review
from src.risk_analyzer import analyze_risk
from src.role_profiles import detect_role_profile, get_profile_by_name
from src.scorer import score_candidate, to_score_values
from src.screener import build_evidence_bridge, build_screening_decision, collect_evidence_snippets
from src.v2_workspace import build_candidate_row

from backend.services.ai_review_service import (
    apply_batch_ai_runtime_to_detail,
    generate_ai_for_candidate,
    normalize_batch_ai_runtime,
    sanitize_runtime_cfg_for_storage,
)


class InMemoryUploadFile(io.BytesIO):
    def __init__(self, filename: str, content: bytes):
        super().__init__(content)
        self.name = filename

    def getvalue(self) -> bytes:
        position = self.tell()
        self.seek(0)
        content = super().getvalue()
        self.seek(position)
        return content


def _friendly_upload_error(err: Exception) -> str:
    return f"{err} 建议改用 txt 上传，或直接手动粘贴简历文本。"


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


def _extract_parse_status(extract_result: dict[str, Any]) -> str:
    return str(extract_result.get("parse_status") or _resolve_parse_status(str(extract_result.get("quality") or "weak"), str(extract_result.get("message") or "")))


def _can_enter_batch_screening(extract_result: dict[str, Any]) -> bool:
    if bool(extract_result.get("should_skip")):
        return False
    return bool(extract_result.get("can_evaluate", True))


def _candidate_pool_label(screening_result: str) -> str:
    if screening_result == "推荐进入下一轮":
        return "通过候选人"
    if screening_result == "建议人工复核":
        return "待复核候选人"
    return "淘汰候选人"


def _review_summary(decision: str, risk_level: str, risk_summary: str = "") -> str:
    decision_map = {
        "推荐进入下一轮": "建议推进",
        "建议人工复核": "建议人工复核",
        "暂不推荐": "暂不推荐",
    }
    risk_map = {"low": "低风险", "medium": "中风险", "high": "高风险", "unknown": "未知风险"}
    decision_text = decision_map.get(str(decision or "").strip(), str(decision or "待判断"))
    risk_text = risk_map.get(str(risk_level or "unknown").strip().lower(), "未知风险")
    if str(risk_summary or "").strip():
        return f"{decision_text} 风险等级：{risk_text}，重点：{str(risk_summary).strip()[:36]}"
    return f"{decision_text} 风险等级：{risk_text}。"


def _build_review_record(result: dict[str, Any], jd_title: str, resume_file: str, *, operator: dict[str, Any]) -> dict[str, Any]:
    parsed_resume = result.get("parsed_resume", {})
    score_details = result.get("score_details", {})
    score_order = ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度", "综合推荐度"]
    score_summary = {dim: score_details.get(dim, {}).get("score") for dim in score_order}
    screening_result = result.get("screening_result", {})
    risk_result = result.get("risk_result", {})
    interview_plan = result.get("interview_plan", {})
    from datetime import datetime

    return {
        "review_id": f"review-{uuid4().hex}",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "jd_title": (jd_title or "").strip(),
        "resume_name": (parsed_resume.get("name") or "").strip(),
        "resume_file": (resume_file or "").strip(),
        "scores": score_summary,
        "risk_level": risk_result.get("risk_level", "unknown"),
        "auto_risk_level": risk_result.get("risk_level", "unknown"),
        "reviewed_by_user_id": operator["user_id"],
        "reviewed_by_name": operator["name"],
        "reviewed_by_email": operator["email"],
        "screening_result": screening_result.get("screening_result", ""),
        "auto_screening_result": screening_result.get("screening_result", ""),
        "manual_decision": "",
        "manual_note": "",
        "manual_priority": "普通",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "screening_reasons": screening_result.get("screening_reasons", []),
        "risk_points": risk_result.get("risk_points", []),
        "interview_summary": interview_plan.get("interview_summary", ""),
        "evidence_snippets": result.get("evidence_snippets", []),
        "evidence_bridge": result.get("evidence_bridge", {}),
        "ai_review_status": str(result.get("ai_review_status") or "not_generated"),
    }


def run_pipeline(jd_text: str, resume_text: str, jd_title: str = "") -> dict[str, Any]:
    normalized_resume_text = normalize_resume_ocr_text(resume_text)
    parsed_jd = parse_jd(jd_text)
    if jd_title:
        parsed_jd["scoring_config"] = load_jd_scoring_config(jd_title)
        parsed_jd["job_title"] = jd_title
    parsed_resume = parse_resume(normalized_resume_text)
    score_details = score_candidate(parsed_jd, parsed_resume)
    score_values = to_score_values(score_details)
    risk_result = analyze_risk(resume_data=parsed_resume, scores_input=score_details, resume_text=normalized_resume_text)
    screening_result = build_screening_decision(
        scores_input=score_details,
        risk_level=risk_result.get("risk_level"),
        risks=risk_result.get("risk_points", []),
        scoring_config=parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {},
    )
    interview_plan = build_interview_plan(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        scores_input=score_details,
        risk_result=risk_result,
        screening_result=screening_result["screening_result"],
    )
    evidence_snippets = collect_evidence_snippets(parsed_resume, parsed_jd=parsed_jd, limit=5)
    analysis_payload = run_analysis_pipeline(
        parsed_resume=parsed_resume,
        parsed_jd=parsed_jd,
        extract_result={},
        normalized_text=normalized_resume_text,
        raw_text=resume_text,
        evidence_snippets=evidence_snippets,
        score_details=score_details,
        risk_result=risk_result,
        screening_result=screening_result,
    )
    evidence_bridge = build_evidence_bridge(score_details, evidence_snippets)
    if isinstance(evidence_bridge.get("score_details"), dict):
        score_details = evidence_bridge["score_details"]
    if isinstance(evidence_bridge.get("summary_snippets"), list):
        evidence_snippets = evidence_bridge["summary_snippets"]
    return {
        "parsed_jd": parsed_jd,
        "parsed_resume": parsed_resume,
        "score_details": score_details,
        "score_values": score_values,
        "risk_result": risk_result,
        "screening_result": screening_result,
        "interview_plan": interview_plan,
        "evidence_snippets": evidence_snippets,
        "analysis_payload": analysis_payload,
        "evidence_bridge": evidence_bridge,
        "ai_review_status": "not_generated",
    }


def preview_files(files: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    preview_rows: list[dict[str, Any]] = []
    for file_name, content in files:
        try:
            extract_result = load_resume_file(InMemoryUploadFile(file_name, content))
            parse_status = _extract_parse_status(extract_result)
            preview_rows.append(
                {
                    "文件名": file_name,
                    "提取方式": str(extract_result.get("method") or "-"),
                    "提取质量": "正常" if str(extract_result.get("quality") or "weak").lower() == "ok" else "较弱",
                    "提取说明": str(extract_result.get("message") or ""),
                    "解析状态": parse_status,
                    "是否可进入批量初筛": "是" if _can_enter_batch_screening(extract_result) else "否（建议跳过或人工处理）",
                }
            )
        except Exception as err:  # noqa: BLE001
            preview_rows.append(
                {
                    "文件名": file_name,
                    "提取方式": "-",
                    "提取质量": "较弱",
                    "提取说明": _friendly_upload_error(err),
                    "解析状态": "读取失败",
                    "是否可进入批量初筛": "否（读取失败）",
                }
            )
    return preview_rows


def create_batch(
    *,
    jd_title: str,
    jd_text: str,
    files: list[tuple[str, bytes]],
    operator: dict[str, Any],
    batch_ai_runtime_cfg: dict[str, Any] | None = None,
    force_allow_weak: bool = False,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    runtime_cfg = normalize_batch_ai_runtime(batch_ai_runtime_cfg, jd_title=jd_title)
    summary = {"success_count": 0, "failed_files": [], "skipped_files": [], "weak_files": [], "ocr_missing_files": []}

    for idx, (file_name, content) in enumerate(files, start=1):
        try:
            extract_result = load_resume_file(InMemoryUploadFile(file_name, content))
            resume_text = str(extract_result.get("text") or "")
            raw_resume_text = str(extract_result.get("raw_ocr_text") or resume_text)
            normalized_resume_text = str(extract_result.get("normalized_ocr_text") or normalize_resume_ocr_text(resume_text))
            method = str(extract_result.get("method") or "text")
            quality = str(extract_result.get("quality") or "weak")
            message = str(extract_result.get("message") or "")
            parse_status = _extract_parse_status(extract_result)
            can_evaluate = _can_enter_batch_screening(extract_result)
            if quality.lower() == "weak":
                summary["weak_files"].append(file_name)
            if parse_status == "OCR能力缺失":
                summary["ocr_missing_files"].append(file_name)
            if not can_evaluate and force_allow_weak:
                can_evaluate = True
                parse_status = "弱质量识别"
                message = f"{message}（已强制进入初筛）"
            if not can_evaluate:
                summary["skipped_files"].append({"file_name": file_name, "reason": message or "该文件未进入稳定评估，建议跳过或人工处理。"})
                continue
            result = run_pipeline(jd_text, normalized_resume_text, jd_title=jd_title)
            row = build_candidate_row(result, source_name=file_name, index=idx - 1)
            row["提取方式"] = method
            row["提取质量"] = quality
            row["提取说明"] = message
            row["解析状态"] = parse_status
            row["解析标签"] = "⚠ OCR缺失" if parse_status == "OCR能力缺失" else ""
            row["处理优先级"] = "普通"
            row["审核摘要"] = _review_summary(row.get("初筛结论", ""), row.get("风险等级", "unknown"), row.get("风险摘要", ""))
            row["候选池"] = _candidate_pool_label(row.get("初筛结论", ""))
            detail_payload = dict(result)
            apply_batch_ai_runtime_to_detail(detail_payload, runtime_cfg, jd_title=jd_title)
            detail_payload["extract_info"] = {
                "file_name": file_name,
                "method": method,
                "quality": quality,
                "message": message,
                "parse_status": parse_status,
                "can_evaluate": bool(extract_result.get("can_evaluate", True)),
                "should_skip": bool(extract_result.get("should_skip", False)),
            }
            detail_payload["raw_resume_text"] = raw_resume_text
            detail_payload["normalized_resume_text"] = normalized_resume_text
            detail_payload["manual_priority"] = row.get("处理优先级", "普通")
            review_record = _build_review_record(result, jd_title=jd_title or "批量初筛岗位", resume_file=file_name, operator=operator)
            append_review(review_record)
            detail_payload["review_id"] = review_record.get("review_id", "")
            rows.append(row)
            details[row["candidate_id"]] = detail_payload
            summary["success_count"] += 1
        except Exception as err:  # noqa: BLE001
            summary["failed_files"].append({"file_name": file_name, "reason": _friendly_upload_error(err)})

    if not rows:
        raise ValueError("批量初筛已结束，但当前没有可进入稳定评估的文件。")

    batch_id = save_candidate_batch(
        jd_title=jd_title,
        rows=rows,
        details=details,
        created_by_user_id=operator["user_id"],
        created_by_name=operator["name"],
        created_by_email=operator["email"],
    )

    if runtime_cfg.get("enable_ai_reviewer") and runtime_cfg.get("auto_generate_for_new_batch"):
        for candidate_id in list(details.keys()):
            try:
                generate_ai_for_candidate(
                    batch_id=batch_id,
                    candidate_id=candidate_id,
                    operator=operator,
                    runtime_cfg=runtime_cfg,
                    force_refresh=False,
                )
            except Exception:  # noqa: BLE001
                continue

    payload = load_batch(batch_id)
    return {
        "batch_id": batch_id,
        "summary": summary,
        "batch": payload,
        "batch_ai_reviewer_runtime": deepcopy(sanitize_runtime_cfg_for_storage(runtime_cfg)),
    }


def get_batch(batch_id: str) -> dict[str, Any] | None:
    payload = load_batch(batch_id)
    if not payload:
        return None
    return payload


def remove_batch(batch_id: str) -> bool:
    return bool(delete_batch(batch_id))


def health_snapshot() -> dict[str, Any]:
    latest_ai = get_latest_ai_call_status() or {}
    return {
        "ocr": check_ocr_capabilities(),
        "latest_ai_call": latest_ai,
    }
