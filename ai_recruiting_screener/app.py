"""HireMate Streamlit 页面（岗位库 + 批量初筛 + 候选人工作台）。"""

from __future__ import annotations

from datetime import datetime
import json
import os
import re
from uuid import uuid4

import streamlit as st

from src.interviewer import build_interview_plan
from src.candidate_store import (
    delete_batch as delete_candidate_batch,
    delete_batches_by_jd,
    list_batches_by_jd as list_candidate_batches_by_jd,
    list_jd_titles as list_candidate_jd_titles,
    load_batch as load_candidate_batch,
    load_latest_batch_by_jd,
    save_candidate_batch,
    upsert_candidate_manual_review,
)
from src.jd_parser import parse_jd
from src.jd_store import delete_jd, list_jd_records, list_jds, load_jd, save_jd, update_jd, upsert_jd_openings
from src.jd_store import load_jd_scoring_config, upsert_jd_scoring_config
from src.role_profiles import build_default_scoring_config, detect_role_profile, get_profile_by_name, get_profile_options
from src.resume_loader import load_resume_file
from src.resume_parser import parse_resume
from src.review_store import append_review, list_reviews, upsert_manual_review
from src.risk_analyzer import analyze_risk
from src.ai_reviewer import run_ai_reviewer
from src.scorer import score_candidate, to_score_values
from src.screener import build_screening_decision
from src.v2_workspace import (
    build_candidate_row,
    filter_by_risk,
    rows_to_csv_bytes,
    search_by_name,
    sort_rows,
)

SAMPLE_JD = """岗位名称：AI 产品经理实习生

岗位职责：
1. 参与 AI 产品需求分析、PRD 撰写与功能设计。
2. 协助推进 AIGC 场景落地，跟踪版本迭代与用户反馈。
3. 与算法、研发、设计协作，推动功能上线并监控关键指标。

任职要求：
1. 本科及以上，计算机、数据科学、信息管理、工业工程等相关专业优先。
2. 了解产品流程，具备需求分析、原型设计、数据分析基础。
3. 具备 SQL/Python 基础，沟通表达清晰。

加分项：
- 有大模型/Prompt/RAG/Agent 相关项目经验。
- 有互联网产品实习经历。"""

SAMPLE_RESUME = """张三
教育背景
XX大学 计算机科学与技术 本科 2022.09 - 2026.06

实习经历
某互联网公司 产品实习生 2025.07 - 2025.10
- 负责 AI 助手功能需求分析与竞品分析，输出 PRD 与原型。
- 推动研发上线 A/B 测试，监控转化率并完成周报复盘。

项目经历
校园知识库问答系统 项目负责人 2025.11 - 2026.01
- 基于 RAG 构建问答链路，设计 Prompt 模板并迭代评估集。
- 使用 Python/SQL 完成日志分析，提出优化方案并落地。

技能
Python、SQL、Axure、Figma、数据分析、AIGC、Prompt Engineering
"""


def _short_text(raw: str, max_len: int = 90) -> str:
    text = raw.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _collect_evidence_snippets(parsed_resume: dict, max_items: int = 5) -> list[dict]:
    snippets: list[dict] = []

    for frag in parsed_resume.get("internships") or []:
        raw = (frag.get("raw_text") or "").strip()
        if raw:
            snippets.append({"source": "实习", "text": _short_text(raw)})
        if len(snippets) >= max_items:
            return snippets

    for frag in parsed_resume.get("projects") or []:
        raw = (frag.get("raw_text") or "").strip()
        if raw:
            snippets.append({"source": "项目", "text": _short_text(raw)})
        if len(snippets) >= max_items:
            return snippets

    education = (parsed_resume.get("education") or "").strip()
    if education and len(snippets) < max_items:
        snippets.append({"source": "教育", "text": _short_text(education)})

    return snippets


def _decision_summary(result_text: str) -> str:
    if result_text == "推荐进入下一轮":
        return "岗位匹配度较高，可进入后续流程。"
    if result_text == "建议人工复核":
        return "存在待核验点，建议结构化追问后再决策。"
    if result_text == "暂不推荐":
        return "关键能力证据不足，与岗位要求存在明显差距。"
    return "建议结合面试与业务需求进一步判断。"


def _show_decision(result_text: str) -> None:
    style = "status-success"
    if result_text == "建议人工复核":
        style = "status-warning"
    elif result_text == "暂不推荐":
        style = "status-error"

    summary = _decision_summary(result_text)
    st.markdown(
        (
            f"<div class='status-box {style}'><strong>初筛结论：</strong>{result_text}"
            f"<div class='subtle' style='margin-top:.35rem'>{summary}</div></div>"
        ),
        unsafe_allow_html=True,
    )


def _risk_level_label(level: str) -> str:
    mapping = {"high": "高风险", "medium": "中风险", "low": "低风险"}
    return mapping.get((level or "").lower(), "未知风险")


def _risk_action(level: str) -> str:
    level_norm = (level or "").lower()
    if level_norm == "high":
        return "建议暂不推进或重点复核。"
    if level_norm == "medium":
        return "建议人工复核并围绕风险点追问。"
    if level_norm == "low":
        return "可进入正常流程。"
    return "建议结合面试信息补充判断。"


def _extract_method_label(method: str) -> str:
    return "OCR 识别" if (method or "").lower() == "ocr" else "文本提取"


def _extract_quality_label(quality: str) -> str:
    return "正常" if (quality or "").lower() == "ok" else "较弱"


def _extract_notice(quality: str) -> str:
    return "⚠️ 建议人工检查后再评估" if (quality or "").lower() == "weak" else ""


def _extract_latest_time(raw_text: str) -> str:
    matches = [
        m.group(0)
        for m in re.finditer(r"20\d{2}(?:[./年]\d{1,2}(?:月)?)?", (raw_text or "").replace("—", "-").replace("–", "-"))
    ]
    return matches[-1] if matches else ""


def _build_timeline_summary(parsed_resume: dict, risk_result: dict) -> dict[str, str]:
    graduation_date = (parsed_resume.get("graduation_date") or "").strip() or "未识别"
    internships = parsed_resume.get("internships") or []
    projects = parsed_resume.get("projects") or []

    latest_candidates: list[str] = []
    for frag in internships + projects:
        raw = (frag.get("raw_text") or "").strip()
        if not raw:
            continue
        latest_time = _extract_latest_time(raw)
        if latest_time:
            latest_candidates.append(latest_time)

    risk_points = risk_result.get("risk_points", []) or []
    timeline_unclear = "是" if any("时间" in str(point) for point in risk_points) else "否"
    return {
        "毕业时间": graduation_date,
        "最近实习/项目时间": latest_candidates[-1] if latest_candidates else "未识别",
        "时间线不清晰风险": timeline_unclear,
    }


def _score_brief_summary(score_details: dict, timeline_summary: dict[str, str]) -> str:
    overall_score = (score_details.get("综合推荐度") or {}).get("score")
    try:
        overall_text = f"{int(overall_score)}/5"
    except (TypeError, ValueError):
        overall_text = "-"

    edu_score = int((score_details.get("教育背景匹配度") or {}).get("score") or 0)
    exp_score = int((score_details.get("相关经历匹配度") or {}).get("score") or 0)
    timeline_risky = timeline_summary.get("时间线不清晰风险", "否") == "是"

    strength = "教育与经历较强" if (edu_score >= 4 and exp_score >= 4) else "教育与经历基本匹配"
    if timeline_risky:
        return f"综合推荐度：{overall_text}；{strength}，时间线需进一步核验。"
    return f"综合推荐度：{overall_text}；{strength}。"


def _manual_to_pool(decision: str) -> str:
    mapping = {
        "通过": "通过候选人",
        "待复核": "待复核候选人",
        "淘汰": "淘汰候选人",
    }
    return mapping.get((decision or "").strip(), "")


def _current_candidate_pool(row: dict) -> str:
    manual_pool = _manual_to_pool(row.get("人工最终结论", ""))
    if manual_pool:
        return manual_pool
    return row.get("候选池", "")


def _friendly_upload_error(err: Exception) -> str:
    return f"{err} 建议改用 txt 上传，或直接手动粘贴简历文本。"


def _business_reason(dim: str, detail: dict) -> str:
    """将评分理由转成短句业务表达。"""
    score = detail.get("score")
    try:
        score_num = int(score)
    except (TypeError, ValueError):
        score_num = 0

    if dim == "教育背景匹配度":
        if score_num >= 4:
            return "教育背景匹配良好。"
        if score_num == 3:
            return "教育背景基本满足。"
        return "教育背景支撑偏弱。"

    if dim == "相关经历匹配度":
        if score_num >= 4:
            return "相关经历较强。"
        if score_num == 3:
            return "有相关经历，建议面试核验。"
        return "相关经历不足。"

    if dim == "技能匹配度":
        if score_num >= 4:
            return "核心技能匹配较高。"
        if score_num == 3:
            return "技能基本匹配。"
        return "技能匹配存在缺口。"

    if dim == "表达完整度":
        if score_num >= 4:
            return "简历信息完整清晰。"
        if score_num == 3:
            return "信息基本完整。"
        return "信息完整度不足。"

    if dim == "综合推荐度":
        if score_num >= 4:
            return "整体建议推进。"
        if score_num == 3:
            return "建议复核后决定。"
        return "当前不建议推进。"

    return (detail.get("reason") or "").strip()


