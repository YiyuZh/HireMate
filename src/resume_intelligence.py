"""Resume Intelligence helpers for structured candidate profile extraction."""

from __future__ import annotations

import re
from typing import Any


_ROLE_FAMILY_KEYWORDS = {
    "ai_pm": ["ai产品", "大模型", "prompt", "rag", "agent", "llm", "aigc"],
    "general_pm": ["产品经理", "prd", "原型", "需求分析", "竞品", "上线复盘"],
    "data_analyst": ["数据分析", "sql", "指标", "报表", "洞察", "实验", "a/b"],
    "user_research": ["用户研究", "访谈", "问卷", "可用性测试", "调研", "研究报告"],
}

_METHOD_TERMS = ["需求分析", "PRD", "原型", "SQL", "Python", "访谈", "问卷", "指标", "A/B", "实验", "RAG", "Prompt"]
_OUTPUT_TERMS = ["文档", "报告", "方案", "原型", "策略", "看板", "总结", "结论"]
_RESULT_TERMS = ["提升", "降低", "增长", "优化", "转化", "效率", "结论", "洞察", "复盘", "上线"]
_TIME_PATTERN = re.compile(r"(19|20)\d{2}[./-]\d{1,2}")


def _guess_role_family(text: str) -> str:
    blob = (text or "").lower()
    best_role = "unknown"
    best_hits = 0
    for role_name, keywords in _ROLE_FAMILY_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword.lower() in blob)
        if hits > best_hits:
            best_hits = hits
            best_role = role_name
    return best_role


def _guess_seniority(text: str) -> str:
    blob = (text or "").lower()
    if any(keyword in blob for keyword in ["负责人", "主导", "owner", "lead", "带队"]):
        return "mid"
    if any(keyword in blob for keyword in ["实习", "intern", "应届"]):
        return "intern"
    return "junior"


def _find_terms(text: str, candidates: list[str]) -> list[str]:
    blob = text or ""
    seen: list[str] = []
    for candidate in candidates:
        if candidate and candidate.lower() in blob.lower() and candidate not in seen:
            seen.append(candidate)
    return seen


def _extract_method_output_result_signals(text: str) -> dict[str, list[str]]:
    return {
        "method": _find_terms(text, _METHOD_TERMS),
        "output": _find_terms(text, _OUTPUT_TERMS),
        "result": _find_terms(text, _RESULT_TERMS),
    }


def _detail(value: Any, *, source_span: str = "", confidence: float = 0.0, why: str = "") -> dict[str, Any]:
    return {
        "value": value,
        "source_span": str(source_span or ""),
        "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
        "why": str(why or ""),
    }


def build_candidate_profile(
    parsed_resume: dict[str, Any],
    normalized_text: str = "",
    raw_text: str = "",
) -> dict[str, Any]:
    internships = parsed_resume.get("internships") if isinstance(parsed_resume.get("internships"), list) else []
    projects = parsed_resume.get("projects") if isinstance(parsed_resume.get("projects"), list) else []
    skills = parsed_resume.get("skills") if isinstance(parsed_resume.get("skills"), list) else []

    education_summary = str(parsed_resume.get("education") or "").strip()
    degree = str(parsed_resume.get("degree") or "").strip()
    major = str(parsed_resume.get("major") or "").strip()
    graduation_date = str(parsed_resume.get("graduation_date") or "").strip()
    skill_inventory = [str(item).strip() for item in skills if str(item).strip()]

    internship_summary = [
        str(item.get("raw_text") or "").strip()
        for item in internships[:3]
        if isinstance(item, dict) and str(item.get("raw_text") or "").strip()
    ]
    project_summary = [
        str(item.get("raw_text") or "").strip()
        for item in projects[:3]
        if isinstance(item, dict) and str(item.get("raw_text") or "").strip()
    ]

    text_blob = "\n".join(
        [
            education_summary,
            degree,
            major,
            graduation_date,
            " ".join(skill_inventory),
            " ".join(internship_summary),
            " ".join(project_summary),
            normalized_text or raw_text or "",
        ]
    ).strip()

    experience_blob = " ".join([*internship_summary, *project_summary]).strip()
    signals = _extract_method_output_result_signals(experience_blob)

    timeline_risks: list[dict[str, Any]] = []
    if experience_blob and not _TIME_PATTERN.search(experience_blob):
        timeline_risks.append(
            {
                "source": "timeline",
                "text": "经历时间线不清晰，项目或实习缺少明确时间锚点。",
                "label": "时间线风险",
                "tags": ["反证", "时间线"],
            }
        )
    if internships and not any(str(item.get("end") or item.get("time_range") or "").strip() for item in internships if isinstance(item, dict)):
        timeline_risks.append(
            {
                "source": "timeline",
                "text": "至少一段实习缺少结束时间，建议人工核验时间连续性。",
                "label": "时间缺失",
                "tags": ["反证", "需复核"],
            }
        )

    missing_info_points: list[dict[str, Any]] = []
    if not education_summary:
        missing_info_points.append(
            {"source": "education", "text": "教育背景信息缺失。", "label": "缺失信息", "tags": ["缺证"]}
        )
    if not (internships or projects):
        missing_info_points.append(
            {"source": "experience", "text": "缺少实习或项目经历。", "label": "缺失信息", "tags": ["缺证"]}
        )
    if not skill_inventory:
        missing_info_points.append(
            {"source": "skills", "text": "缺少技能清单或技能证据。", "label": "缺失信息", "tags": ["缺证"]}
        )
    if not graduation_date:
        missing_info_points.append(
            {"source": "education", "text": "毕业时间未识别到。", "label": "时间缺失", "tags": ["缺证", "时间线"]}
        )

    role_family_guess = _guess_role_family(text_blob)
    seniority_guess = _guess_seniority(text_blob)

    return {
        "education_summary": education_summary or "未提取到教育背景",
        "education_summary_detail": _detail(
            education_summary or "未提取到教育背景",
            source_span=education_summary[:160],
            confidence=0.82 if education_summary else 0.18,
            why="来自教育字段与清洗稿中的教育段落。",
        ),
        "internship_summary": internship_summary,
        "internship_summary_detail": _detail(
            internship_summary,
            source_span=" | ".join(internship_summary[:2])[:200],
            confidence=0.8 if internship_summary else 0.2,
            why="来自实习区块与经历原文片段。",
        ),
        "project_summary": project_summary,
        "project_summary_detail": _detail(
            project_summary,
            source_span=" | ".join(project_summary[:2])[:200],
            confidence=0.8 if project_summary else 0.2,
            why="来自项目区块与经历原文片段。",
        ),
        "skill_inventory": skill_inventory,
        "skill_inventory_detail": _detail(
            skill_inventory,
            source_span=", ".join(skill_inventory[:8]),
            confidence=0.86 if skill_inventory else 0.2,
            why="来自技能清单与文本命中项。",
        ),
        "role_family_guess": role_family_guess,
        "role_family_guess_detail": _detail(
            role_family_guess,
            source_span=text_blob[:180],
            confidence=0.72 if role_family_guess != "unknown" else 0.25,
            why="根据岗位族群关键词匹配结果推测。",
        ),
        "seniority_guess": seniority_guess,
        "seniority_guess_detail": _detail(
            seniority_guess,
            source_span=text_blob[:180],
            confidence=0.68,
            why="根据实习/负责人/主导等资历信号推测。",
        ),
        "method_output_result_signals": signals,
        "method_output_result_signals_detail": _detail(
            signals,
            source_span=experience_blob[:220],
            confidence=0.78 if any(signals.values()) else 0.3,
            why="根据经历区块中的方法、产出与结果词命中生成。",
        ),
        "timeline_risks": timeline_risks,
        "missing_info_points": missing_info_points,
    }