def _inject_page_style() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #f6f6f6; color: #111; }
        .block-container { max-width: 1220px; padding-top: 1rem; padding-bottom: 2.8rem; }

        .hero {
            border: 1px solid #dedede; background: #fff; padding: 1rem 1.2rem;
            margin-bottom: 1.2rem; border-radius: 10px;
        }
        .hero h1 { margin: 0; font-size: 1.18rem; font-weight: 700; color: #111; letter-spacing: .2px; }
        .hero p { margin: .28rem 0 0; color: #555; font-size: .92rem; line-height: 1.45; }
        .hero .cap { margin-top: .62rem; }
        .hero .badge {
            display: inline-block; border: 1px solid #d4d4d4; color: #555;
            border-radius: 999px; padding: .08rem .48rem; margin: 0 .35rem .3rem 0;
            font-size: .73rem; background: #fff;
        }

        .panel { border: none; background: transparent; padding: 0.2rem 0.1rem; border-radius: 0; box-shadow: none; }
        .workspace-list, .workspace-detail {
            background: #fff; border-radius: 10px; padding: .9rem .95rem; min-height: 0;
        }
        .workspace-list { border: 1px solid #ececec; }
        .workspace-detail { border: 1px solid #d7d7d7; }

        .section-title { font-size: 1rem; font-weight: 700; margin: .2rem 0 .6rem; letter-spacing: .1px; }
        .subtle { color: #666; font-size: .84rem; line-height: 1.45; }
        .status-box {
            border: 1px solid #dcdcdc; background: #fff; padding: .75rem .88rem;
            margin: .32rem 0 .85rem; border-radius: 8px;
        }
        .status-success { border-color: #cfcfcf; }
        .status-warning { border-color: #bdbdbd; }
        .status-error { border-color: #b2b2b2; background: #f7f7f7; }

        .module-box {
            border: none; background: #fafafa; padding: .78rem .82rem;
            margin: .56rem 0; border-radius: 8px;
        }
        .chip {
            display: inline-block; border: 1px solid #c5c5c5; font-size: .72rem; color: #555;
            padding: .06rem .38rem; margin-right: .45rem; border-radius: 999px; background: #fff;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    st.markdown(
        """
        <div class='hero'>
          <h1>HireMate · Screening</h1>
          <p>面向招聘团队的简洁候选人初筛工作台。</p>
          <div class='cap'>
            <span class='badge'>批量初筛</span>
            <span class='badge'>候选人工作台</span>
            <span class='badge'>审核留痕</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_score_cards(score_details: dict) -> None:
    score_order = ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度", "综合推荐度"]
    for i in range(0, len(score_order), 2):
        row_cols = st.columns(2)
        for col, dim in zip(row_cols, score_order[i : i + 2]):
            detail = score_details.get(dim, {})
            with col:
                st.markdown("<div class='module-box'>", unsafe_allow_html=True)
                st.markdown(f"**{dim}**")
                st.metric("分数", f"{detail.get('score', '-')}/5")
                st.caption(_business_reason(dim, detail))

                evidence = detail.get("evidence")
                if evidence:
                    with st.expander("查看证据", expanded=False):
                        if isinstance(evidence, list):
                            for item in evidence:
                                st.markdown(f"- {item}")
                        else:
                            st.write(evidence)
                st.markdown("</div>", unsafe_allow_html=True)






def _apply_ai_evidence_suggestions(detail: dict, suggestions: list[dict]) -> int:
    if not suggestions:
        return 0
    existing = detail.get("evidence_snippets") or []
    existing_pairs = {(str(item.get("source") or ""), str(item.get("text") or "")) for item in existing}
    added = 0
    for item in suggestions:
        source = str(item.get("source") or "AI建议")
        txt = str(item.get("text") or "").strip()
        if not txt:
            continue
        pair = (source, txt)
        if pair in existing_pairs:
            continue
        existing.append({"source": source, "text": txt})
        existing_pairs.add(pair)
        added += 1
    detail["evidence_snippets"] = existing
    return added


def _apply_ai_risk_suggestion(detail: dict, selected_row: dict, ai_suggestion: dict) -> bool:
    risk_adjustment = ai_suggestion.get("risk_adjustment") if isinstance(ai_suggestion, dict) else {}
    if not isinstance(risk_adjustment, dict):
        return False
    new_level = str(risk_adjustment.get("suggested_risk_level") or "").strip().lower()
    if not new_level:
        return False
    risk_result = detail.get("risk_result") or {}
    risk_result["risk_level"] = new_level
    detail["risk_result"] = risk_result
    selected_row["风险等级"] = new_level
    reason = str(risk_adjustment.get("reason") or "").strip()
    if reason:
        selected_row["风险摘要"] = reason
    return True


def _apply_ai_score_suggestions(detail: dict, selected_row: dict, ai_suggestion: dict) -> int:
    adjustments = ai_suggestion.get("score_adjustments") if isinstance(ai_suggestion, dict) else []
    if not isinstance(adjustments, list):
        return 0
    score_details = detail.get("score_details") or {}
    applied = 0
    for item in adjustments:
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dimension") or "").strip()
        if dim not in score_details:
            continue
        current = score_details.get(dim) or {}
        try:
            base_score = int(current.get("score", 0) or 0)
            delta = int(item.get("suggested_delta", 0) or 0)
            max_delta = int(item.get("max_delta", 1) or 1)
        except (TypeError, ValueError):
            continue
        bounded = max(-max_delta, min(max_delta, delta))
        new_score = max(1, min(5, base_score + bounded))
        current["score"] = new_score
        current.setdefault("evidence", []).append(f"AI建议修正：{item.get('reason') or '无'}")
        score_details[dim] = current
        if dim in {"教育背景匹配度", "相关经历匹配度", "技能匹配度"}:
            selected_row[dim] = new_score
        applied += 1
    detail["score_details"] = score_details
    return applied
def _review_summary(decision: str, risk_level: str, risk_summary: str = "") -> str:
    decision_text = _decision_summary(decision)
    risk_text = _risk_level_label(risk_level)
    risk_hint = (risk_summary or "").strip()
    if risk_hint:
        return f"{decision_text} 风险等级：{risk_text}，重点：{_short_text(risk_hint, 36)}"
    return f"{decision_text} 风险等级：{risk_text}。"




def _candidate_pool_label(screening_result: str) -> str:
    if screening_result == "推荐进入下一轮":
        return "通过候选人"
    if screening_result == "建议人工复核":
        return "待复核候选人"
    return "淘汰候选人"


def _filter_by_candidate_pool(rows: list[dict], selected_pool: str) -> list[dict]:
    if selected_pool == "全部候选人":
        return rows
    return [row for row in rows if row.get("候选池") == selected_pool]


def _render_evidence_snippets(snippets: list[dict]) -> None:
    if not snippets:
        st.caption("未提取到可展示的关键证据片段。")
        return

    for item in snippets:
        source = item.get("source", "其他")
        text = item.get("text", "")
        st.markdown(
            f"<div class='module-box'><span class='chip'>{source}</span>{text}</div>",
            unsafe_allow_html=True,
        )


def _render_history_records(limit: int = 5) -> None:
    """历史审核留痕：列表 + 选择查看单条详情（兼容旧记录字段缺失）。"""
    history = list_reviews(limit=limit)
    if not history:
        st.caption("暂无历史审核记录。")
        return

    # 简要列表（快速浏览）
    for row in history:
        auto_decision = row.get("auto_screening_result") or row.get("screening_result") or "-"
        manual_decision = row.get("manual_decision") or "未标记"
        st.markdown(
            f"- {row.get('timestamp', '')}｜"
            f"{row.get('resume_name', '未命名候选人')}｜"
            f"自动：{auto_decision}｜人工：{manual_decision}"
        )

    st.markdown("**查看单条审核详情**")
    options = [
        f"{idx + 1}. {row.get('timestamp', '')}｜{row.get('resume_name', '未命名候选人')}"
        for idx, row in enumerate(history)
    ]
    selected_label = st.selectbox("选择历史记录", options=options, key="history_record_select")
    selected_index = options.index(selected_label)
    row = history[selected_index]

    st.markdown("#### 审核详情")
    st.write(f"JD 标题：{row.get('jd_title', '') or '未填写'}")
    st.write(f"候选人：{row.get('resume_name', '') or '未命名候选人'}")
    st.write(f"自动结论：{row.get('auto_screening_result') or row.get('screening_result', '-')}")
    st.write(f"自动风险：{_risk_level_label(row.get('auto_risk_level') or row.get('risk_level', 'unknown'))}")
    st.write(f"人工结论：{row.get('manual_decision') or '未标记'}")
    st.write(f"修改时间：{row.get('updated_at') or row.get('timestamp', '-')}")
    st.write(f"人工备注：{row.get('manual_note') or '无'}")

    st.write("五维评分：")
    st.json(row.get("scores", {}))

    st.write("结论原因：")
    reasons = row.get("screening_reasons") or []
    if reasons:
        for reason in reasons:
            st.markdown(f"- {reason}")
    else:
        st.caption("旧记录未包含结论原因。")

    st.write("风险点：")
    risk_points = row.get("risk_points") or []
    if risk_points:
        for rp in risk_points:
            st.markdown(f"- ⚠️ {rp}")
    else:
        st.caption("旧记录未包含风险点。")

    st.write("面试总结：")
    summary = row.get("interview_summary", "")
    st.write(summary if summary else "旧记录未包含面试总结。")

    st.write("关键证据片段：")
    evidence = row.get("evidence_snippets") or []
    if evidence:
        _render_evidence_snippets(evidence)
    else:
        st.caption("旧记录未包含关键证据片段。")


def _build_review_record(result: dict, jd_title: str, resume_file: str = "") -> dict:
    parsed_resume = result.get("parsed_resume", {})
    score_details = result.get("score_details", {})
    score_order = ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度", "综合推荐度"]
    score_summary = {dim: score_details.get(dim, {}).get("score") for dim in score_order}

    screening_result = result.get("screening_result", {})
    risk_result = result.get("risk_result", {})
    interview_plan = result.get("interview_plan", {})

    return {
        "review_id": f"review-{uuid4().hex}",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "jd_title": (jd_title or "").strip(),
        "resume_name": (parsed_resume.get("name") or "").strip(),
        "resume_file": (resume_file or "").strip(),
        "scores": score_summary,
        "risk_level": risk_result.get("risk_level", "unknown"),
        "auto_risk_level": risk_result.get("risk_level", "unknown"),
        "screening_result": screening_result.get("screening_result", ""),
        "auto_screening_result": screening_result.get("screening_result", ""),
        "manual_decision": "",
        "manual_note": "",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "screening_reasons": screening_result.get("screening_reasons", []),
        "risk_points": risk_result.get("risk_points", []),
        "interview_summary": interview_plan.get("interview_summary", ""),
        "evidence_snippets": result.get("evidence_snippets", []),
    }


def _normalize_jd_title(input_title: str) -> str:
    """优先使用输入标题；未输入时回退到当前已选择 JD 标题。"""
    clean = (input_title or "").strip()
    if clean:
        return clean
    return (st.session_state.get("selected_jd_title") or "").strip()


def _run_pipeline(jd_text: str, resume_text: str, jd_title: str = "") -> dict:
    parsed_jd = parse_jd(jd_text)
    if jd_title:
        parsed_jd["scoring_config"] = load_jd_scoring_config(jd_title)
    parsed_resume = parse_resume(resume_text)
    score_details = score_candidate(parsed_jd, parsed_resume)
    score_values = to_score_values(score_details)

    risk_result = analyze_risk(resume_data=parsed_resume, scores_input=score_details, resume_text=resume_text)
    screening_result = build_screening_decision(
        scores_input=score_details,
        risk_level=risk_result.get("risk_level"),
        risks=risk_result.get("risk_points", []),
    )
    interview_plan = build_interview_plan(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        scores_input=score_details,
        risk_result=risk_result,
        screening_result=screening_result["screening_result"],
    )

    evidence_snippets = _collect_evidence_snippets(parsed_resume)
    scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    template_name = scoring_cfg.get("role_template") or scoring_cfg.get("profile_name")
    role_profile = get_profile_by_name(template_name) if template_name else detect_role_profile(parsed_jd)
    ai_review_suggestion = run_ai_reviewer(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        role_profile=role_profile,
        scoring_config=scoring_cfg,
        score_details=score_details,
        risk_result=risk_result,
        screening_result=screening_result,
        evidence_snippets=evidence_snippets,
    )

    return {
        "parsed_jd": parsed_jd,
        "parsed_resume": parsed_resume,
        "score_details": score_details,
        "score_values": score_values,
        "risk_result": risk_result,
        "screening_result": screening_result,
        "interview_plan": interview_plan,
        "evidence_snippets": evidence_snippets,
        "ai_review_suggestion": ai_review_suggestion,
    }




def _on_v1_saved_jd_change() -> None:
    """快速审核：选择已保存 JD 后，同步标题草稿与 JD 文本。"""
    selected = (st.session_state.get("v1_saved_jd_select") or "").strip()
    st.session_state.selected_jd_title = selected
    if not selected:
        return

    st.session_state.v1_jd_title_draft = selected
    st.session_state.jd_text = load_jd(selected) or ""




def _jd_summary(text: str, max_len: int = 80) -> str:
    clean = (text or "").replace("\n", " ").strip()
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1] + "…"


def _latest_batch_snapshot(jd_title: str) -> dict:
    """读取岗位最近一次批量初筛概况。"""
    history = list_candidate_batches_by_jd(jd_title)
    if not history:
        return {
            "latest_time": "-",
            "pass_count": 0,
            "review_count": 0,
            "reject_count": 0,
        }
    latest = history[0]
    return {
        "latest_time": latest.get("created_at", "-") or "-",
        "pass_count": int(latest.get("pass_count", 0) or 0),
        "review_count": int(latest.get("review_count", 0) or 0),
        "reject_count": int(latest.get("reject_count", 0) or 0),
    }


def _apply_jd_to_quick_review(title: str) -> None:
    st.session_state.selected_jd_title = title
    st.session_state.v1_jd_title_draft = title
    st.session_state.jd_text = load_jd(title) or ""


def _apply_jd_to_workspace(title: str) -> None:
    jd_text = load_jd(title) or ""
    st.session_state.v2_selected_jd_prev = title
    st.session_state.v2_jd_text_area = jd_text
    st.session_state.batch_selected_jd_prev = title
    st.session_state.batch_jd_text_area = jd_text
    st.session_state.workspace_selected_jd_title = title


def _apply_batch_to_workspace(jd_title: str, batch_id: str, preferred_pool: str | None = None) -> None:
    """将岗位和批次设为候选人工作台默认上下文。"""
    st.session_state.workspace_selected_jd_title = (jd_title or "").strip()
    st.session_state.workspace_preferred_batch_id = (batch_id or "").strip()
    if preferred_pool in {"待复核候选人", "通过候选人", "淘汰候选人"}:
        st.session_state.workspace_pool_top_radio = preferred_pool
        st.session_state.workspace_default_entry_pool = preferred_pool


def _clear_workspace_selection_cache() -> None:
    st.session_state.workspace_selected_candidate_by_context = {}


def _after_batch_deleted(jd_title: str, deleted_batch_id: str) -> None:
    """删除批次后同步页面上下文，避免旧候选人详情残留。"""
    _clear_workspace_selection_cache()

    if st.session_state.get("v2_current_batch_id") == deleted_batch_id:
        st.session_state.v2_current_batch_id = ""

    remaining = list_candidate_batches_by_jd(jd_title)
    if remaining:
        latest_batch_id = remaining[0].get("batch_id", "")
        _apply_batch_to_workspace(jd_title, latest_batch_id)
    else:
        if st.session_state.get("workspace_selected_jd_title") == jd_title:
            st.session_state.workspace_preferred_batch_id = ""
            st.session_state.workspace_pool_top_radio = "待复核候选人"
            st.session_state.workspace_default_entry_pool = "待复核候选人"
        st.session_state.v2_rows = []
        st.session_state.v2_details = {}


def _request_page_navigation(page: str) -> None:
    """统一发起页面跳转请求，避免直接修改 sidebar radio 的 session key。"""
    target = (page or "").strip()
    if not target:
        return
    st.session_state.active_page = target
    st.session_state.pending_navigation_page = target


def _sync_job_management_drafts(selected_job: str) -> None:
    """同步岗位管理区草稿字段，避免切换岗位后保留旧值。"""
    job = (selected_job or "").strip()
    st.session_state.joblib_selected_job = job
    if not job:
        st.session_state.joblib_draft_text = ""
        st.session_state.joblib_draft_openings = 0
        st.session_state.joblib_draft_scoring_config = _normalize_scoring_config(build_default_scoring_config("AI产品经理 / 大模型产品经理"))
        st.session_state.joblib_selected_title_prev = ""
        return

    records = list_jd_records()
    rec = next((item for item in records if item.get("title") == job), {})
    st.session_state.joblib_draft_text = load_jd(job)
    st.session_state.joblib_draft_openings = int(rec.get("openings", 0) or 0)
    st.session_state.joblib_draft_scoring_config = _normalize_scoring_config(load_jd_scoring_config(job))
    st.session_state.joblib_selected_title_prev = job


def _profile_hard_flag_options(profile_name: str) -> list[tuple[str, str]]:
    mapping = {
        "AI产品经理 / 大模型产品经理": [],
        "通用产品经理 / 用户产品经理": [
            ("require_pm_project_support", "无产品项目/实习支撑时降档"),
            ("require_prd_or_prototype", "无 PRD/原型证据时降档"),
        ],
        "数据分析师 / 数据分析实习生": [
            ("require_sql", "无 SQL 证据时降档"),
            ("require_data_project_support", "无数据项目/实习支撑时降档"),
        ],
        "用户研究分析师": [
            ("require_research_project_support", "无研究项目支撑时降档"),
            ("market_activity_not_equal_research", "市场活动经验不能等同用户研究"),
        ],
        "通用岗位模板": [],
    }
    return mapping.get(profile_name, [])


def _default_ai_rule_suggester_config() -> dict:
    return {
        "enable_ai_rule_suggester": False,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_base": "",
        "api_key_env_name": "OPENAI_API_KEY",
    }


def _default_ai_reviewer_config() -> dict:
    return {
        "enable_ai_reviewer": False,
        "ai_reviewer_mode": "off",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_base": "",
        "api_key_env_name": "OPENAI_API_KEY",
        "capabilities": {
            "add_evidence_snippets": True,
            "organize_timeline": True,
            "suggest_risk_adjustment": False,
            "suggest_score_adjustment": False,
            "generate_review_summary": True,
        },
        "score_adjustment_limit": {
            "max_delta_per_dimension": 1,
            "allow_break_hard_thresholds": False,
            "allow_direct_recommendation_change": False,
        },
    }


def _normalize_scoring_config(scoring_cfg: dict | None) -> dict:
    cfg = scoring_cfg if isinstance(scoring_cfg, dict) else {}
    profile_name = cfg.get("role_template") or cfg.get("profile_name") or "AI产品经理 / 大模型产品经理"
    default_cfg = build_default_scoring_config(profile_name)
    thresholds = cfg.get("screening_thresholds") if isinstance(cfg.get("screening_thresholds"), dict) else cfg.get("thresholds")
    hard_flags = cfg.get("hard_thresholds") if isinstance(cfg.get("hard_thresholds"), dict) else cfg.get("hard_flags")
    normalized = {
        "profile_name": profile_name,
        "role_template": profile_name,
        "weights": {**(default_cfg.get("weights") or {}), **(cfg.get("weights") or {})},
        "thresholds": {**(default_cfg.get("thresholds") or {}), **(thresholds or {})},
        "screening_thresholds": {**(default_cfg.get("screening_thresholds") or {}), **(thresholds or {})},
        "hard_flags": {**(default_cfg.get("hard_flags") or {}), **(hard_flags or {})},
        "hard_thresholds": {**(default_cfg.get("hard_thresholds") or {}), **(hard_flags or {})},
        "risk_focus": cfg.get("risk_focus") if isinstance(cfg.get("risk_focus"), list) else list(default_cfg.get("risk_focus") or []),
        "ai_rule_suggester": {**_default_ai_rule_suggester_config(), **(cfg.get("ai_rule_suggester") or {})},
        "ai_reviewer": {
            **_default_ai_reviewer_config(),
            **(cfg.get("ai_reviewer") or {}),
            "capabilities": {
                **_default_ai_reviewer_config().get("capabilities", {}),
                **((cfg.get("ai_reviewer") or {}).get("capabilities") or {}),
            },
            "score_adjustment_limit": {
                **_default_ai_reviewer_config().get("score_adjustment_limit", {}),
                **((cfg.get("ai_reviewer") or {}).get("score_adjustment_limit") or {}),
            },
        },
    }
    return normalized


def _build_ai_scoring_stub_suggestion(profile_name: str, current_cfg: dict, jd_text: str) -> dict:
    template_defaults = build_default_scoring_config(profile_name)
    keywords = [k for k in ["SQL", "Python", "A/B", "访谈", "RAG", "Prompt"] if k.lower() in (jd_text or "").lower()]
    suggestion = {
        "role_template": profile_name,
        "weights": current_cfg.get("weights") or template_defaults.get("weights") or {},
        "hard_thresholds": current_cfg.get("hard_thresholds") or template_defaults.get("hard_thresholds") or {},
        "screening_thresholds": current_cfg.get("screening_thresholds") or current_cfg.get("thresholds") or template_defaults.get("screening_thresholds") or {},
        "risk_focus": current_cfg.get("risk_focus") or template_defaults.get("risk_focus") or [],
        "notes": [
            "本地为预留接口，当前返回结构化 mock 建议。",
            "部署到云服务器并配置 API Key 后可切换为真实模型建议。",
            f"JD 关键词命中：{keywords if keywords else '无明显额外信号'}",
        ],
    }
    return suggestion


def _on_joblib_selected_job_change() -> None:
    _sync_job_management_drafts(st.session_state.get("joblib_selected_title", ""))


def _cleanup_legacy_joblib_widget_state() -> None:
    """清理历史版本遗留 key，避免触发 Streamlit widget key 冲突。"""
    legacy_keys = ["joblib_edit_text", "joblib_edit_openings"]
    for key in legacy_keys:
        if key in st.session_state:
            del st.session_state[key]


def _render_job_library() -> None:
    _cleanup_legacy_joblib_widget_state()

    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>岗位首页 · 招聘工作台入口</div>", unsafe_allow_html=True)
    st.caption("先查看各岗位当前招聘状态，再进入批量初筛或候选人工作台处理当日任务。")

    flash_msg = st.session_state.pop("joblib_flash_success", "")
    if flash_msg:
        st.success(flash_msg)

    records = list_jd_records()
    in_use_titles = {
        (st.session_state.get("selected_jd_title") or "").strip(),
        (st.session_state.get("v2_selected_jd_prev") or "").strip(),
        (st.session_state.get("batch_selected_jd_prev") or "").strip(),
    }

    st.markdown("### 岗位卡片区")
    if records:
        for rec in records:
            title = rec.get("title", "")
            snapshot = _latest_batch_snapshot(title)
            st.markdown("<div class='module-box'>", unsafe_allow_html=True)
            st.markdown(f"**{title}**")
            st.caption(f"JD 摘要：{_jd_summary(rec.get('text', ''), max_len=110)}")
            openings = int(rec.get("openings", 0) or 0)
            st.caption(
                f"最近批次时间：{snapshot.get('latest_time', '-')}"
                f" ｜ 当前空缺人数：{openings}"
                f" ｜ 当前候选池：通过 {snapshot.get('pass_count', 0)}"
                f" / 待复核 {snapshot.get('review_count', 0)}"
                f" / 淘汰 {snapshot.get('reject_count', 0)}"
            )
            if title in in_use_titles:
                st.caption("当前上下文：该岗位正在被批量初筛或工作台使用。")

            metric_cols = st.columns(4)
            metric_cols[0].metric("空缺", openings)
            metric_cols[1].metric("通过", snapshot.get("pass_count", 0))
            metric_cols[2].metric("待复核", snapshot.get("review_count", 0))
            metric_cols[3].metric("淘汰", snapshot.get("reject_count", 0))

            op_col1, op_col2 = st.columns(2)
            with op_col1:
                if st.button("进入批量初筛", key=f"job_entry_batch_{title}", use_container_width=True):
                    _apply_jd_to_workspace(title)
                    _request_page_navigation("批量初筛")
                    st.rerun()
            with op_col2:
                if st.button("进入候选人工作台", key=f"job_entry_workspace_{title}", use_container_width=True):
                    _apply_jd_to_workspace(title)
                    _request_page_navigation("候选人工作台")
                    st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("岗位库为空，请先在下方岗位配置管理区新建岗位。")

    st.markdown("---")
    st.markdown("### 岗位配置管理区")
    st.caption("保留岗位配置能力：新建、编辑、删除与 JD 文本查看。")
    st.caption("当前编辑模式：草稿状态（joblib_selected_job / joblib_draft_text / joblib_draft_openings）。")

    with st.expander("筛选规则摘要", expanded=False):
        st.markdown("- **通过线**：综合评分达到岗位推荐线且无高风险阻断。")
        st.markdown("- **关键维度门槛**：相关经历匹配度、技能匹配度为重点核验维度。")
        st.markdown("- **风险修正规则**：高风险优先降档至人工复核或暂不推荐，中风险建议人工追问。")

    selected_job = st.selectbox(
        "选择岗位进行管理",
        options=[""] + [r.get("title", "") for r in records],
        format_func=lambda x: x if x else "请选择岗位",
        key="joblib_selected_title",
        on_change=_on_joblib_selected_job_change,
    )

    if selected_job:
        if st.session_state.get("joblib_selected_title_prev", "") != selected_job:
            _sync_job_management_drafts(selected_job)

        edited_openings = st.number_input(
            "当前空缺人数",
            min_value=0,
            step=1,
            value=int(st.session_state.get("joblib_draft_openings", 0) or 0),
            key=f"joblib_edit_openings_input_{selected_job}",
            help="用于岗位总览首页卡片展示，可按招聘进度手动维护。",
        )
        edited_text = st.text_area(
            "岗位 JD 内容（可查看/编辑）",
            value=st.session_state.get("joblib_draft_text", load_jd(selected_job)),
            height=180,
            key=f"joblib_edit_text_input_{selected_job}",
        )
        st.session_state.joblib_draft_openings = int(edited_openings)
        st.session_state.joblib_draft_text = edited_text

        st.markdown("**评分设置区**")
        scoring_cfg = _normalize_scoring_config(st.session_state.get("joblib_draft_scoring_config") or build_default_scoring_config("AI产品经理 / 大模型产品经理"))

        profile_options = get_profile_options()
        current_profile = scoring_cfg.get("profile_name") or profile_options[0]
        if current_profile not in profile_options:
            current_profile = profile_options[0]
        selected_profile = st.selectbox(
            "岗位评分模板",
            options=profile_options,
            index=profile_options.index(current_profile),
            key=f"joblib_scoring_profile_{selected_job}",
        )

        if st.button("恢复模板默认值", key=f"joblib_restore_profile_defaults_{selected_job}", use_container_width=True):
            scoring_cfg = _normalize_scoring_config(build_default_scoring_config(selected_profile))
            st.session_state.joblib_draft_scoring_config = scoring_cfg
            st.rerun()

        if selected_profile != current_profile:
            scoring_cfg = _normalize_scoring_config(build_default_scoring_config(selected_profile))

        st.caption("四项基础权重")
        weight_cfg = scoring_cfg.get("weights") or {}
        wcols = st.columns(4)
        weight_keys = ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度"]
        weight_values = {}
        for idx, wk in enumerate(weight_keys):
            weight_values[wk] = wcols[idx].number_input(
                wk,
                min_value=0.0,
                max_value=1.0,
                step=0.01,
                value=float(weight_cfg.get(wk, 0.25) or 0.25),
                key=f"joblib_weight_{selected_job}_{wk}",
            )

        st.caption("筛选门槛")
        thr_cfg = scoring_cfg.get("screening_thresholds") or scoring_cfg.get("thresholds") or {}
        tcols = st.columns(5)
        pass_line = tcols[0].number_input("通过线", min_value=1, max_value=5, value=int(thr_cfg.get("pass_line", 4) or 4), key=f"joblib_thr_pass_{selected_job}")
        review_line = tcols[1].number_input("复核线", min_value=1, max_value=5, value=int(thr_cfg.get("review_line", 3) or 3), key=f"joblib_thr_review_{selected_job}")
        min_exp = tcols[2].number_input("相关经历最低分", min_value=1, max_value=5, value=int(thr_cfg.get("min_experience", 2) or 2), key=f"joblib_thr_exp_{selected_job}")
        min_skill = tcols[3].number_input("技能最低分", min_value=1, max_value=5, value=int(thr_cfg.get("min_skill", 2) or 2), key=f"joblib_thr_skill_{selected_job}")
        min_expr = tcols[4].number_input("表达完整度最低分", min_value=1, max_value=5, value=int(thr_cfg.get("min_expression", 2) or 2), key=f"joblib_thr_expr_{selected_job}")

        st.caption("硬门槛开关")
        hard_cfg = dict(scoring_cfg.get("hard_thresholds") or scoring_cfg.get("hard_flags") or {})
        hard_opts = _profile_hard_flag_options(selected_profile)
        if hard_opts:
            for hard_key, hard_label in hard_opts:
                hard_cfg[hard_key] = st.checkbox(
                    hard_label,
                    value=bool(hard_cfg.get(hard_key, False)),
                    key=f"joblib_hard_{selected_job}_{hard_key}",
                )
        else:
            st.caption("当前模板无额外硬门槛开关。")

        ai_rule_cfg = {**_default_ai_rule_suggester_config(), **(scoring_cfg.get("ai_rule_suggester") or {})}

        st.markdown("**AI 优化评分细则（预留接口）**")
        st.caption("用于预留未来上云后的模型优化能力；本地未配置密钥时默认走 mock 建议。")
        ai_cols = st.columns(2)
        enable_ai_rule_suggester = ai_cols[0].toggle(
            "启用 AI 评分细则建议",
            value=bool(ai_rule_cfg.get("enable_ai_rule_suggester", False)),
            key=f"joblib_ai_enable_{selected_job}",
        )
        provider = ai_cols[1].selectbox(
            "provider",
            options=["openai", "azure_openai", "anthropic", "mock"],
            index=["openai", "azure_openai", "anthropic", "mock"].index(ai_rule_cfg.get("provider", "openai")) if ai_rule_cfg.get("provider", "openai") in ["openai", "azure_openai", "anthropic", "mock"] else 0,
            key=f"joblib_ai_provider_{selected_job}",
        )
        ai_cols2 = st.columns(3)
        model_name = ai_cols2[0].text_input("model", value=str(ai_rule_cfg.get("model", "gpt-4o-mini") or "gpt-4o-mini"), key=f"joblib_ai_model_{selected_job}")
        api_base = ai_cols2[1].text_input("api_base（可选）", value=str(ai_rule_cfg.get("api_base", "") or ""), key=f"joblib_ai_api_base_{selected_job}")
        api_key_env_name = ai_cols2[2].text_input("api_key_env_name", value=str(ai_rule_cfg.get("api_key_env_name", "OPENAI_API_KEY") or "OPENAI_API_KEY"), key=f"joblib_ai_env_name_{selected_job}")

        if st.button("AI 生成评分细则建议", key=f"joblib_ai_suggest_btn_{selected_job}", use_container_width=True):
            api_key_value = os.getenv(api_key_env_name or "")
            if not enable_ai_rule_suggester or not api_key_value:
                st.info("当前本地未启用 AI 评分细则优化，建议部署到云服务器后启用。")
            suggestion = _build_ai_scoring_stub_suggestion(selected_profile, scoring_cfg, edited_text)
            st.session_state[f"joblib_ai_suggestion_{selected_job}"] = suggestion

        ai_suggestion = st.session_state.get(f"joblib_ai_suggestion_{selected_job}")
        if ai_suggestion:
            st.caption("AI 建议结果（JSON）")
            suggestion_text_default = json.dumps(ai_suggestion, ensure_ascii=False, indent=2)
            suggestion_text = st.text_area(
                "可手动编辑建议后再应用",
                value=st.session_state.get(f"joblib_ai_suggestion_text_{selected_job}", suggestion_text_default),
                height=220,
                key=f"joblib_ai_suggestion_text_{selected_job}",
            )
            st.json(ai_suggestion)
            if st.button("应用建议到当前岗位配置", key=f"joblib_apply_ai_suggestion_{selected_job}", use_container_width=True):
                try:
                    parsed_suggestion = json.loads(suggestion_text)
                    selected_profile = parsed_suggestion.get("role_template") or selected_profile
                    weight_values = parsed_suggestion.get("weights") or weight_values
                    hard_cfg = parsed_suggestion.get("hard_thresholds") or hard_cfg
                    thr_override = parsed_suggestion.get("screening_thresholds") or {}
                    pass_line = int(thr_override.get("pass_line", pass_line))
                    review_line = int(thr_override.get("review_line", review_line))
                    min_exp = int(thr_override.get("min_experience", min_exp))
                    min_skill = int(thr_override.get("min_skill", min_skill))
                    min_expr = int(thr_override.get("min_expression", min_expr))
                    st.success("已将 AI 建议应用到当前草稿，请点击“保存修改”落盘。")
                except (json.JSONDecodeError, TypeError, ValueError):
                    st.warning("AI 建议 JSON 解析失败，请检查格式后重试。")

        st.markdown("**AI 审核员设置区（预留接口）**")
        st.caption("用于候选人评分后的二次审阅；本地阶段优先做配置预留，不强制调用真实 API。")
        reviewer_cfg = {
            **_default_ai_reviewer_config(),
            **(scoring_cfg.get("ai_reviewer") or {}),
            "capabilities": {
                **_default_ai_reviewer_config().get("capabilities", {}),
                **((scoring_cfg.get("ai_reviewer") or {}).get("capabilities") or {}),
            },
            "score_adjustment_limit": {
                **_default_ai_reviewer_config().get("score_adjustment_limit", {}),
                **((scoring_cfg.get("ai_reviewer") or {}).get("score_adjustment_limit") or {}),
            },
        }

        reviewer_top_cols = st.columns(2)
        enable_ai_reviewer = reviewer_top_cols[0].toggle(
            "启用 AI 审核员",
            value=bool(reviewer_cfg.get("enable_ai_reviewer", False)),
            key=f"joblib_ai_reviewer_enable_{selected_job}",
        )
        ai_reviewer_mode = reviewer_top_cols[1].selectbox(
            "ai_reviewer_mode",
            options=["off", "suggest_only", "bounded_override", "human_approve"],
            index=["off", "suggest_only", "bounded_override", "human_approve"].index(reviewer_cfg.get("ai_reviewer_mode", "off")) if reviewer_cfg.get("ai_reviewer_mode", "off") in ["off", "suggest_only", "bounded_override", "human_approve"] else 0,
            key=f"joblib_ai_reviewer_mode_{selected_job}",
            help="off=关闭；suggest_only=仅建议；bounded_override=受限自动修正；human_approve=需人工确认后生效",
        )

        reviewer_model_cols = st.columns(4)
        reviewer_provider = reviewer_model_cols[0].selectbox(
            "provider（审核员）",
            options=["openai", "azure_openai", "anthropic", "mock"],
            index=["openai", "azure_openai", "anthropic", "mock"].index(reviewer_cfg.get("provider", "openai")) if reviewer_cfg.get("provider", "openai") in ["openai", "azure_openai", "anthropic", "mock"] else 0,
            key=f"joblib_ai_reviewer_provider_{selected_job}",
        )
        reviewer_model = reviewer_model_cols[1].text_input(
            "model（审核员）",
            value=str(reviewer_cfg.get("model", "gpt-4o-mini") or "gpt-4o-mini"),
            key=f"joblib_ai_reviewer_model_{selected_job}",
        )
        reviewer_api_base = reviewer_model_cols[2].text_input(
            "api_base（可选）",
            value=str(reviewer_cfg.get("api_base", "") or ""),
            key=f"joblib_ai_reviewer_api_base_{selected_job}",
        )
        reviewer_api_key_env_name = reviewer_model_cols[3].text_input(
            "api_key_env_name（审核员）",
            value=str(reviewer_cfg.get("api_key_env_name", "OPENAI_API_KEY") or "OPENAI_API_KEY"),
            key=f"joblib_ai_reviewer_api_key_env_{selected_job}",
        )

        st.caption("AI 可操作范围")
        cap_cfg = reviewer_cfg.get("capabilities") or {}
        cap_cols = st.columns(3)
        add_evidence_snippets = cap_cols[0].checkbox(
            "可补充关键证据片段",
            value=bool(cap_cfg.get("add_evidence_snippets", True)),
            key=f"joblib_ai_reviewer_cap_evidence_{selected_job}",
        )
        organize_timeline = cap_cols[1].checkbox(
            "可整理关键时间点",
            value=bool(cap_cfg.get("organize_timeline", True)),
            key=f"joblib_ai_reviewer_cap_timeline_{selected_job}",
        )
        suggest_risk_adjustment = cap_cols[2].checkbox(
            "可建议调整风险等级",
            value=bool(cap_cfg.get("suggest_risk_adjustment", False)),
            key=f"joblib_ai_reviewer_cap_risk_{selected_job}",
        )
        cap_cols2 = st.columns(2)
        suggest_score_adjustment = cap_cols2[0].checkbox(
            "可建议调整分数",
            value=bool(cap_cfg.get("suggest_score_adjustment", False)),
            key=f"joblib_ai_reviewer_cap_score_{selected_job}",
        )
        generate_review_summary = cap_cols2[1].checkbox(
            "可生成审核摘要",
            value=bool(cap_cfg.get("generate_review_summary", True)),
            key=f"joblib_ai_reviewer_cap_summary_{selected_job}",
        )

        st.caption("分数修正限制")
        limit_cfg = reviewer_cfg.get("score_adjustment_limit") or {}
        limit_cols = st.columns(3)
        max_delta_per_dimension = limit_cols[0].number_input(
            "单维最大调整幅度",
            min_value=0,
            max_value=2,
            step=1,
            value=int(limit_cfg.get("max_delta_per_dimension", 1) or 1),
            key=f"joblib_ai_reviewer_max_delta_{selected_job}",
        )
        allow_break_hard_thresholds = limit_cols[1].checkbox(
            "是否允许突破硬门槛",
            value=bool(limit_cfg.get("allow_break_hard_thresholds", False)),
            key=f"joblib_ai_reviewer_allow_break_hard_{selected_job}",
        )
        allow_direct_recommendation_change = limit_cols[2].checkbox(
            "是否允许直接改变推荐结论",
            value=bool(limit_cfg.get("allow_direct_recommendation_change", False)),
            key=f"joblib_ai_reviewer_allow_change_decision_{selected_job}",
        )

        if enable_ai_reviewer and not os.getenv(reviewer_api_key_env_name or ""):
            st.info("当前本地未启用 AI 审核员真实调用，建议部署到云服务器后配置 API 再启用。")

        st.session_state.joblib_draft_scoring_config = {
            "profile_name": selected_profile,
            "role_template": selected_profile,
            "weights": weight_values,
            "thresholds": {
                "pass_line": int(pass_line),
                "review_line": int(review_line),
                "min_experience": int(min_exp),
                "min_skill": int(min_skill),
                "min_expression": int(min_expr),
            },
            "screening_thresholds": {
                "pass_line": int(pass_line),
                "review_line": int(review_line),
                "min_experience": int(min_exp),
                "min_skill": int(min_skill),
                "min_expression": int(min_expr),
            },
            "hard_flags": hard_cfg,
            "hard_thresholds": hard_cfg,
            "risk_focus": scoring_cfg.get("risk_focus") if isinstance(scoring_cfg.get("risk_focus"), list) else [],
            "ai_rule_suggester": {
                "enable_ai_rule_suggester": bool(enable_ai_rule_suggester),
                "provider": provider,
                "model": model_name,
                "api_base": api_base,
                "api_key_env_name": api_key_env_name,
            },
            "ai_reviewer": {
                "enable_ai_reviewer": bool(enable_ai_reviewer),
                "ai_reviewer_mode": ai_reviewer_mode,
                "provider": reviewer_provider,
                "model": reviewer_model,
                "api_base": reviewer_api_base,
                "api_key_env_name": reviewer_api_key_env_name,
                "capabilities": {
                    "add_evidence_snippets": bool(add_evidence_snippets),
                    "organize_timeline": bool(organize_timeline),
                    "suggest_risk_adjustment": bool(suggest_risk_adjustment),
                    "suggest_score_adjustment": bool(suggest_score_adjustment),
                    "generate_review_summary": bool(generate_review_summary),
                },
                "score_adjustment_limit": {
                    "max_delta_per_dimension": int(max_delta_per_dimension),
                    "allow_break_hard_thresholds": bool(allow_break_hard_thresholds),
                    "allow_direct_recommendation_change": bool(allow_direct_recommendation_change),
                },
            },
        }

        action_cols = st.columns(5)
        with action_cols[0]:
            if st.button("保存修改", use_container_width=True, key="joblib_update_btn"):
                try:
                    update_jd(selected_job, edited_text, openings=int(edited_openings))
                    upsert_jd_scoring_config(selected_job, st.session_state.get("joblib_draft_scoring_config", {}))
                    _apply_jd_to_workspace(selected_job)
                    _sync_job_management_drafts(selected_job)
                    st.session_state.joblib_flash_success = "岗位已更新。"
                    st.rerun()
                except ValueError as err:
                    st.warning(str(err))
        with action_cols[1]:
            if st.button("仅更新空缺人数", use_container_width=True, key="joblib_update_openings_btn"):
                try:
                    upsert_jd_openings(selected_job, int(edited_openings))
                    upsert_jd_scoring_config(selected_job, st.session_state.get("joblib_draft_scoring_config", {}))
                    _sync_job_management_drafts(selected_job)
                    st.session_state.joblib_flash_success = "空缺人数与评分设置已更新。"
                    st.rerun()
                except ValueError as err:
                    st.warning(str(err))
        with action_cols[2]:
            if st.button("删除岗位", use_container_width=True, key="joblib_delete_btn"):
                try:
                    delete_jd(selected_job)
                    if st.session_state.get("selected_jd_title") == selected_job:
                        st.session_state.selected_jd_title = ""
                        st.session_state.v1_jd_title_draft = ""
                    if st.session_state.get("v2_selected_jd_prev") == selected_job:
                        st.session_state.v2_selected_jd_prev = ""
                        st.session_state.v2_jd_text_area = ""
                    if st.session_state.get("batch_selected_jd_prev") == selected_job:
                        st.session_state.batch_selected_jd_prev = ""
                        st.session_state.batch_jd_text_area = ""
                    _sync_job_management_drafts("")
                    st.session_state.joblib_flash_success = "岗位已删除。"
                    st.rerun()
                except ValueError as err:
                    st.warning(str(err))
        with action_cols[3]:
            if st.button("进入批量初筛", use_container_width=True, key="joblib_use_v1_btn"):
                _apply_jd_to_workspace(selected_job)
                st.success("已定位到该岗位，可前往“批量初筛”继续操作。")
        with action_cols[4]:
            if st.button("进入候选人工作台", use_container_width=True, key="joblib_use_v2_btn"):
                _apply_jd_to_workspace(selected_job)
                st.success("已定位到该岗位，可前往“候选人工作台”继续操作。")

        st.markdown("**历史批次（岗位级候选池）**")
        batch_history = list_candidate_batches_by_jd(selected_job)
        if batch_history:
            st.warning("删除批次后将不可恢复，请确认后再操作。")
            st.dataframe(
                [
                    {
                        "批次ID": item.get("batch_id", "")[:12],
                        "创建时间": item.get("created_at", "-"),
                        "总简历": item.get("total_resumes", item.get("candidate_count", 0)),
                        "通过": item.get("pass_count", 0),
                        "待复核": item.get("review_count", 0),
                        "淘汰": item.get("reject_count", 0),
                    }
                    for item in batch_history
                ],
                use_container_width=True,
                hide_index=True,
            )

            batch_choice = st.selectbox(
                "选择批次进入候选人工作台",
                options=[item.get("batch_id", "") for item in batch_history],
                format_func=lambda bid: f"{bid[:12]}…",
                key="joblib_batch_choice",
            )
            if st.button("打开该批次到候选人工作台", key="joblib_open_batch_btn", use_container_width=True):
                _apply_batch_to_workspace(selected_job, batch_choice)
                st.success("已设置工作台默认批次，请切换到“候选人工作台”查看。")

            batch_delete_cols = st.columns(2)
            with batch_delete_cols[0]:
                if st.button("删除所选批次（不可恢复）", key="joblib_delete_batch_btn", use_container_width=True):
                    if delete_candidate_batch(batch_choice):
                        _after_batch_deleted(selected_job, batch_choice)
                        st.session_state.joblib_flash_success = f"已删除批次：{batch_choice[:12]}…"
                        st.rerun()
                    else:
                        st.warning("未找到可删除的批次，可能已被删除。")
            with batch_delete_cols[1]:
                if st.button("清空该岗位所有批次（高风险）", key="joblib_delete_all_batches_btn", use_container_width=True):
                    deleted_count = delete_batches_by_jd(selected_job)
                    if deleted_count > 0:
                        _after_batch_deleted(selected_job, batch_choice)
                        st.session_state.joblib_flash_success = f"已清空岗位“{selected_job}”的 {deleted_count} 个批次。"
                        st.rerun()
                    else:
                        st.warning("该岗位暂无可删除的批次。")
        else:
            st.caption("该岗位暂无批次记录，请先在“批量初筛”页面执行一次初筛。")

    st.markdown("---")
    st.markdown("**新建岗位**")
    new_title = st.text_input("岗位名称", placeholder="例如：AI 产品经理实习生（2026 春招）", key="joblib_new_title")
    new_openings = st.number_input("初始空缺人数", min_value=0, step=1, value=1, key="joblib_new_openings")
    new_text = st.text_area("JD 内容", height=180, key="joblib_new_text")
    if st.button("新建岗位", type="primary", key="joblib_create_btn"):
        try:
            save_jd(new_title, new_text, openings=int(new_openings))
            _apply_jd_to_workspace((new_title or "").strip())
            _sync_job_management_drafts((new_title or "").strip())
            st.session_state.joblib_flash_success = "岗位创建成功，已同步到批量初筛。"
            st.rerun()
        except ValueError as err:
            st.warning(str(err))

    st.markdown("</div>", unsafe_allow_html=True)


def _render_v1() -> None:
    left_col, right_col = st.columns([1, 1.25], gap="large")

    with left_col:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.markdown("<div class='section-title'>输入表单</div>", unsafe_allow_html=True)

        if "v1_jd_title_draft" not in st.session_state:
            st.session_state.v1_jd_title_draft = st.session_state.get("selected_jd_title", "")

        jd_titles = list_jds()
        st.markdown("**JD 列表**")
        if jd_titles:
            st.caption(" / ".join(jd_titles))
        else:
            st.caption("暂无已保存 JD。")

        options = [""] + jd_titles
        selected_index = options.index(st.session_state.selected_jd_title) if st.session_state.selected_jd_title in options else 0
        st.selectbox(
            "选择已保存 JD",
            options=options,
            index=selected_index,
            format_func=lambda x: x if x else "请选择已保存 JD",
            key="v1_saved_jd_select",
            on_change=_on_v1_saved_jd_change,
        )

        jd_title = st.text_input(
            "JD 标题",
            placeholder="例如：AI 产品经理实习生（2026 春招）",
            key="v1_jd_title_draft",
        )
        effective_title = _normalize_jd_title(jd_title)

        action_cols = st.columns(3)
        with action_cols[0]:
            if st.button("保存 JD", use_container_width=True):
                try:
                    save_jd(effective_title, st.session_state.jd_text)
                    st.success("JD 已保存。")
                except ValueError as err:
                    st.warning(str(err))

        with action_cols[1]:
            if st.button("更新当前 JD", use_container_width=True):
                try:
                    update_jd(effective_title, st.session_state.jd_text)
                    st.success("JD 已更新。")
                except ValueError as err:
                    st.warning(str(err))

        with action_cols[2]:
            if st.button("删除当前 JD", use_container_width=True):
                try:
                    delete_jd(effective_title)
                    st.session_state.jd_text = ""
                    if st.session_state.selected_jd_title == effective_title:
                        st.session_state.selected_jd_title = ""
                        st.session_state.v1_saved_jd_select = ""
                    st.session_state.v1_jd_title_draft = ""
                    st.success("JD 已删除。")
                except ValueError as err:
                    st.warning(str(err))

        resume_file = st.file_uploader(
            "上传简历（txt / pdf / docx / png / jpg / jpeg）", type=["txt", "pdf", "docx", "png", "jpg", "jpeg"], key="v1_resume_upload"
        )
        if resume_file is not None:
            try:
                extract_result = load_resume_file(resume_file)
                st.session_state.resume_text = extract_result.get("text", "")
                st.session_state.v1_extract_method = extract_result.get("method", "text")
                st.session_state.v1_extract_quality = extract_result.get("quality", "weak")
                st.session_state.v1_extract_message = extract_result.get("message", "")
                st.success("已读取上传简历并自动填充到输入框。")
            except Exception as err:  # noqa: BLE001
                st.warning(f"简历读取失败：{_friendly_upload_error(err)}")

        demo_cols = st.columns(2)
        with demo_cols[0]:
            if st.button("填充示例 JD", use_container_width=True):
                st.session_state.jd_text = SAMPLE_JD
        with demo_cols[1]:
            if st.button("填充示例简历", use_container_width=True):
                st.session_state.resume_text = SAMPLE_RESUME

        jd_text = st.text_area("岗位 JD", value=st.session_state.jd_text, height=220, key="jd_text_area")

        method = st.session_state.get("v1_extract_method")
        quality = st.session_state.get("v1_extract_quality")
        message = st.session_state.get("v1_extract_message", "")
        if method or quality:
            st.caption(
                f"提取方式：{_extract_method_label(method or 'text')} ｜ 提取质量：{_extract_quality_label(quality or 'weak')}"
            )
            if message:
                st.caption(f"提取说明：{message}")
            notice = _extract_notice(quality or "weak")
            if notice:
                st.warning(notice)

        preview_text = (st.session_state.resume_text or "").strip()
        if preview_text:
            st.caption("提取到的简历文本预览")
            st.code(_short_text(preview_text, max_len=240), language=None)

        resume_text = st.text_area("候选人简历（可手动编辑）", value=st.session_state.resume_text, height=320, key="resume_text_area")
        st.session_state.jd_text = jd_text
        st.session_state.resume_text = resume_text

        run_btn = st.button("开始评估", type="primary", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with right_col:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.markdown("<div class='section-title'>审核报告</div>", unsafe_allow_html=True)
        if run_btn:
            if not jd_text.strip() or not resume_text.strip():
                st.warning("请先完整填写岗位 JD 和候选人简历。")
            else:
                try:
                    result = _run_pipeline(jd_text, resume_text, jd_title=effective_title)
                    append_review(_build_review_record(result, jd_title=effective_title))

                    st.markdown("<div class='section-title'>1) 初筛结论</div>", unsafe_allow_html=True)
                    _show_decision(result["screening_result"]["screening_result"])
                    for reason in result["screening_result"].get("screening_reasons", []):
                        st.markdown(f"- {reason}")

                    st.markdown("<div class='section-title'>2) 五维评分</div>", unsafe_allow_html=True)
                    _render_score_cards(result["score_details"])

                    st.markdown("<div class='section-title'>3) 风险与建议动作</div>", unsafe_allow_html=True)
                    st.markdown("<div class='module-box'>", unsafe_allow_html=True)
                    risk_result = result["risk_result"]
                    risk_level = risk_result.get("risk_level", "unknown")
                    st.info(f"风险等级：**{_risk_level_label(risk_level)}**")
                    st.caption(risk_result.get("risk_summary", ""))
                    st.markdown(f"**建议动作：** {_risk_action(risk_level)}")
                    for rp in risk_result.get("risk_points", []):
                        st.markdown(f"- ⚠️ {rp}")
                    st.markdown("</div>", unsafe_allow_html=True)

                    st.markdown("<div class='section-title'>4) 面试建议</div>", unsafe_allow_html=True)
                    st.markdown("<div class='module-box'>", unsafe_allow_html=True)
                    plan = result["interview_plan"]
                    st.markdown("**建议追问问题（3-5）**")
                    for q in plan.get("interview_questions", []):
                        st.markdown(f"- {q}")
                    st.markdown("**重点核实能力点（2-4）**")
                    for fp in plan.get("focus_points", []):
                        st.markdown(f"- {fp}")
                    st.markdown("**面试总结**")
                    st.write(plan.get("interview_summary", ""))

                    st.markdown("</div>", unsafe_allow_html=True)

                    st.markdown("<div class='section-title'>5) 关键证据片段</div>", unsafe_allow_html=True)
                    _render_evidence_snippets(result.get("evidence_snippets", []))

                    with st.expander("查看结构化解析结果（调试/扩展）"):
                        st.write("JD 解析结果")
                        st.json(result["parsed_jd"])
                        st.write("简历解析结果")
                        st.json(result["parsed_resume"])
                except Exception as exc:  # noqa: BLE001
                    st.error("评估过程发生异常，请检查输入格式或稍后重试。")
                    st.caption(f"错误信息：{exc}")
        else:
            st.caption("点击“开始评估”后，这里将展示结构化初筛结果。")

        st.markdown("<div class='section-title'>6) 历史审核记录</div>", unsafe_allow_html=True)
        with st.expander("查看历史记录", expanded=False):
            _render_history_records(limit=5)
        st.markdown("</div>", unsafe_allow_html=True)


def _run_batch_screening(jd_title: str, jd_text: str, uploaded_files: list) -> None:
    """批量初筛执行器：仅负责批量解析与自动分流。"""
    rows: list[dict] = []
    details: dict[str, dict] = {}
    progress = st.progress(0)
    total = len(uploaded_files)

    failed_files: list[str] = []
    failed_reasons: list[str] = []
    weak_files: list[str] = []
    ocr_files: list[str] = []

    for idx, file_obj in enumerate(uploaded_files):
        try:
            extract_result = load_resume_file(file_obj)
            resume_text = extract_result.get("text", "")
            method = extract_result.get("method", "text")
            quality = extract_result.get("quality", "weak")
            message = extract_result.get("message", "")
            if method == "ocr":
                ocr_files.append(file_obj.name)
            if quality == "weak":
                weak_files.append(file_obj.name)
        except Exception as err:  # noqa: BLE001
            failed_files.append(file_obj.name)
            failed_reasons.append(f"{file_obj.name}：{_friendly_upload_error(err)}")
            progress.progress((idx + 1) / total)
            continue

        result = _run_pipeline(jd_text, resume_text, jd_title=jd_title)
        row = build_candidate_row(result, source_name=file_obj.name, index=idx)
        row["提取方式"] = method
        row["提取质量"] = quality
        row["提取提示"] = "⚠ 建议人工检查提取文本" if quality == "weak" else ""
        row["提取说明"] = message
        row["处理优先级"] = "普通"
        row["审核摘要"] = _review_summary(
            decision=row.get("初筛结论", ""),
            risk_level=row.get("风险等级", "unknown"),
            risk_summary=row.get("风险摘要", ""),
        )
        row["候选池"] = _candidate_pool_label(row.get("初筛结论", ""))
        rows.append(row)

        detail_payload = dict(result)
        detail_payload["extract_info"] = {
            "file_name": file_obj.name,
            "method": method,
            "quality": quality,
            "message": message,
        }
        detail_payload["raw_resume_text"] = resume_text
        detail_payload["manual_priority"] = row.get("处理优先级", "普通")
        details[row["candidate_id"]] = detail_payload

        review_record = _build_review_record(result, jd_title=jd_title or "批量初筛岗位", resume_file=file_obj.name)
        append_review(review_record)
        details[row["candidate_id"]]["review_id"] = review_record.get("review_id", "")
        progress.progress((idx + 1) / total)

    st.session_state.v2_rows = rows
    st.session_state.v2_details = details

    batch_id = save_candidate_batch(jd_title=jd_title, rows=rows, details=details)
    st.session_state.v2_current_batch_id = batch_id
    st.session_state.workspace_selected_jd_title = (jd_title or "").strip() or "未命名岗位"

    st.success(f"批量初筛完成：成功处理 {len(rows)} 份简历。已保存批次 {batch_id[:12]}…")
    if ocr_files:
        st.info(f"以下简历触发 OCR：{', '.join(ocr_files)}")
    if weak_files:
        st.warning(f"以下简历提取质量较弱，建议人工复核文本：{', '.join(weak_files)}")
    if failed_files:
        st.warning(f"以下文件读取失败：{', '.join(failed_files)}")
        for reason in failed_reasons:
            st.caption(reason)


def _render_candidate_workspace_panel(rows: list[dict], details: dict[str, dict]) -> None:
    """候选人工作台：左侧名单浏览，右侧审核报告。"""
    list_col, detail_col = st.columns([0.9, 1.4], gap="large")

    pass_count = sum(1 for row in rows if _current_candidate_pool(row) == "通过候选人")
    review_count = sum(1 for row in rows if _current_candidate_pool(row) == "待复核候选人")
    reject_count = sum(1 for row in rows if _current_candidate_pool(row) == "淘汰候选人")
    pool_counts = {
        "待复核候选人": review_count,
        "通过候选人": pass_count,
        "淘汰候选人": reject_count,
    }

    with list_col:
        st.subheader("候选人名单")
        selected_pool_label = st.radio(
            "候选池切换",
            options=["待复核候选人", "通过候选人", "淘汰候选人"],
            format_func=lambda label: f"{label}（{pool_counts.get(label, 0)}）",
            horizontal=True,
            key="workspace_pool_top_radio",
        )

        action_feedback = st.session_state.pop("workspace_action_feedback", "")
        pool_move_feedback = st.session_state.pop("workspace_pool_move_feedback", "")
        pool_empty_feedback = st.session_state.pop("workspace_pool_empty_feedback", "")
        if action_feedback:
            st.success(action_feedback)
        if pool_move_feedback:
            st.info(pool_move_feedback)
        if pool_empty_feedback:
            st.warning(pool_empty_feedback)

        search_kw = st.text_input("搜索候选人（姓名）", value="", key="workspace_search")
        risk_filter = st.selectbox(
            "按风险等级筛选",
            options=["全部", "低风险", "中风险", "高风险"],
            key="workspace_risk_filter",
        )
        sort_key = st.selectbox(
            "排序方式",
            options=[
                "处理优先级（高到低）",
                "处理优先级（低到高）",
                "综合得分（高到低）",
                "综合得分（低到高）",
                "风险等级（高到低）",
                "风险等级（低到高）",
            ],
            key="workspace_sort_key",
        )

        filtered_rows = [row for row in rows if _current_candidate_pool(row) == selected_pool_label]
        filtered_rows = search_by_name(filtered_rows, search_kw)
        filtered_rows = filter_by_risk(filtered_rows, risk_filter)
        filtered_rows = sort_rows(filtered_rows, sort_key)

        context_key = "|".join(
            [
                str(st.session_state.get("workspace_selected_jd_title", "")),
                str(st.session_state.get("workspace_preferred_batch_id", "")),
                str(selected_pool_label),
                str((search_kw or "").strip().lower()),
                str(risk_filter),
                str(sort_key),
            ]
        )
        if "workspace_selected_candidate_by_context" not in st.session_state:
            st.session_state.workspace_selected_candidate_by_context = {}
        selected_cache = st.session_state.workspace_selected_candidate_by_context

        candidate_ids = [str(row.get("candidate_id", "")) for row in filtered_rows if row.get("candidate_id")]
        selected_candidate_id = str(selected_cache.get(context_key, ""))
        if selected_candidate_id not in candidate_ids:
            selected_candidate_id = candidate_ids[0] if candidate_ids else ""
            selected_cache[context_key] = selected_candidate_id
            st.session_state.workspace_selected_candidate_by_context = selected_cache

        if filtered_rows:
            for row in filtered_rows:
                row_candidate_id = str(row.get("candidate_id", ""))
                fallback_name = (row.get("文件名") or "未命名候选人").strip()
                display_name = (row.get("姓名") or "").strip() or fallback_name
                is_active = row_candidate_id == selected_candidate_id

                label_prefix = "🟢 当前审核中｜" if is_active else ""
                if st.button(
                    f"{label_prefix}{display_name}",
                    key=f"workspace_list_pick_{context_key}_{row_candidate_id}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    selected_cache[context_key] = row_candidate_id
                    st.session_state.workspace_selected_candidate_by_context = selected_cache
                    st.rerun()

                row_note = [
                    f"候选池：{_current_candidate_pool(row)}",
                    f"风险：{_risk_level_label(row.get('风险等级', 'unknown'))}",
                    f"优先级：{row.get('处理优先级', '普通')}",
                ]
                st.caption(" ｜ ".join(row_note))
                st.caption(f"审核摘要：{row.get('审核摘要', '暂无摘要')} ")
                st.divider()

            st.download_button(
                "导出当前列表 CSV",
                data=rows_to_csv_bytes(filtered_rows),
                file_name="hiremate_workspace_candidates.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            selected_cache[context_key] = ""
            st.session_state.workspace_selected_candidate_by_context = selected_cache
            selected_candidate_id = ""
            st.info("当前筛选结果为空，请切换候选池、筛选条件或批次后继续。")

    with detail_col:
        st.subheader("候选人审核报告")
        if not filtered_rows or not selected_candidate_id or selected_candidate_id not in candidate_ids:
            st.info("当前无可审核候选人。请先在左侧名单中选择候选人。")
            return

        detail = details.get(selected_candidate_id)
        if not detail:
            st.info("当前候选人详情不可用，请在左侧重新选择候选人。")
            return

        cand_id = selected_candidate_id
        selected_row = next((item for item in rows if item.get("candidate_id") == cand_id), {})
        parsed_resume = detail.get("parsed_resume", {})
        extract_info = detail.get("extract_info", {})
        risk_result = detail.get("risk_result", {})

        current_pool = _current_candidate_pool(selected_row) or "未分配"
        auto_decision = detail["screening_result"]["screening_result"]
        manual_decision = detail.get("manual_decision") or "未处理"
        risk_level = risk_result.get("risk_level", "unknown")
        suggested_action = _risk_action(risk_level)
        timeline_summary = _build_timeline_summary(parsed_resume, risk_result)
        timeline_clear = "是" if timeline_summary.get("时间线不清晰风险", "否") == "否" else "否"

        st.caption("审核摘要条")
        summary_cols = st.columns(6)
        summary_cols[0].caption(f"当前候选池\n\n{current_pool}")
        summary_cols[1].caption(f"自动初筛结论\n\n{auto_decision}")
        summary_cols[2].caption(f"人工最终结论\n\n{manual_decision}")
        summary_cols[3].caption(f"风险等级\n\n{_risk_level_label(risk_level)}")
        summary_cols[4].caption(f"时间线状态\n\n{'清晰' if timeline_clear == '是' else '不清晰'}")
        summary_cols[5].caption(f"建议动作\n\n{suggested_action}")

        st.markdown("**1) 候选人概览**")
        name_fallback = (extract_info.get("file_name") or selected_row.get("文件名") or "未命名候选人")
        display_name = (parsed_resume.get("name") or selected_row.get("姓名") or "").strip() or name_fallback

        st.write(f"姓名：{display_name}")
        st.caption(f"文件名：{extract_info.get('file_name') or selected_row.get('文件名') or '-'}")

        st.markdown("**2) 关键时间线**")
        st.write(f"毕业时间：{timeline_summary.get('毕业时间', '未识别')}")
        st.write(f"最近实习/项目时间：{timeline_summary.get('最近实习/项目时间', '未识别')}")
        st.write(f"时间线是否清晰：{timeline_clear}")
        if timeline_summary.get("时间线不清晰风险", "否") == "是":
            st.warning("检测到时间线不清晰风险，建议优先人工核验时间字段。")

        st.markdown("**3) 关键证据片段**")
        _render_evidence_snippets((detail.get("evidence_snippets", []) or [])[:5])

        st.markdown("**4) 风险与建议动作**")
        st.write(f"风险等级：{_risk_level_label(risk_level)}")
        st.write(f"建议动作：{suggested_action}")
        for rp in risk_result.get("risk_points", []):
            st.markdown(f"- ⚠️ {rp}")

        st.markdown("**5) 面试建议**")
        interview_plan = detail.get("interview_plan", {})
        st.caption("建议追问问题")
        for q in interview_plan.get("interview_questions", []):
            st.markdown(f"- {q}")
        st.caption("重点核实点")
        for fp in interview_plan.get("focus_points", []):
            st.markdown(f"- {fp}")
        st.caption(f"面试总结：{interview_plan.get('interview_summary', '')}")

        st.markdown("**6) AI 审核建议**")
        ai_suggestion = detail.get("ai_review_suggestion") or {}
        ai_cfg = ((detail.get("parsed_jd") or {}).get("scoring_config") or {}).get("ai_reviewer") or {}
        ai_mode = str(ai_cfg.get("ai_reviewer_mode") or ai_suggestion.get("mode") or "off")
        ai_enabled = bool(ai_cfg.get("enable_ai_reviewer", False)) and ai_mode != "off"

        if not ai_enabled:
            st.caption("当前岗位未启用 AI 审核员。")
        else:
            if (ai_suggestion.get("meta") or {}).get("source") == "stub":
                st.info("当前为本地预留模式（mock/stub），部署后可启用真实模型。")

            st.caption(f"AI 审核模式：{ai_mode}")
            st.write(f"AI 审核摘要：{ai_suggestion.get('review_summary') or '暂无'}")

            st.caption("AI 补充证据建议")
            evidence_updates = ai_suggestion.get("evidence_updates") or []
            if evidence_updates:
                for item in evidence_updates:
                    st.markdown(f"- [{item.get('source', 'AI')}] {item.get('text', '')}")
            else:
                st.caption("暂无建议。")

            st.caption("AI 关键时间点补充")
            timeline_updates = ai_suggestion.get("timeline_updates") or []
            if timeline_updates:
                for item in timeline_updates:
                    st.markdown(f"- {item.get('label', '时间点')}：{item.get('value', '')}")
            else:
                st.caption("暂无建议。")

            st.caption("AI 风险调整建议")
            risk_adjustment = ai_suggestion.get("risk_adjustment") or {}
            if risk_adjustment:
                st.markdown(f"- 建议风险等级：{_risk_level_label(str(risk_adjustment.get('suggested_risk_level') or 'unknown'))}")
                if risk_adjustment.get("reason"):
                    st.caption(f"说明：{risk_adjustment.get('reason')}")
            else:
                st.caption("暂无建议。")

            st.caption("AI 分数调整建议")
            score_adjustments = ai_suggestion.get("score_adjustments") or []
            if score_adjustments:
                for item in score_adjustments:
                    st.markdown(
                        f"- {item.get('dimension', '-')}: Δ{item.get('suggested_delta', 0)} (max {item.get('max_delta', 1)})"
                    )
                    if item.get("reason"):
                        st.caption(f"  说明：{item.get('reason')}")
            else:
                st.caption("暂无建议。")

            st.caption(f"AI 推荐动作建议：{ai_suggestion.get('recommended_action') or 'no_action'}")

            if ai_mode == "human_approve":
                apply_cols = st.columns(4)
                with apply_cols[0]:
                    if st.button("应用 AI 证据建议", key=f"apply_ai_evidence_{cand_id}", use_container_width=True):
                        added = _apply_ai_evidence_suggestions(detail, evidence_updates)
                        st.session_state.v2_details = details
                        st.success(f"已应用 {added} 条 AI 证据建议。")
                        st.rerun()
                with apply_cols[1]:
                    if st.button("应用 AI 分数建议", key=f"apply_ai_scores_{cand_id}", use_container_width=True):
                        applied = _apply_ai_score_suggestions(detail, selected_row, ai_suggestion)
                        st.session_state.v2_rows = rows
                        st.session_state.v2_details = details
                        st.success(f"已应用 {applied} 项分数建议。")
                        st.rerun()
                with apply_cols[2]:
                    if st.button("应用 AI 风险建议", key=f"apply_ai_risk_{cand_id}", use_container_width=True):
                        ok = _apply_ai_risk_suggestion(detail, selected_row, ai_suggestion)
                        st.session_state.v2_rows = rows
                        st.session_state.v2_details = details
                        st.success("已应用 AI 风险建议。" if ok else "当前无可应用的风险建议。")
                        st.rerun()
                with apply_cols[3]:
                    if st.button("应用 AI 建议", key=f"apply_ai_all_{cand_id}", use_container_width=True):
                        added = _apply_ai_evidence_suggestions(detail, evidence_updates)
                        applied = _apply_ai_score_suggestions(detail, selected_row, ai_suggestion)
                        ok = _apply_ai_risk_suggestion(detail, selected_row, ai_suggestion)
                        st.session_state.v2_rows = rows
                        st.session_state.v2_details = details
                        st.success(f"已应用 AI 建议：证据 {added} 条、分数 {applied} 项、风险 {'已更新' if ok else '无变更'}。")
                        st.rerun()

        st.markdown("**7) 五维评分（辅助信息）**")
        score_details = detail.get("score_details") or {}
        st.caption(
            _score_brief_summary(
                score_details=score_details,
                timeline_summary=timeline_summary,
            )
        )
        with st.expander("展开查看五维评分详情", expanded=False):
            ordered_dims = ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度", "综合推荐度"]
            for dim_name in ordered_dims:
                dim_detail = score_details.get(dim_name) or {}
                score_value = dim_detail.get("score", "-")
                st.markdown(f"**{dim_name}：{score_value}/5**")
                reason = dim_detail.get("reason") or ""
                if reason:
                    st.caption(f"说明：{reason}")
                evidences = dim_detail.get("evidence") or []
                if evidences:
                    st.markdown("证据说明：")
                    for ev in evidences:
                        st.markdown(f"- {ev}")

        st.markdown("**8) 原始提取与解析信息**")
        with st.expander("展开查看原始提取与解析信息", expanded=False):
            method_raw = extract_info.get("method", "text")
            quality_raw = extract_info.get("quality", "weak")
            st.caption(f"提取方式：{_extract_method_label(method_raw)}")
            st.caption(f"提取质量：{_extract_quality_label(quality_raw)}")
            st.caption(f"提取说明：{extract_info.get('message') or '无'}")
            if (quality_raw or "").lower() == "weak":
                st.warning("⚠ 提取质量较弱，建议核对原文")
            raw_text = (detail.get("raw_resume_text") or "").strip()
            if raw_text:
                st.text_area(
                    "提取文本",
                    value=raw_text,
                    height=220,
                    disabled=True,
                    key=f"raw_text_preview_{cand_id}",
                )
            else:
                st.caption("当前批次未保存原始提取文本。")

        review_id = detail.get("review_id", "")
        review_notes = st.session_state.get("v2_manual_review_notes", {})
        review_status = st.session_state.get("v2_manual_review_status", {})

        st.markdown("**9) 人工备注与人工决策**")
        note_value = review_notes.get(cand_id, "")
        note_input = st.text_area(
            "人工备注",
            value=note_value,
            placeholder="记录面试官主观判断、待核验信息、沟通反馈等",
            key=f"manual_note_{cand_id}",
            height=110,
        )
        review_notes[cand_id] = note_input
        st.session_state.v2_manual_review_notes = review_notes
        if st.button("保存人工备注", key=f"manual_note_save_{cand_id}", use_container_width=True):
            if review_id:
                ok = upsert_manual_review(review_id=review_id, manual_note=note_input)
                if ok:
                    st.success("人工备注已写入操作留痕。")
                else:
                    st.warning("未找到对应审核记录，未能写入备注。")
            else:
                st.warning("当前候选人缺少留痕 ID，无法写入备注。")
            active_batch_id = st.session_state.get("workspace_preferred_batch_id", "")
            if active_batch_id:
                upsert_candidate_manual_review(
                    batch_id=active_batch_id,
                    candidate_id=cand_id,
                    manual_note=note_input,
                )

        current_priority = detail.get("manual_priority") or selected_row.get("处理优先级") or "普通"
        priority_options = ["高", "中", "普通", "低"]
        if current_priority not in priority_options:
            current_priority = "普通"
        selected_priority = st.selectbox(
            "处理优先级",
            options=priority_options,
            index=priority_options.index(current_priority),
            key=f"manual_priority_{cand_id}",
        )
        if st.button("保存处理优先级", key=f"manual_priority_save_{cand_id}", use_container_width=True):
            detail["manual_priority"] = selected_priority
            for row_item in rows:
                if row_item.get("candidate_id") == cand_id:
                    row_item["处理优先级"] = selected_priority
                    break
            active_batch_id = st.session_state.get("workspace_preferred_batch_id", "")
            if active_batch_id:
                upsert_candidate_manual_review(
                    batch_id=active_batch_id,
                    candidate_id=cand_id,
                    manual_priority=selected_priority,
                )
            st.session_state.v2_rows = rows
            st.session_state.v2_details = details
            st.success("处理优先级已更新。")
            st.rerun()

        def _apply_manual_decision(manual_decision: str) -> None:
            review_status[cand_id] = manual_decision
            detail["manual_decision"] = manual_decision
            for row in rows:
                if row.get("candidate_id") == cand_id:
                    row["人工最终结论"] = manual_decision
                    break
            if review_id:
                upsert_manual_review(review_id=review_id, manual_decision=manual_decision, manual_note=note_input)
            active_batch_id = st.session_state.get("workspace_preferred_batch_id", "")
            if active_batch_id:
                upsert_candidate_manual_review(
                    batch_id=active_batch_id,
                    candidate_id=cand_id,
                    manual_decision=manual_decision,
                    manual_note=note_input,
                    manual_priority=detail.get("manual_priority") or selected_row.get("处理优先级") or "普通",
                )

            st.session_state.v2_manual_review_status = review_status
            st.session_state.v2_rows = rows
            st.session_state.v2_details = details

            candidate_name = selected_row.get("姓名") or detail.get("parsed_resume", {}).get("name") or "该候选人"
            new_pool = _manual_to_pool(manual_decision) or _current_candidate_pool(selected_row)
            st.session_state.workspace_action_feedback = (
                f"已将候选人“{candidate_name}”移动到“{new_pool}”，并已写入人工备注与操作留痕。"
            )

            if new_pool and new_pool != selected_pool_label:
                selected_cache_local = st.session_state.get("workspace_selected_candidate_by_context", {})
                remaining_ids = [cid for cid in candidate_ids if cid != selected_candidate_id]
                selected_cache_local[context_key] = remaining_ids[0] if remaining_ids else ""
                st.session_state.workspace_selected_candidate_by_context = selected_cache_local
                st.session_state.workspace_pool_move_feedback = f"当前候选人已移至“{new_pool}”。"
                if remaining_ids:
                    st.session_state.workspace_pool_empty_feedback = ""
                else:
                    st.session_state.workspace_pool_empty_feedback = "当前候选池暂无候选人，请切换到其他候选池继续处理。"
            else:
                st.session_state.workspace_pool_move_feedback = ""
                st.session_state.workspace_pool_empty_feedback = ""
            st.rerun()

        tag_cols = st.columns(3)
        with tag_cols[0]:
            if st.button("通过", key=f"manual_pass_{cand_id}", use_container_width=True):
                _apply_manual_decision("通过")
        with tag_cols[1]:
            if st.button("待复核", key=f"manual_pending_{cand_id}", use_container_width=True):
                _apply_manual_decision("待复核")
        with tag_cols[2]:
            if st.button("淘汰", key=f"manual_reject_{cand_id}", use_container_width=True):
                _apply_manual_decision("淘汰")

        st.session_state.v2_manual_review_status = review_status
        current_tag = st.session_state.v2_manual_review_status.get(cand_id)
        if current_tag:
            st.caption(f"当前人工最终决策：{current_tag}")


def _render_batch_screening() -> None:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.subheader("批量初筛")
    st.caption("当前岗位下执行批量初筛：上传简历、检查提取质量、自动分流。")

    jd_titles = list_jds()
    if "batch_selected_jd_prev" not in st.session_state:
        st.session_state.batch_selected_jd_prev = ""
    if "batch_jd_text_area" not in st.session_state:
        st.session_state.batch_jd_text_area = st.session_state.get("v2_jd_text_area") or st.session_state.get("jd_text", "")

    current_jd = (st.session_state.get("batch_selected_jd_prev") or "").strip()
    if not current_jd and jd_titles:
        current_jd = jd_titles[0]
        st.session_state.batch_selected_jd_prev = current_jd
        st.session_state.batch_jd_text_area = load_jd(current_jd)
        st.session_state.v2_jd_text_area = st.session_state.batch_jd_text_area
        st.session_state.v2_selected_jd_prev = current_jd

    if current_jd:
        latest = _latest_batch_snapshot(current_jd)
        st.markdown("<div class='module-box'>", unsafe_allow_html=True)
        st.markdown(f"**当前岗位：{current_jd}**")
        st.caption(f"最近一次批量初筛：{latest.get('latest_time', '-')}")
        st.markdown("筛选规则摘要：通过线（综合达标且无高风险阻断）｜关键维度（经历/技能）｜风险修正（高风险降档）。")
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.warning("当前尚未选择岗位，请先在岗位配置页选择或创建岗位。")

    with st.expander("切换当前岗位", expanded=False):
        selected_jd = st.selectbox(
            "选择岗位（JD）",
            options=[""] + jd_titles,
            index=([""] + jd_titles).index(current_jd) if current_jd in ([""] + jd_titles) else 0,
            format_func=lambda x: x if x else "请选择岗位",
            key="batch_saved_jd_select",
        )
        if selected_jd and selected_jd != st.session_state.batch_selected_jd_prev:
            st.session_state.batch_selected_jd_prev = selected_jd
            jd_text = load_jd(selected_jd)
            st.session_state.batch_jd_text_area = jd_text
            st.session_state.v2_jd_text_area = jd_text
            st.session_state.v2_selected_jd_prev = selected_jd
            st.rerun()

    batch_jd_text = st.text_area("岗位 JD", height=180, key="batch_jd_text_area")
    uploaded_files = st.file_uploader(
        "批量上传简历（txt / pdf / docx / png / jpg / jpeg，可多选）",
        type=["txt", "pdf", "docx", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="batch_uploader",
    )

    st.markdown("**提取质量预检查**")
    st.caption("先检查每份简历的提取方式与提取质量，再执行初筛。")
    if st.button("检查提取方式 / 提取质量", key="batch_preview_btn"):
        if not uploaded_files:
            st.warning("请先上传简历文件。")
        else:
            preview_rows: list[dict] = []
            for file_obj in uploaded_files:
                try:
                    extract_result = load_resume_file(file_obj)
                    preview_rows.append(
                        {
                            "文件名": file_obj.name,
                            "提取方式": _extract_method_label(extract_result.get("method", "text")),
                            "提取质量": _extract_quality_label(extract_result.get("quality", "weak")),
                            "提取说明": extract_result.get("message", ""),
                        }
                    )
                except Exception as err:  # noqa: BLE001
                    preview_rows.append(
                        {
                            "文件名": file_obj.name,
                            "提取方式": "-",
                            "提取质量": "较弱",
                            "提取说明": _friendly_upload_error(err),
                        }
                    )
            st.session_state.batch_extract_preview = preview_rows

    preview_rows = st.session_state.get("batch_extract_preview", [])
    if preview_rows:
        st.dataframe(preview_rows, use_container_width=True, hide_index=True)

    if st.button("开始批量初筛", type="primary", key="batch_run_btn"):
        if not batch_jd_text.strip():
            st.warning("请先填写 JD。")
        elif not uploaded_files:
            st.warning("请至少上传一份简历文件。")
        else:
            effective_jd_title = (st.session_state.get("batch_selected_jd_prev") or "").strip() or "未命名岗位"
            _run_batch_screening(jd_title=effective_jd_title, jd_text=batch_jd_text, uploaded_files=uploaded_files)

    rows = st.session_state.get("v2_rows", [])
    if rows:
        pass_count = sum(1 for row in rows if row.get("候选池") == "通过候选人")
        review_count = sum(1 for row in rows if row.get("候选池") == "待复核候选人")
        reject_count = sum(1 for row in rows if row.get("候选池") == "淘汰候选人")

        active_jd = (st.session_state.get("batch_selected_jd_prev") or "").strip() or "未命名岗位"
        active_batch_id = st.session_state.get("v2_current_batch_id", "")
        batch_created_at = "-"
        batch_total_resumes = len(rows)
        if active_batch_id:
            current_batch = load_candidate_batch(active_batch_id)
            if current_batch:
                batch_created_at = current_batch.get("created_at", "-")
                batch_total_resumes = int(current_batch.get("total_resumes", len(rows)) or len(rows))

        st.markdown("<div class='module-box'>", unsafe_allow_html=True)
        st.markdown("**✅ 批次完成卡片**")
        st.caption(
            f"当前岗位：{active_jd} ｜ 批次时间：{batch_created_at} ｜ 总简历数：{batch_total_resumes}"
        )
        card_cols = st.columns(3)
        card_cols[0].metric("通过人数", pass_count)
        card_cols[1].metric("待复核人数", review_count)
        card_cols[2].metric("淘汰人数", reject_count)
        st.caption("下一步：进入该批次候选池进行人工审核。")
        st.caption("默认优先进入待复核候选人池；若无待复核，则进入通过候选人池。")
        if st.button("进入该批次候选池", type="primary", key="go_workspace_from_batch", use_container_width=True):
            preferred_pool = "待复核候选人" if review_count > 0 else "通过候选人"
            _apply_batch_to_workspace(active_jd, active_batch_id, preferred_pool=preferred_pool)
            _request_page_navigation("候选人工作台")
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("**分流结果总览**")
        col1, col2, col3 = st.columns(3)
        col1.metric("通过候选人", pass_count)
        col2.metric("待复核候选人", review_count)
        col3.metric("淘汰候选人", reject_count)

        st.caption("已完成初筛。请前往“候选人工作台”进行逐个审核与人工决策。")
        pool_display_order = ["通过候选人", "待复核候选人", "淘汰候选人"]
        for pool_name in pool_display_order:
            pool_rows = [row for row in rows if row.get("候选池") == pool_name]
            st.markdown(f"**{pool_name}（{len(pool_rows)}）**")
            if not pool_rows:
                st.caption("暂无候选人")
                continue
            st.dataframe(
                [
                    {
                        "姓名": row.get("姓名", ""),
                        "初筛结论": row.get("初筛结论", ""),
                        "风险等级": _risk_level_label(row.get("风险等级", "unknown")),
                        "审核摘要": row.get("审核摘要", ""),
                        "提取质量": _extract_quality_label(row.get("提取质量", "weak")),
                    }
                    for row in pool_rows
                ],
                use_container_width=True,
                hide_index=True,
            )

    if st.session_state.get("dev_debug_mode", False):
        st.markdown("---")
        st.markdown("### 开发辅助：单份审核（调试入口）")
        st.caption("仅用于开发调试/演示，不属于主产品流程。")
        _render_v1()

    st.markdown("</div>", unsafe_allow_html=True)


def _render_candidate_workspace() -> None:
    st.subheader("候选人工作台")
    st.caption("当前岗位 + 当前批次的候选池审核台。")

    jd_titles = list_candidate_jd_titles()
    if not jd_titles:
        rows = st.session_state.get("v2_rows", [])
        details = st.session_state.get("v2_details", {})
        if not rows:
            st.info("暂无候选池数据，请先在“批量初筛”页面上传简历并运行初筛。")
            return
        st.caption("当前展示的是本次会话结果（尚未检测到持久化岗位候选池）。")
        _render_candidate_workspace_panel(rows, details)
        return

    default_jd = st.session_state.get("workspace_selected_jd_title", "")
    default_jd_index = jd_titles.index(default_jd) if default_jd in jd_titles else 0
    selected_jd = jd_titles[default_jd_index]
    st.session_state.workspace_selected_jd_title = selected_jd

    batch_summaries = list_candidate_batches_by_jd(selected_jd)
    if not batch_summaries:
        st.info("该岗位暂无候选池批次，请先在“批量初筛”生成结果。")
        return

    batch_options = {
        (
            f"{item.get('created_at', '-')}｜"
            f"总{item.get('total_resumes', item.get('candidate_count', 0))} ｜"
            f"通过{item.get('pass_count', 0)} ｜"
            f"待复核{item.get('review_count', 0)} ｜"
            f"淘汰{item.get('reject_count', 0)}"
        ): item.get("batch_id", "")
        for item in batch_summaries
    }
    batch_labels = list(batch_options.keys())
    preferred_batch_id = st.session_state.get("workspace_preferred_batch_id", "")
    default_batch_index = 0
    if preferred_batch_id:
        for idx, label in enumerate(batch_labels):
            if batch_options.get(label) == preferred_batch_id:
                default_batch_index = idx
                break

    selected_batch_label = batch_labels[default_batch_index]
    selected_batch_id = batch_options.get(selected_batch_label, "")
    st.session_state.workspace_preferred_batch_id = selected_batch_id

    payload = load_candidate_batch(selected_batch_id)
    if payload is None:
        payload = load_latest_batch_by_jd(selected_jd)
    if payload is None:
        st.info("未读取到可用候选池批次，请先在“批量初筛”生成结果。")
        return

    rows = payload.get("rows", [])
    details = payload.get("details", {})
    current_pool = st.session_state.get("workspace_pool_top_radio", "")
    if current_pool not in {"待复核候选人", "通过候选人", "淘汰候选人"}:
        current_pool = "待复核候选人" if int(payload.get("review_count", 0) or 0) > 0 else "通过候选人"
        st.session_state.workspace_pool_top_radio = current_pool
    st.session_state.workspace_default_entry_pool = current_pool

    st.markdown("### 工作台上下文")
    st.caption(
        f"当前岗位：{selected_jd} ｜ 当前批次：{(selected_batch_id or payload.get('batch_id', ''))[:12]}… ｜ 当前候选池：{current_pool}"
    )
    st.caption(
        f"当前批次总人数：{payload.get('total_resumes', len(rows))} ｜ "
        f"通过：{payload.get('pass_count', 0)} ｜ 待复核：{payload.get('review_count', 0)} ｜ 淘汰：{payload.get('reject_count', 0)}"
    )

    with st.expander("切换岗位/批次", expanded=False):
        chosen_jd = st.selectbox("选择岗位候选池", options=jd_titles, index=default_jd_index, key="workspace_jd_switch")
        switch_batches = list_candidate_batches_by_jd(chosen_jd)
        switch_options = {
            (
                f"{item.get('created_at', '-')}｜"
                f"总{item.get('total_resumes', item.get('candidate_count', 0))} ｜"
                f"通过{item.get('pass_count', 0)} ｜"
                f"待复核{item.get('review_count', 0)} ｜"
                f"淘汰{item.get('reject_count', 0)}"
            ): item.get("batch_id", "")
            for item in switch_batches
        }
        switch_labels = list(switch_options.keys()) or ["无可用批次"]
        chosen_batch_label = st.selectbox("选择批次", options=switch_labels, key="workspace_batch_switch")
        if st.button("应用切换", key="workspace_apply_switch", use_container_width=True):
            if switch_options:
                _apply_batch_to_workspace(chosen_jd, switch_options.get(chosen_batch_label, ""))
                st.rerun()
            else:
                st.warning("该岗位暂无可切换批次。")

        st.caption("删除提示：删除当前批次后会自动切换到该岗位最新剩余批次。")
        if st.button("删除当前批次（不可恢复）", key="workspace_delete_current_batch", use_container_width=True):
            current_bid = st.session_state.get("workspace_preferred_batch_id", "")
            if not current_bid:
                st.warning("当前没有可删除的批次。")
            elif delete_candidate_batch(current_bid):
                _after_batch_deleted(selected_jd, current_bid)
                st.session_state.workspace_action_feedback = f"已删除当前批次：{current_bid[:12]}…"
                st.rerun()
            else:
                st.warning("当前批次删除失败，可能已被删除。")

    st.session_state.v2_rows = rows
    st.session_state.v2_details = details

    if not rows:
        st.info("当前批次暂无候选人，请返回“批量初筛”上传简历，或在上方切换其他批次继续审核。")
        return

    _render_candidate_workspace_panel(rows, details)

st.set_page_config(page_title="HireMate", page_icon="🧠", layout="wide")
_inject_page_style()
_render_hero()

st.sidebar.markdown("### 开发调试")
st.sidebar.checkbox(
    "显示单份审核调试入口",
    key="dev_debug_mode",
    help="开启后可在“批量初筛”页面底部看到单份审核调试入口。",
)

if "jd_text" not in st.session_state:
    st.session_state.jd_text = ""
if "resume_text" not in st.session_state:
    st.session_state.resume_text = ""
if "selected_jd_title" not in st.session_state:
    st.session_state.selected_jd_title = ""
if "v1_jd_title_draft" not in st.session_state:
    st.session_state.v1_jd_title_draft = ""
if "v1_extract_method" not in st.session_state:
    st.session_state.v1_extract_method = ""
if "v1_extract_quality" not in st.session_state:
    st.session_state.v1_extract_quality = ""
if "v1_extract_message" not in st.session_state:
    st.session_state.v1_extract_message = ""
if "v2_manual_review_notes" not in st.session_state:
    st.session_state.v2_manual_review_notes = {}
if "v2_manual_review_status" not in st.session_state:
    st.session_state.v2_manual_review_status = {}
if "active_page" not in st.session_state:
    st.session_state.active_page = "岗位配置页"
if "joblib_selected_job" not in st.session_state:
    st.session_state.joblib_selected_job = ""
if "joblib_draft_text" not in st.session_state:
    st.session_state.joblib_draft_text = ""
if "joblib_draft_openings" not in st.session_state:
    st.session_state.joblib_draft_openings = 0
if "joblib_draft_scoring_config" not in st.session_state:
    st.session_state.joblib_draft_scoring_config = _normalize_scoring_config(build_default_scoring_config("AI产品经理 / 大模型产品经理"))

pages = ["岗位配置页", "批量初筛", "候选人工作台"]
pending_page = (st.session_state.get("pending_navigation_page") or "").strip()
if pending_page in pages:
    st.session_state.active_page = pending_page
st.session_state.pending_navigation_page = ""

default_idx = pages.index(st.session_state.active_page) if st.session_state.active_page in pages else 0
active_page_nav = st.sidebar.radio("页面导航", options=pages, index=default_idx, key="active_page_nav")
st.session_state.active_page = active_page_nav

if st.session_state.active_page == "岗位配置页":
    _render_job_library()
elif st.session_state.active_page == "批量初筛":
    _render_batch_screening()
else:
    _render_candidate_workspace()
