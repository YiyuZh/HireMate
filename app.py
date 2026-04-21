"""HireMate Streamlit 页面（岗位库 + 批量初筛 + 候选人工作台）。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import html
import json
import os
import re
import sys
from pathlib import Path
from time import perf_counter
import traceback
from uuid import uuid4

import streamlit as st

from src.auth import (
    authenticate_user,
    create_user_account,
    get_current_user,
    login_user,
    logout_user,
    mark_login_success,
    reset_user_password,
)
from src.db import get_connection, get_db_backend, init_db
from src.interviewer import build_interview_plan
from src.legacy_json_compat import migrate_legacy_json_if_needed
from src.utils import load_env
from src.candidate_store import (
    acquire_candidate_lock,
    can_user_operate_candidate,
    cleanup_expired_candidate_locks,
    delete_batch as delete_candidate_batch,
    delete_batches_by_jd,
    get_candidate_lock_state,
    list_batch_candidate_lock_states,
    list_recent_lock_events,
    list_batches_by_jd as list_candidate_batches_by_jd,
    list_jd_titles as list_candidate_jd_titles,
    load_batch as load_candidate_batch,
    load_latest_batch_by_jd,
    persist_candidate_snapshot,
    release_candidate_lock,
    refresh_candidate_lock,
    save_candidate_batch,
    upsert_candidate_manual_review,
)
from src.jd_parser import parse_jd
from src.jd_loader import load_jd_file
from src.jd_store import delete_jd, list_jd_records, list_jds, load_jd, save_jd, update_jd, upsert_jd_openings
from src.jd_store import load_jd_scoring_config, upsert_jd_scoring_config
from src.role_profiles import (
    BASE_WEIGHT_KEYS,
    build_default_scoring_config,
    detect_role_profile,
    get_profile_by_name,
    get_profile_options,
    is_weight_total_valid,
    merge_scoring_config,
    normalize_weights,
    weight_total,
)
from src.resume_loader import check_ocr_capabilities, load_resume_file
from src.resume_parser import normalize_resume_ocr_text, parse_resume
from src.review_store import append_review, list_reviews, upsert_manual_review
from src.risk_analyzer import analyze_risk
from src.ai_reviewer import (
    get_latest_ai_call_status,
    get_ai_model_presets,
    get_ai_provider_options,
    get_ai_reviewer_prompt_version,
    get_default_ai_api_base,
    get_default_ai_api_key_env_name,
    get_default_ai_model,
    provider_requires_explicit_api_base,
    resolve_ai_api_base,
    resolve_ai_api_key_env_name,
    resolve_ai_runtime_config,
    run_ai_reviewer,
    run_ai_rule_suggester,
    test_ai_connection,
)
from src.scorer import score_candidate, to_score_values
from src.screener import build_evidence_bridge, build_screening_decision, collect_evidence_snippets
from src.user_store import count_users, get_user_by_id, list_users, set_user_active, set_user_admin
from src.v2_workspace import (
    build_candidate_row,
    filter_by_risk,
    rows_to_csv_bytes,
    search_by_name,
    sort_rows,
)
from src.analysis_pipeline import run_analysis_pipeline

load_env()
_APP_DB_INIT_ERROR: Exception | None = None
_APP_DB_INIT_TRACEBACK = ""
_APP_DB_INIT_BACKEND = get_db_backend()

try:
    init_db()
    if os.getenv("HIREMATE_AUTO_MIGRATE_JSON", "0").strip() == "1":
        migrate_legacy_json_if_needed()
except Exception as exc:  # noqa: BLE001
    _APP_DB_INIT_ERROR = exc
    _APP_DB_INIT_TRACEBACK = traceback.format_exc()
    print(
        f"[HireMate] Database initialization failed for backend={_APP_DB_INIT_BACKEND}: {exc}",
        file=sys.stderr,
    )
    traceback.print_exc()

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


def _collect_evidence_snippets(parsed_resume: dict, parsed_jd: dict | None = None, max_items: int = 5) -> list[dict]:
    return collect_evidence_snippets(
        parsed_resume,
        parsed_jd=parsed_jd if isinstance(parsed_jd, dict) else {},
        limit=max_items,
    )


def _dimension_chip_label(dimension: str) -> str:
    mapping = {
        "教育背景匹配度": "教育",
        "相关经历匹配度": "经历",
        "技能匹配度": "技能",
        "表达完整度": "表达",
        "综合推荐度": "综合",
    }
    return mapping.get(str(dimension or "").strip(), str(dimension or "其他"))


def _sync_detail_evidence_bridge(detail: dict) -> dict:
    score_details = detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {}
    evidence_snippets = detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else []
    bridge = build_evidence_bridge(score_details, evidence_snippets)
    if isinstance(bridge.get("score_details"), dict):
        detail["score_details"] = bridge["score_details"]
    if isinstance(bridge.get("summary_snippets"), list):
        detail["evidence_snippets"] = bridge["summary_snippets"]
    detail["evidence_bridge"] = bridge if isinstance(bridge, dict) else {}
    return detail.get("evidence_bridge") if isinstance(detail.get("evidence_bridge"), dict) else {}


def _normalize_representative_evidence(item: dict | None) -> dict:
    payload = item if isinstance(item, dict) else {}
    display_text = str(payload.get("display_text") or payload.get("text") or "").strip()
    raw_text = str(payload.get("raw_text") or payload.get("raw") or display_text).strip()
    label = str(payload.get("label") or "代表证据").strip() or "代表证据"
    raw_tags = payload.get("tags")
    tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()] if isinstance(raw_tags, list) else []
    return {
        "dimension": str(payload.get("dimension") or "").strip(),
        "score": payload.get("score"),
        "label": label,
        "display_text": display_text,
        "raw_text": raw_text,
        "text": display_text,
        "raw": raw_text,
        "tags": tags,
        "is_low_readability": bool(payload.get("is_low_readability")),
        "linked_snippet_id": str(payload.get("linked_snippet_id") or "").strip(),
    }


def _remaining_dimension_evidence(detail: dict) -> list[str]:
    evidence = detail.get("evidence")
    if not isinstance(evidence, list):
        evidence = [str(evidence)] if evidence else []
    representative = _normalize_representative_evidence(
        detail.get("representative_evidence") if isinstance(detail.get("representative_evidence"), dict) else {}
    )
    representative_raw = str(representative.get("raw_text") or representative.get("display_text") or "").strip()
    if not representative_raw:
        return evidence
    representative_display = str(representative.get("display_text") or "").strip()
    filtered: list[str] = []
    for item in evidence:
        candidate = str(item or "").strip()
        if not candidate:
            continue
        if candidate == representative_raw or candidate == representative_display:
            continue
        filtered.append(candidate)
    return filtered


def _render_dimension_evidence_summary(score_details: dict, evidence_bridge: dict | None = None) -> None:
    bridge = evidence_bridge if isinstance(evidence_bridge, dict) else {}
    rows = bridge.get("dimension_evidence") if isinstance(bridge.get("dimension_evidence"), list) else []
    if not rows:
        rows = []
        ordered_dims = ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度", "综合推荐度"]
        for dim_name in ordered_dims:
            dim_detail = score_details.get(dim_name) if isinstance(score_details.get(dim_name), dict) else {}
            representative = dim_detail.get("representative_evidence") if isinstance(dim_detail.get("representative_evidence"), dict) else {}
            if representative:
                rows.append(representative)

    if not rows:
        st.caption("当前未提取到维度代表证据。")
        return

    for item in rows:
        representative = _normalize_representative_evidence(item if isinstance(item, dict) else {})
        dim_name = str(representative.get("dimension") or "其他")
        score_value = str(representative.get("score") or "-")
        label = str(representative.get("label") or "代表证据").strip()
        display_text = str(representative.get("display_text") or "").strip()
        raw_text = str(representative.get("raw_text") or display_text).strip()
        if not display_text:
            continue
        chips = [f"<span class='chip'>{html.escape(_dimension_chip_label(dim_name))} {html.escape(score_value)}/5</span>"]
        if label:
            chips.append(f"<span class='chip'>{html.escape(label)}</span>")
        for tag in representative.get("tags", []):
            chips.append(f"<span class='chip'>{html.escape(str(tag))}</span>")
        if str(representative.get("linked_snippet_id") or "").strip():
            chips.append("<span class='chip'>摘要层已展示</span>")
        chip_row = "".join(chips)
        safe_display_text = html.escape(display_text).replace("\n", "<br>")
        st.markdown(
            f"<div class='module-box'>{chip_row}<div>{safe_display_text}</div></div>",
            unsafe_allow_html=True,
        )
        if bool(representative.get("is_low_readability")):
            st.caption("原文识别质量较弱，建议结合原始提取信息复核。")
        if raw_text and raw_text != display_text:
            with st.expander(f"查看原文片段：{label}", expanded=False):
                st.caption(raw_text)


def _decision_summary(result_text: str) -> str:
    if result_text == "推荐进入下一轮":
        return "岗位匹配度较高，可进入后续流程。"
    if result_text == "建议人工复核":
        return "存在待核验点，建议结构化追问后再决策。"
    if result_text == "暂不推荐":
        return "关键能力证据不足，与岗位要求存在明显差距。"
    return "建议结合面试与业务需求进一步判断。"


def _show_decision(result_text: str, reasons: list[str] | None = None) -> None:
    style = "status-success"
    if result_text == "建议人工复核":
        style = "status-warning"
    elif result_text == "暂不推荐":
        style = "status-error"

    summary = _decision_summary(result_text)
    if result_text in {"建议人工复核", "暂不推荐"} and reasons:
        first_reason = str(reasons[0] or "").strip()
        if first_reason:
            summary = _short_text(first_reason, max_len=72)
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


def _extract_notice(quality: str, message: str = "") -> str:
    if _is_ocr_missing_message(message):
        return "⚠️ 当前环境 OCR 能力缺失，建议改用 txt/docx，或在云上补齐 tesseract / poppler 后再试。"
    if (quality or "").lower() == "weak" and "ocr" in str(message or "").lower():
        return "⚠️ 当前 OCR 识别仍偏弱，清洗稿已尽量修复标题、时间与段落结构，但关键字段仍建议人工复核。"
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


def _is_ocr_missing_message(message: str) -> bool:
    msg = (message or "").lower()
    keywords = [
        "未启用图片 ocr",
        "ocr 不可用",
        "未安装",
        "未启用 ocr",
        "pdf ocr 需要",
        "图片 ocr 需要",
        "tesseract",
        "poppler",
        "pdfinfo",
        "pdftoppm",
    ]
    return any(k in msg for k in keywords)


def _resolve_parse_status(quality: str, message: str) -> str:
    if _is_ocr_missing_message(message):
        return "OCR能力缺失"
    if (quality or "").lower() == "ok":
        return "正常识别"
    return "弱质量识别"


def _extract_parse_status(extract_result: dict) -> str:
    return str(
        extract_result.get("parse_status")
        or _resolve_parse_status(
            str(extract_result.get("quality") or "weak"),
            str(extract_result.get("message") or ""),
        )
    )


def _can_enter_batch_screening(extract_result: dict) -> bool:
    if bool(extract_result.get("should_skip")):
        return False
    return bool(extract_result.get("can_evaluate", True))


def _batch_screening_entry_label(extract_result: dict) -> str:
    if not _can_enter_batch_screening(extract_result):
        return "否（建议跳过或人工处理）"
    if str(extract_result.get("quality") or "weak").lower() == "weak":
        return "是（建议人工复核）"
    return "是"


def _render_batch_ocr_health_panel(ocr_caps: dict) -> None:
    image_ok = bool(ocr_caps.get("image_ocr_available"))
    pdf_ok = bool(ocr_caps.get("pdf_ocr_available"))
    missing_deps = ", ".join(ocr_caps.get("missing_deps") or []) or "-"
    missing_runtime = ", ".join(ocr_caps.get("missing_runtime") or []) or "-"

    st.markdown("**OCR 健康检查**")
    cols = st.columns(4)
    cols[0].metric("图片 OCR", "可用" if image_ok else "不可用")
    cols[1].metric("PDF OCR fallback", "可用" if pdf_ok else "不可用")
    cols[2].metric("缺失 Python 依赖", missing_deps)
    cols[3].metric("缺失 runtime", missing_runtime)
    if not image_ok or not pdf_ok:
        st.warning("当前 OCR 能力不完整：txt/docx 可继续，可提文本的 PDF 也可继续；扫描版 PDF/图片可能不可稳定识别。")


def _build_batch_preview_row(file_obj, extract_result: dict) -> dict:
    return {
        "文件名": getattr(file_obj, "name", ""),
        "提取方式": _extract_method_label(str(extract_result.get("method") or "text")),
        "提取质量": _extract_quality_label(str(extract_result.get("quality") or "weak")),
        "提取说明": str(extract_result.get("message") or ""),
        "解析状态": _extract_parse_status(extract_result),
        "是否可进入批量初筛": _batch_screening_entry_label(extract_result),
    }


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


def _db_init_troubleshooting_tips(backend: str) -> list[str]:
    if backend == "mysql":
        return [
            "如果 MySQL 8 使用 `caching_sha2_password` 或 `sha256_password`，请确认镜像内已安装 `cryptography`。",
            "确认 MySQL 服务已经启动，并检查 `HIREMATE_MYSQL_HOST`、`HIREMATE_MYSQL_PORT`、`HIREMATE_MYSQL_USER`、`HIREMATE_MYSQL_PASSWORD`、`HIREMATE_MYSQL_DATABASE` 是否正确。",
            "如果卡在 schema bootstrap，请确认数据库账号具备建表/建索引权限，并检查 `sql/mysql_schema.sql`、`sql/mysql_indexes.sql` 是否可正常执行。",
        ]
    return [
        "确认 SQLite 数据目录存在且当前进程可写。",
        "如果正在使用容器挂载卷，请确认 `/app/data` 对应用进程可写。",
    ]


def _render_db_init_error_page() -> None:
    backend = _APP_DB_INIT_BACKEND or get_db_backend()
    st.error(f"数据库初始化失败，当前 backend：`{backend}`")
    st.caption("HireMate 没有静默降级到其他数据库后端，请先排查当前错误。")
    st.markdown("**常见排查建议**")
    for tip in _db_init_troubleshooting_tips(backend):
        st.markdown(f"- {tip}")
    st.markdown("**原始异常摘要**")
    st.code(str(_APP_DB_INIT_ERROR) or "unknown database initialization error", language="text")
    with st.expander("查看原始异常栈", expanded=False):
        st.code(_APP_DB_INIT_TRACEBACK or "traceback unavailable", language="text")


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
                representative = _normalize_representative_evidence(
                    detail.get("representative_evidence") if isinstance(detail.get("representative_evidence"), dict) else {}
                )
                representative_text = str(representative.get("display_text") or "").strip()
                if representative_text:
                    st.markdown(f"**代表证据：** {representative_text}")
                if bool(representative.get("is_low_readability")):
                    st.caption("原文识别质量较弱，建议结合原始提取信息复核。")

                evidence = detail.get("evidence")
                if evidence:
                    with st.expander("查看证据", expanded=False):
                        remaining_evidence = _remaining_dimension_evidence(detail)
                        if isinstance(remaining_evidence, list):
                            for item in remaining_evidence:
                                st.markdown(f"- {item}")
                        else:
                            st.write(remaining_evidence)
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
    selected_row["风险等级"] = new_level
    reason = str(risk_adjustment.get("reason") or "").strip()
    if reason:
        risk_result["risk_summary"] = reason
        selected_row["风险摘要"] = reason
    detail["risk_result"] = risk_result
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
        evidence = current.get("evidence")
        if not isinstance(evidence, list):
            evidence = [str(evidence)] if evidence else []
        note = f"AI建议修正：{item.get('reason') or '无'}"
        if note not in evidence:
            evidence.append(note)
        if new_score == base_score and note in evidence and bounded == 0:
            continue
        current["score"] = new_score
        current["evidence"] = evidence
        score_details[dim] = current
        if dim in {"教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度"}:
            selected_row[dim] = new_score
        applied += 1
    detail["score_details"] = score_details
    if applied > 0:
        _recalculate_overall_score(detail)
        detail["score_values"] = to_score_values(detail["score_details"])
    return applied


def _recalculate_overall_score(detail: dict) -> None:
    score_details = detail.get("score_details")
    if not isinstance(score_details, dict):
        return

    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    raw_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    template_name = raw_cfg.get("role_template") or raw_cfg.get("profile_name")
    base_profile = get_profile_by_name(template_name) if template_name else detect_role_profile(parsed_jd)
    resolved_cfg, _ = merge_scoring_config(base_profile, raw_cfg)

    base_dims = list(BASE_WEIGHT_KEYS)
    weights = resolved_cfg.get("weights") if isinstance(resolved_cfg.get("weights"), dict) else {}

    weighted_sum = 0.0
    weight_total = 0.0
    low_count = 0
    exp_score = 1
    skill_score = 1

    for dim in base_dims:
        dim_detail = score_details.get(dim) or {}
        try:
            dim_score = int(dim_detail.get("score", 1) or 1)
        except (TypeError, ValueError):
            dim_score = 1
        try:
            dim_weight = float(weights.get(dim, 1.0) or 1.0)
        except (TypeError, ValueError):
            dim_weight = 1.0

        weighted_sum += dim_score * dim_weight
        weight_total += dim_weight
        if dim_score <= 2:
            low_count += 1
        if dim == "相关经历匹配度":
            exp_score = dim_score
        if dim == "技能匹配度":
            skill_score = dim_score

    if weight_total <= 0:
        return

    overall_score = round(weighted_sum / weight_total)
    if exp_score <= 2 or skill_score <= 2:
        overall_score = min(overall_score, 3)
    if low_count >= 2:
        overall_score = min(overall_score, 2)
    if not (exp_score >= 4 and skill_score >= 4):
        overall_score = min(overall_score, 4)

    overall_detail = score_details.get("综合推荐度") or {}
    evidence = overall_detail.get("evidence")
    if not isinstance(evidence, list):
        evidence = [str(evidence)] if evidence else []
    note = "AI建议应用后按当前维度评分重算综合推荐度。"
    if note not in evidence:
        evidence.append(note)

    overall_detail["score"] = max(1, min(5, int(overall_score)))
    overall_detail["reason"] = overall_detail.get("reason") or "综合推荐度已根据当前维度评分重算。"
    overall_detail["evidence"] = evidence
    score_details["综合推荐度"] = overall_detail
    detail["score_details"] = score_details


def _review_summary(decision: str, risk_level: str, risk_summary: str = "") -> str:
    decision_text = _decision_summary(decision)
    risk_text = _risk_level_label(risk_level)
    risk_hint = (risk_summary or "").strip()
    if risk_hint:
        return f"{decision_text} 风险等级：{risk_text}，重点：{_short_text(risk_hint, 36)}"
    return f"{decision_text} 风险等级：{risk_text}。"


def _apply_ai_timeline_updates(detail: dict, ai_suggestion: dict) -> int:
    updates = ai_suggestion.get("timeline_updates") if isinstance(ai_suggestion, dict) else []
    if not isinstance(updates, list):
        return 0

    existing = detail.get("ai_timeline_updates_snapshot")
    if not isinstance(existing, list):
        existing = []
    existing_pairs = {
        (str(item.get("label") or ""), str(item.get("value") or ""))
        for item in existing
        if isinstance(item, dict)
    }

    added = 0
    for item in updates:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not label or not value:
            continue
        pair = (label, value)
        if pair in existing_pairs:
            continue
        existing.append(
            {
                "label": label,
                "value": value,
                "note": str(item.get("note") or "").strip(),
            }
        )
        existing_pairs.add(pair)
        added += 1

    if added > 0:
        detail["ai_timeline_updates_snapshot"] = existing
    return added


def _merge_ai_actions(existing_actions: list[str], new_actions: list[str]) -> list[str]:
    merged: list[str] = []
    for action in [*(existing_actions or []), *(new_actions or [])]:
        clean = str(action or "").strip()
        if clean and clean not in merged:
            merged.append(clean)
    return merged


def _ai_review_status_label(status: str) -> str:
    mapping = {
        "ready": "已生成",
        "failed": "失败",
        "not_generated": "未生成",
        "outdated": "已过期",
    }
    return mapping.get((status or "").strip(), "未生成")


def _normalize_ai_review_state(detail: dict) -> None:
    ai_suggestion = detail.get("ai_review_suggestion")
    if not isinstance(ai_suggestion, dict):
        ai_suggestion = {}
        detail["ai_review_suggestion"] = ai_suggestion
    ai_meta = ai_suggestion.get("meta") if isinstance(ai_suggestion.get("meta"), dict) else {}

    status = str(detail.get("ai_review_status") or "").strip()
    if status not in {"ready", "failed", "not_generated", "outdated"}:
        has_suggestion = any(
            ai_suggestion.get(key)
            for key in [
                "review_summary",
                "evidence_updates",
                "timeline_updates",
                "risk_adjustment",
                "score_adjustments",
                "recommended_action",
            ]
        )
        detail["ai_review_status"] = "ready" if has_suggestion else "not_generated"

    for key in [
        "ai_input_hash",
        "ai_prompt_version",
        "ai_generated_at",
        "ai_generated_by_name",
        "ai_generated_by_email",
        "ai_review_error",
        "ai_source",
        "ai_model",
        "ai_mode",
        "ai_generation_reason",
        "ai_refresh_reason",
    ]:
        detail[key] = str(detail.get(key) or "")
    if not detail["ai_source"]:
        detail["ai_source"] = str(ai_meta.get("source") or "")
    if not detail["ai_model"]:
        detail["ai_model"] = str(ai_meta.get("model") or "")
    if not detail["ai_mode"]:
        detail["ai_mode"] = str(ai_suggestion.get("mode") or "")
    if not detail["ai_prompt_version"]:
        detail["ai_prompt_version"] = str(ai_meta.get("prompt_version") or "")
    detail["ai_generated_latency_ms"] = int(
        detail.get("ai_generated_latency_ms") or ai_meta.get("generated_latency_ms") or 0
    )

    _refresh_ai_review_freshness(detail)


def _stable_ai_payload_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _compute_ai_input_hash(
    *,
    parsed_resume: dict,
    scoring_config: dict,
    score_details: dict,
    risk_result: dict,
    screening_result: dict,
    evidence_snippets: list,
) -> str:
    payload = {
        "parsed_resume": parsed_resume if isinstance(parsed_resume, dict) else {},
        "scoring_config": scoring_config if isinstance(scoring_config, dict) else {},
        "score_details": score_details if isinstance(score_details, dict) else {},
        "risk_result": risk_result if isinstance(risk_result, dict) else {},
        "screening_result": screening_result if isinstance(screening_result, dict) else {},
        "evidence_snippets": evidence_snippets if isinstance(evidence_snippets, list) else [],
    }
    return sha256(_stable_ai_payload_dumps(payload).encode("utf-8")).hexdigest()


def _current_ai_input_hash(detail: dict) -> str:
    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    scoring_config = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    return _compute_ai_input_hash(
        parsed_resume=detail.get("parsed_resume") if isinstance(detail.get("parsed_resume"), dict) else {},
        scoring_config=scoring_config,
        score_details=detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {},
        risk_result=detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {},
        screening_result=detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {},
        evidence_snippets=detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else [],
    )


def _refresh_ai_review_freshness(detail: dict) -> str:
    ai_suggestion = detail.get("ai_review_suggestion")
    if not isinstance(ai_suggestion, dict):
        ai_suggestion = {}
        detail["ai_review_suggestion"] = ai_suggestion

    status = str(detail.get("ai_review_status") or "").strip() or "not_generated"
    current_hash = _current_ai_input_hash(detail)
    stored_hash = str(detail.get("ai_input_hash") or "").strip()
    has_suggestion = bool(ai_suggestion)

    if status == "failed":
        return status
    if not has_suggestion:
        detail["ai_review_status"] = "not_generated"
        return "not_generated"
    if stored_hash and current_hash == stored_hash:
        detail["ai_review_status"] = "ready"
        return "ready"
    if stored_hash and current_hash != stored_hash:
        detail["ai_review_status"] = "outdated"
        return "outdated"

    detail["ai_review_status"] = "ready"
    return "ready"


def _resolve_ai_generation_reason(detail: dict, force_refresh: bool) -> str:
    status = str(detail.get("ai_review_status") or "")
    if status == "failed":
        return "retry_after_failure"
    if status == "outdated":
        return "refresh_after_outdated"
    if force_refresh:
        return "manual_refresh"
    return "first_generate"


def _ai_generation_lock_key(candidate_id: str, batch_id: str, review_id: str) -> str:
    parts = [str(part or "").strip() for part in [candidate_id, batch_id, review_id] if str(part or "").strip()]
    return "::".join(parts) or "workspace_ai_review"


WORKSPACE_LOCK_TTL_MINUTES = 30


def _empty_workspace_lock_state() -> dict[str, str | bool]:
    return {
        "batch_id": "",
        "candidate_id": "",
        "lock_status": "unlocked",
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


def _lock_owner_display(lock_state: dict) -> str:
    return str(lock_state.get("lock_owner_name") or lock_state.get("lock_owner_email") or "未领取")


def _sync_candidate_lock_state(selected_row: dict, detail: dict, lock_state: dict, operator_user_id: str = "") -> dict:
    normalized = _empty_workspace_lock_state()
    if isinstance(lock_state, dict):
        normalized.update(lock_state)

    detail["lock_status"] = str(normalized.get("lock_status") or "unlocked")
    detail["lock_owner_user_id"] = str(normalized.get("lock_owner_user_id") or "")
    detail["lock_owner_name"] = str(normalized.get("lock_owner_name") or "")
    detail["lock_owner_email"] = str(normalized.get("lock_owner_email") or "")
    detail["lock_acquired_at"] = str(normalized.get("lock_acquired_at") or "")
    detail["lock_expires_at"] = str(normalized.get("lock_expires_at") or "")
    detail["lock_last_heartbeat_at"] = str(normalized.get("lock_last_heartbeat_at") or "")
    detail["lock_reason"] = str(normalized.get("lock_reason") or "")
    detail["is_expired"] = bool(normalized.get("is_expired"))
    detail["is_locked_effective"] = bool(normalized.get("is_locked_effective"))

    if bool(normalized.get("is_locked_effective")):
        if operator_user_id and str(normalized.get("lock_owner_user_id") or "") == str(operator_user_id or ""):
            lock_badge = "我处理中"
        else:
            lock_badge = "他人锁定"
    else:
        lock_badge = "未领取"

    selected_row["锁定状态"] = lock_badge
    selected_row["锁定人"] = _lock_owner_display(normalized)
    selected_row["锁过期时间"] = str(normalized.get("lock_expires_at") or "-")
    return normalized


def _refresh_workspace_candidate_lock_state(batch_id: str, candidate_id: str, selected_row: dict, detail: dict) -> dict:
    operator = _current_operator()
    lock_state = get_candidate_lock_state(batch_id, candidate_id) if batch_id and candidate_id else None
    return _sync_candidate_lock_state(
        selected_row,
        detail,
        lock_state if isinstance(lock_state, dict) else _empty_workspace_lock_state(),
        operator.get("user_id", ""),
    )


def _is_candidate_self_locked(lock_state: dict, operator: dict) -> bool:
    if not isinstance(lock_state, dict):
        return False
    return bool(lock_state.get("is_locked_effective")) and str(lock_state.get("lock_owner_user_id") or "") == str(
        operator.get("user_id") or ""
    )


def _can_edit_claimed_candidate(batch_id: str, lock_state: dict, operator: dict) -> tuple[bool, str]:
    if not batch_id:
        return True, ""
    if _is_candidate_self_locked(lock_state, operator):
        return True, ""
    if bool(lock_state.get("is_locked_effective")):
        if bool(operator.get("is_admin")):
            return False, "当前候选人已被其他 HR 锁定。请先使用“管理员强制解锁”。"
        return False, "当前候选人已被其他 HR 锁定，暂不可编辑。"
    return False, "请先领取并开始处理，再执行人工写入或 AI 应用。"


def _workspace_context_cache_key(
    *,
    selected_jd: str,
    batch_id: str,
    pool_label: str,
    quick_filter: str,
    search_kw: str,
    risk_filter: str,
    sort_key: str,
) -> str:
    return "|".join(
        [
            str(selected_jd or ""),
            str(batch_id or ""),
            str(pool_label or ""),
            str(quick_filter or ""),
            str((search_kw or "").strip().lower()),
            str(risk_filter or ""),
            str(sort_key or ""),
        ]
    )


def _sync_workspace_candidate_lock_in_session(candidate_id: str, lock_state: dict | None = None) -> None:
    candidate_key = str(candidate_id or "").strip()
    if not candidate_key:
        return

    rows = st.session_state.get("v2_rows", [])
    details = st.session_state.get("v2_details", {})
    if not isinstance(rows, list) or not isinstance(details, dict):
        return

    row = next((item for item in rows if str(item.get("candidate_id") or "").strip() == candidate_key), None)
    detail = details.get(candidate_key)
    if not isinstance(row, dict) or not isinstance(detail, dict):
        return

    operator = _current_operator()
    normalized_lock_state = lock_state
    if not isinstance(normalized_lock_state, dict):
        active_batch_id = str(st.session_state.get("workspace_preferred_batch_id") or "").strip()
        normalized_lock_state = get_candidate_lock_state(active_batch_id, candidate_key) if active_batch_id else None
    _sync_candidate_lock_state(
        row,
        detail,
        normalized_lock_state if isinstance(normalized_lock_state, dict) else _empty_workspace_lock_state(),
        str(operator.get("user_id") or ""),
    )
    st.session_state.v2_rows = rows
    st.session_state.v2_details = details


def _focus_workspace_candidate(candidate_id: str, row: dict, *, reset_filters: bool = True) -> None:
    candidate_key = str(candidate_id or "").strip()
    if not candidate_key or not isinstance(row, dict):
        return

    target_pool = _current_candidate_pool(row) or str(row.get("候选池") or "").strip() or "待复核候选人"
    st.session_state.workspace_pool_top_radio = target_pool
    st.session_state.workspace_default_entry_pool = target_pool
    if reset_filters:
        st.session_state.workspace_quick_filter = "全部"
        st.session_state.workspace_search = ""
        st.session_state.workspace_risk_filter = "全部"

    selected_jd = str(st.session_state.get("workspace_selected_jd_title") or "").strip()
    batch_id = str(st.session_state.get("workspace_preferred_batch_id") or "").strip()
    quick_filter = str(st.session_state.get("workspace_quick_filter") or "全部").strip() or "全部"
    search_kw = str(st.session_state.get("workspace_search") or "")
    risk_filter = str(st.session_state.get("workspace_risk_filter") or "全部").strip() or "全部"
    sort_key = str(st.session_state.get("workspace_sort_key") or "")
    context_key = _workspace_context_cache_key(
        selected_jd=selected_jd,
        batch_id=batch_id,
        pool_label=target_pool,
        quick_filter=quick_filter,
        search_kw=search_kw,
        risk_filter=risk_filter,
        sort_key=sort_key,
    )
    selected_cache = st.session_state.get("workspace_selected_candidate_by_context", {})
    if not isinstance(selected_cache, dict):
        selected_cache = {}
    selected_cache[context_key] = candidate_key
    st.session_state.workspace_selected_candidate_by_context = selected_cache
    st.session_state.workspace_pool_move_feedback = ""
    st.session_state.workspace_pool_empty_feedback = ""


def _persist_workspace_candidate_state(
    rows: list[dict],
    details: dict[str, dict],
    selected_row: dict,
    detail: dict,
    candidate_id: str,
    batch_id: str,
    review_id: str,
    *,
    operator: dict[str, str] | None = None,
) -> bool:
    actor = operator or _current_operator()
    _sync_detail_evidence_bridge(detail)
    details[candidate_id] = detail
    st.session_state.v2_rows = rows
    st.session_state.v2_details = details

    persisted = False
    if batch_id:
        persisted = persist_candidate_snapshot(
            batch_id=batch_id,
            candidate_id=candidate_id,
            row_payload=selected_row,
            detail_payload=detail,
            operator_user_id=actor["user_id"],
            operator_name=actor["name"],
            operator_email=actor["email"],
            is_admin=bool(actor.get("is_admin")),
            enforce_lock=True,
        )
        if not persisted:
            return False
        refreshed_lock_state = get_candidate_lock_state(batch_id, candidate_id)
        _sync_candidate_lock_state(selected_row, detail, refreshed_lock_state or _empty_workspace_lock_state(), actor["user_id"])

    if review_id:
        upsert_manual_review(
            review_id=review_id,
            reviewed_by_user_id=actor["user_id"],
            reviewed_by_name=actor["name"],
            reviewed_by_email=actor["email"],
            metadata_updates=_build_ai_review_metadata(detail),
        )
    return bool(batch_id or review_id)


def _ensure_ai_application_baseline(detail: dict, selected_row: dict) -> bool:
    if detail.get("ai_baseline_saved"):
        return False

    detail["baseline_score_details"] = deepcopy(
        detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {}
    )
    detail["baseline_score_values"] = deepcopy(
        detail.get("score_values") if isinstance(detail.get("score_values"), dict) else {}
    )
    detail["baseline_risk_result"] = deepcopy(
        detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {}
    )
    detail["baseline_screening_result"] = deepcopy(
        detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {}
    )
    detail["baseline_review_summary"] = str(
        detail.get("review_summary") or selected_row.get("审核摘要") or ""
    )
    detail["baseline_evidence_snippets"] = deepcopy(
        detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else []
    )
    detail["baseline_timeline_updates_snapshot"] = deepcopy(
        detail.get("ai_timeline_updates_snapshot")
        if isinstance(detail.get("ai_timeline_updates_snapshot"), list)
        else []
    )
    detail["baseline_candidate_pool"] = str(selected_row.get("候选池") or "")
    detail["ai_baseline_saved"] = True
    return True


def _update_selected_row_from_detail(detail: dict, selected_row: dict) -> None:
    score_details = detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {}
    for dim in ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度"]:
        dim_detail = score_details.get(dim) if isinstance(score_details.get(dim), dict) else {}
        if "score" in dim_detail:
            selected_row[dim] = dim_detail.get("score")

    risk_result = detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {}
    screening_result = detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {}
    selected_row["风险等级"] = str(risk_result.get("risk_level") or selected_row.get("风险等级") or "unknown")
    selected_row["风险摘要"] = str(risk_result.get("risk_summary") or selected_row.get("风险摘要") or "")
    selected_row["初筛结论"] = str(
        screening_result.get("screening_result") or selected_row.get("初筛结论") or ""
    )
    selected_row["审核摘要"] = str(detail.get("review_summary") or selected_row.get("审核摘要") or "")
    selected_row["候选池"] = str(selected_row.get("候选池") or _candidate_pool_label(selected_row.get("初筛结论", "")))


def _clear_ai_application_state(detail: dict, *, clear_baseline: bool = True) -> None:
    detail["ai_applied"] = False
    detail["ai_applied_actions"] = []
    detail["ai_applied_by_name"] = ""
    detail["ai_applied_by_email"] = ""
    detail["ai_applied_at"] = ""
    detail["ai_reverted"] = False
    detail["ai_reverted_actions"] = []
    detail["ai_reverted_at"] = ""
    detail["ai_reverted_by_name"] = ""
    detail["ai_reverted_by_email"] = ""
    detail["ai_review_summary_snapshot"] = ""
    detail["ai_score_adjustments_snapshot"] = []
    detail["ai_risk_adjustment_snapshot"] = {}
    if clear_baseline:
        for key in [
            "ai_baseline_saved",
            "baseline_score_details",
            "baseline_score_values",
            "baseline_risk_result",
            "baseline_screening_result",
            "baseline_review_summary",
            "baseline_evidence_snippets",
            "baseline_timeline_updates_snapshot",
            "baseline_candidate_pool",
        ]:
            detail.pop(key, None)


def _restore_ai_baseline(
    detail: dict,
    selected_row: dict,
    *,
    restore_evidence: bool,
    restore_timeline: bool,
) -> bool:
    if not detail.get("ai_baseline_saved"):
        return False

    baseline_scores = detail.get("baseline_score_details")
    baseline_score_values = detail.get("baseline_score_values")
    baseline_risk = detail.get("baseline_risk_result")
    baseline_screening = detail.get("baseline_screening_result")

    detail["score_details"] = deepcopy(baseline_scores) if isinstance(baseline_scores, dict) else {}
    detail["score_values"] = (
        deepcopy(baseline_score_values)
        if isinstance(baseline_score_values, dict)
        else to_score_values(detail.get("score_details") or {})
    )
    detail["risk_result"] = deepcopy(baseline_risk) if isinstance(baseline_risk, dict) else {}
    detail["screening_result"] = deepcopy(baseline_screening) if isinstance(baseline_screening, dict) else {}
    detail["review_summary"] = str(detail.get("baseline_review_summary") or "")

    if restore_evidence:
        detail["evidence_snippets"] = deepcopy(
            detail.get("baseline_evidence_snippets")
            if isinstance(detail.get("baseline_evidence_snippets"), list)
            else []
        )
    if restore_timeline:
        detail["ai_timeline_updates_snapshot"] = deepcopy(
            detail.get("baseline_timeline_updates_snapshot")
            if isinstance(detail.get("baseline_timeline_updates_snapshot"), list)
            else []
        )

    selected_row["初筛结论"] = str((detail.get("screening_result") or {}).get("screening_result") or "")
    selected_row["风险等级"] = str((detail.get("risk_result") or {}).get("risk_level") or "unknown")
    selected_row["风险摘要"] = str((detail.get("risk_result") or {}).get("risk_summary") or "")
    selected_row["审核摘要"] = str(detail.get("review_summary") or "")
    selected_row["候选池"] = str(
        detail.get("baseline_candidate_pool") or _candidate_pool_label(selected_row.get("初筛结论", ""))
    )
    _sync_detail_evidence_bridge(detail)
    _update_selected_row_from_detail(detail, selected_row)
    return True


def _build_ai_change_preview(
    detail: dict,
    selected_row: dict,
    ai_cfg: dict,
    ai_suggestion: dict,
) -> dict:
    score_details = detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {}
    score_adjustments = ai_suggestion.get("score_adjustments") if isinstance(ai_suggestion, dict) else []
    if not isinstance(score_adjustments, list):
        score_adjustments = []

    score_rows: list[dict] = []
    for item in score_adjustments:
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dimension") or "").strip()
        current_detail = score_details.get(dim) if isinstance(score_details.get(dim), dict) else {}
        try:
            current_score = int((current_detail or {}).get("score", 0) or 0)
            max_delta = int(item.get("max_delta", 1) or 1)
            delta = int(item.get("suggested_delta", 0) or 0)
        except (TypeError, ValueError):
            continue
        bounded = max(-max_delta, min(max_delta, delta))
        next_score = max(1, min(5, current_score + bounded)) if current_score else "-"
        score_rows.append(
            {
                "dimension": dim,
                "current_score": current_score if current_score else "-",
                "suggested_delta": bounded,
                "next_score": next_score,
                "reason": str(item.get("reason") or ""),
            }
        )

    risk_result = detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {}
    risk_adjustment = ai_suggestion.get("risk_adjustment") if isinstance(ai_suggestion, dict) else {}
    if not isinstance(risk_adjustment, dict):
        risk_adjustment = {}
    current_risk = str(risk_result.get("risk_level") or selected_row.get("风险等级") or "unknown").lower()
    suggested_risk = str(risk_adjustment.get("suggested_risk_level") or current_risk).lower()

    evidence_existing = {
        (str(item.get("source") or ""), str(item.get("text") or ""))
        for item in (detail.get("evidence_snippets") or [])
        if isinstance(item, dict)
    }
    evidence_add_count = sum(
        1
        for item in (ai_suggestion.get("evidence_updates") or [])
        if isinstance(item, dict)
        and str(item.get("text") or "").strip()
        and (str(item.get("source") or ""), str(item.get("text") or "")) not in evidence_existing
    )

    timeline_existing = {
        (str(item.get("label") or ""), str(item.get("value") or ""))
        for item in (detail.get("ai_timeline_updates_snapshot") or [])
        if isinstance(item, dict)
    }
    timeline_add_count = sum(
        1
        for item in (ai_suggestion.get("timeline_updates") or [])
        if isinstance(item, dict)
        and str(item.get("label") or "").strip()
        and str(item.get("value") or "").strip()
        and (str(item.get("label") or ""), str(item.get("value") or "")) not in timeline_existing
    )

    current_decision = str(
        (detail.get("screening_result") or {}).get("screening_result") or selected_row.get("初筛结论") or ""
    )
    allow_direct_change = bool(
        ((ai_cfg.get("score_adjustment_limit") or {}).get("allow_direct_recommendation_change", False))
    )
    estimated_decision = current_decision
    if allow_direct_change:
        preview_detail = deepcopy(detail)
        preview_row = deepcopy(selected_row)
        if suggested_risk:
            _apply_ai_risk_suggestion(preview_detail, preview_row, ai_suggestion)
        if score_rows:
            _apply_ai_score_suggestions(preview_detail, preview_row, ai_suggestion)
        _refresh_candidate_after_ai_application(preview_detail, preview_row, ai_cfg)
        estimated_decision = str(
            (preview_detail.get("screening_result") or {}).get("screening_result") or current_decision
        )

    return {
        "score_rows": score_rows,
        "current_risk": current_risk,
        "suggested_risk": suggested_risk,
        "risk_changed": suggested_risk != current_risk,
        "current_decision": current_decision,
        "estimated_decision": estimated_decision,
        "allow_direct_change": allow_direct_change,
        "evidence_add_count": evidence_add_count,
        "timeline_add_count": timeline_add_count,
    }


def _refresh_candidate_after_ai_application(detail: dict, selected_row: dict, ai_cfg: dict) -> None:
    score_details = detail.get("score_details")
    if not isinstance(score_details, dict):
        detail["score_details"] = {}
    _recalculate_overall_score(detail)
    detail["score_values"] = to_score_values(detail.get("score_details") or {})

    risk_result = detail.get("risk_result")
    if not isinstance(risk_result, dict):
        risk_result = {}
    risk_level = str(risk_result.get("risk_level") or selected_row.get("风险等级") or "unknown").lower()
    risk_result["risk_level"] = risk_level
    risk_summary = str(selected_row.get("风险摘要") or risk_result.get("risk_summary") or "").strip()
    if risk_summary:
        risk_result["risk_summary"] = risk_summary
    detail["risk_result"] = risk_result

    current_screening = detail.get("screening_result")
    if not isinstance(current_screening, dict):
        current_screening = {}

    allow_direct_change = bool(
        ((ai_cfg.get("score_adjustment_limit") or {}).get("allow_direct_recommendation_change", False))
    )
    if allow_direct_change:
        current_screening = build_screening_decision(
            scores_input=detail.get("score_details") or {},
            risk_level=risk_level,
            risks=risk_result.get("risk_points", []),
            scoring_config=((detail.get("parsed_jd") or {}).get("scoring_config") or {}),
        )
        selected_row["初筛结论"] = current_screening.get("screening_result", "")
        selected_row["候选池"] = _candidate_pool_label(selected_row.get("初筛结论", ""))
    else:
        current_screening.setdefault("screening_result", str(selected_row.get("初筛结论") or ""))
        current_screening.setdefault("screening_reasons", current_screening.get("screening_reasons") or [])

    detail["screening_result"] = current_screening

    auto_decision = str((detail.get("screening_result") or {}).get("screening_result") or "")
    if auto_decision:
        selected_row["初筛结论"] = auto_decision
    selected_row["风险等级"] = risk_level
    if risk_summary:
        selected_row["风险摘要"] = risk_summary
    selected_row["审核摘要"] = _review_summary(auto_decision, risk_level, risk_summary)
    detail["review_summary"] = selected_row["审核摘要"]


def _build_ai_review_metadata(detail: dict) -> dict:
    actions = detail.get("ai_applied_actions")
    score_details = detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {}
    score_snapshot = {
        dim_name: (dim_detail.get("score") if isinstance(dim_detail, dict) else dim_detail)
        for dim_name, dim_detail in score_details.items()
    }
    screening_result = detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {}
    risk_result = detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {}
    return {
        "scores": score_snapshot,
        "auto_screening_result": str(screening_result.get("screening_result") or ""),
        "auto_risk_level": str(risk_result.get("risk_level") or "unknown"),
        "screening_reasons": screening_result.get("screening_reasons") if isinstance(screening_result.get("screening_reasons"), list) else [],
        "risk_points": risk_result.get("risk_points") if isinstance(risk_result.get("risk_points"), list) else [],
        "evidence_snippets": detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else [],
        "ai_applied": bool(detail.get("ai_applied")),
        "ai_applied_actions": actions if isinstance(actions, list) else [],
        "ai_applied_by_name": str(detail.get("ai_applied_by_name") or ""),
        "ai_applied_by_email": str(detail.get("ai_applied_by_email") or ""),
        "ai_applied_at": str(detail.get("ai_applied_at") or ""),
        "ai_source": str(detail.get("ai_source") or ""),
        "ai_mode": str(detail.get("ai_mode") or ""),
        "ai_model": str(detail.get("ai_model") or ""),
        "ai_input_hash": str(detail.get("ai_input_hash") or ""),
        "ai_prompt_version": str(detail.get("ai_prompt_version") or ""),
        "ai_generated_latency_ms": int(detail.get("ai_generated_latency_ms") or 0),
        "ai_generation_reason": str(detail.get("ai_generation_reason") or ""),
        "ai_refresh_reason": str(detail.get("ai_refresh_reason") or ""),
        "ai_review_status": str(detail.get("ai_review_status") or ""),
        "ai_generated_at": str(detail.get("ai_generated_at") or ""),
        "ai_generated_by_name": str(detail.get("ai_generated_by_name") or ""),
        "ai_generated_by_email": str(detail.get("ai_generated_by_email") or ""),
        "ai_review_error": str(detail.get("ai_review_error") or ""),
        "ai_review_summary_snapshot": str(detail.get("ai_review_summary_snapshot") or ""),
        "ai_score_adjustments_snapshot": (
            detail.get("ai_score_adjustments_snapshot")
            if isinstance(detail.get("ai_score_adjustments_snapshot"), list)
            else []
        ),
        "ai_risk_adjustment_snapshot": (
            detail.get("ai_risk_adjustment_snapshot")
            if isinstance(detail.get("ai_risk_adjustment_snapshot"), dict)
            else {}
        ),
        "ai_reverted": bool(detail.get("ai_reverted")),
        "ai_reverted_actions": (
            detail.get("ai_reverted_actions")
            if isinstance(detail.get("ai_reverted_actions"), list)
            else []
        ),
        "ai_reverted_at": str(detail.get("ai_reverted_at") or ""),
        "ai_reverted_by_name": str(detail.get("ai_reverted_by_name") or ""),
        "ai_reverted_by_email": str(detail.get("ai_reverted_by_email") or ""),
    }


def _generate_ai_review_for_batch_detail(
    detail: dict,
    *,
    runtime_cfg: dict,
    operator: dict[str, object] | None = None,
) -> tuple[bool, str]:
    payload = detail if isinstance(detail, dict) else {}
    _apply_batch_ai_reviewer_runtime_to_detail(payload, runtime_cfg, jd_title=str((payload.get("parsed_jd") or {}).get("job_title") or ""))
    _normalize_ai_review_state(payload)

    parsed_jd = payload.get("parsed_jd") if isinstance(payload.get("parsed_jd"), dict) else {}
    scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    effective_scoring_cfg = _build_runtime_ai_reviewer_scoring_config(
        scoring_cfg,
        runtime_cfg,
        jd_title=str(parsed_jd.get("job_title") or ""),
    )
    ai_cfg = (
        effective_scoring_cfg.get("ai_reviewer")
        if isinstance(effective_scoring_cfg.get("ai_reviewer"), dict)
        else {}
    )
    ai_mode = str(ai_cfg.get("ai_reviewer_mode") or "suggest_only")
    ai_enabled = bool(ai_cfg.get("enable_ai_reviewer", False)) and ai_mode != "off"
    if not ai_enabled:
        payload["ai_review_suggestion"] = {}
        payload["ai_review_status"] = "not_generated"
        payload["ai_review_error"] = ""
        payload["ai_generation_reason"] = ""
        payload["ai_refresh_reason"] = ""
        return False, ""

    template_name = scoring_cfg.get("role_template") or scoring_cfg.get("profile_name")
    role_profile = get_profile_by_name(template_name) if template_name else detect_role_profile(parsed_jd)
    actor = operator if isinstance(operator, dict) else {}
    actor_name = str(actor.get("name") or "Batch Auto")
    actor_email = str(actor.get("email") or "")
    generation_reason = "batch_auto_generate"
    current_input_hash = _current_ai_input_hash(payload)
    started_at = perf_counter()

    try:
        ai_suggestion = run_ai_reviewer(
            parsed_jd=parsed_jd,
            parsed_resume=payload.get("parsed_resume") if isinstance(payload.get("parsed_resume"), dict) else {},
            role_profile=role_profile,
            scoring_config=effective_scoring_cfg,
            score_details=payload.get("score_details") if isinstance(payload.get("score_details"), dict) else {},
            risk_result=payload.get("risk_result") if isinstance(payload.get("risk_result"), dict) else {},
            screening_result=payload.get("screening_result") if isinstance(payload.get("screening_result"), dict) else {},
            evidence_snippets=payload.get("evidence_snippets") if isinstance(payload.get("evidence_snippets"), list) else [],
        )
        elapsed_ms = max(0, int((perf_counter() - started_at) * 1000))
        ai_suggestion = ai_suggestion if isinstance(ai_suggestion, dict) else {}
        ai_meta = ai_suggestion.get("meta") if isinstance(ai_suggestion.get("meta"), dict) else {}
        payload["ai_review_suggestion"] = ai_suggestion
        payload["ai_generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload["ai_generated_by_name"] = actor_name
        payload["ai_generated_by_email"] = actor_email
        payload["ai_review_error"] = ""
        payload["ai_source"] = str(ai_meta.get("source") or "")
        payload["ai_model"] = str(ai_meta.get("model") or ai_cfg.get("model") or "")
        payload["ai_mode"] = str(ai_suggestion.get("mode") or ai_mode or "")
        payload["ai_input_hash"] = current_input_hash
        payload["ai_prompt_version"] = str(ai_meta.get("prompt_version") or get_ai_reviewer_prompt_version())
        payload["ai_generated_latency_ms"] = int(ai_meta.get("generated_latency_ms") or elapsed_ms)
        payload["ai_generation_reason"] = generation_reason
        payload["ai_refresh_reason"] = ""
        payload["ai_review_status"] = "ready"
        _refresh_ai_review_freshness(payload)
        return True, str(payload.get("ai_source") or "")
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = max(0, int((perf_counter() - started_at) * 1000))
        payload["ai_review_suggestion"] = {}
        payload["ai_review_status"] = "failed"
        payload["ai_review_error"] = str(exc)
        payload["ai_generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload["ai_generated_by_name"] = actor_name
        payload["ai_generated_by_email"] = actor_email
        payload["ai_input_hash"] = current_input_hash
        payload["ai_prompt_version"] = get_ai_reviewer_prompt_version()
        payload["ai_generated_latency_ms"] = elapsed_ms
        payload["ai_generation_reason"] = generation_reason
        payload["ai_refresh_reason"] = ""
        payload["ai_mode"] = ai_mode
        payload["ai_model"] = str(payload.get("ai_model") or ai_cfg.get("model") or "")
        return False, str(exc)


def _generate_ai_review_for_candidate(
    rows: list[dict],
    details: dict[str, dict],
    selected_row: dict,
    detail: dict,
    candidate_id: str,
    batch_id: str,
    review_id: str,
    *,
    force_refresh: bool = False,
) -> tuple[bool, str, str]:
    _normalize_ai_review_state(detail)
    ai_status = _refresh_ai_review_freshness(detail)
    current_input_hash = _current_ai_input_hash(detail)
    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    runtime_cfg = _extract_batch_ai_reviewer_runtime_from_detail(detail)
    effective_scoring_cfg = _build_runtime_ai_reviewer_scoring_config(
        scoring_cfg,
        runtime_cfg,
        jd_title=str(parsed_jd.get("job_title") or ""),
        batch_id=batch_id,
    )
    ai_cfg = (
        effective_scoring_cfg.get("ai_reviewer")
        if isinstance(effective_scoring_cfg.get("ai_reviewer"), dict)
        else {}
    )
    ai_mode = str(ai_cfg.get("ai_reviewer_mode") or "off")
    ai_enabled = bool(ai_cfg.get("enable_ai_reviewer", False)) and ai_mode != "off"
    if not ai_enabled:
        detail["ai_review_status"] = "not_generated"
        detail["ai_review_error"] = ""
        return False, "当前批次未启用 AI reviewer，无法生成 AI 审核建议。", "warning"

    ai_suggestion = detail.get("ai_review_suggestion") if isinstance(detail.get("ai_review_suggestion"), dict) else {}
    if (
        not force_refresh
        and ai_status == "ready"
        and bool(ai_suggestion)
        and current_input_hash
        and current_input_hash == str(detail.get("ai_input_hash") or "").strip()
    ):
        return False, "当前 AI 建议仍然有效，可直接查看或手动刷新。", "info"

    template_name = scoring_cfg.get("role_template") or scoring_cfg.get("profile_name")
    role_profile = get_profile_by_name(template_name) if template_name else detect_role_profile(parsed_jd)
    operator = _current_operator()
    if batch_id:
        can_operate, lock_state = can_user_operate_candidate(
            batch_id=batch_id,
            candidate_id=candidate_id,
            operator_user_id=str(operator["user_id"] or ""),
            is_admin=bool(operator.get("is_admin")),
        )
        if not can_operate:
            _sync_candidate_lock_state(selected_row, detail, lock_state, str(operator["user_id"] or ""))
            return False, "当前候选人已被其他 HR 锁定，暂不可生成或刷新 AI 建议。", "warning"
    generation_reason = _resolve_ai_generation_reason(detail, force_refresh)
    lock_key = _ai_generation_lock_key(candidate_id, batch_id, review_id)
    generation_locks = st.session_state.setdefault("workspace_ai_generation_locks", {})
    if generation_locks.get(lock_key):
        return False, "AI 审核建议正在生成中，请稍候。", "info"

    generation_locks[lock_key] = True
    st.session_state.workspace_ai_generation_locks = generation_locks
    started_at = perf_counter()
    try:
        ai_suggestion = run_ai_reviewer(
            parsed_jd=parsed_jd,
            parsed_resume=detail.get("parsed_resume") if isinstance(detail.get("parsed_resume"), dict) else {},
            role_profile=role_profile,
            scoring_config=effective_scoring_cfg,
            score_details=detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {},
            risk_result=detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {},
            screening_result=detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {},
            evidence_snippets=detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else [],
        )
        elapsed_ms = max(0, int((perf_counter() - started_at) * 1000))
        ai_suggestion = ai_suggestion if isinstance(ai_suggestion, dict) else {}
        ai_meta = ai_suggestion.get("meta") if isinstance(ai_suggestion.get("meta"), dict) else {}
        detail["ai_review_suggestion"] = ai_suggestion
        detail["ai_generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        detail["ai_generated_by_name"] = operator["name"]
        detail["ai_generated_by_email"] = operator["email"]
        detail["ai_review_error"] = ""
        detail["ai_source"] = str(ai_meta.get("source") or "")
        detail["ai_model"] = str(ai_meta.get("model") or ai_cfg.get("model") or "")
        detail["ai_mode"] = str(ai_suggestion.get("mode") or ai_mode or "")
        detail["ai_input_hash"] = current_input_hash
        detail["ai_prompt_version"] = str(ai_meta.get("prompt_version") or get_ai_reviewer_prompt_version())
        detail["ai_generated_latency_ms"] = int(ai_meta.get("generated_latency_ms") or elapsed_ms)
        detail["ai_generation_reason"] = generation_reason
        detail["ai_refresh_reason"] = "" if generation_reason == "first_generate" else generation_reason
        detail["ai_review_status"] = "ready"
        ai_status = _refresh_ai_review_freshness(detail)
        persisted = _persist_workspace_candidate_state(
            rows=rows,
            details=details,
            selected_row=selected_row,
            detail=detail,
            candidate_id=candidate_id,
            batch_id=batch_id,
            review_id=review_id,
            operator=operator,
        )
        if not persisted:
            return False, "AI 审核建议已生成，但当前候选人锁状态已变化，未能落盘。", "warning"
        action_text = "刷新" if force_refresh else "生成"
        source = detail.get("ai_source") or "-"
        model = detail.get("ai_model") or "-"
        latency = int(detail.get("ai_generated_latency_ms") or elapsed_ms)
        if source == "stub":
            return (
                True,
                f"已{action_text} AI 审核建议。当前为 fallback 结果，仅用于辅助参考。来源：{source}，模型：{model}，耗时：{latency} ms。",
                "warning",
            )
        status_note = "最新有效" if ai_status == "ready" else _ai_review_status_label(ai_status)
        return (
            True,
            f"已{action_text} AI 审核建议。来源：{source}，模型：{model}，耗时：{latency} ms，状态：{status_note}。",
            "success",
        )
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = max(0, int((perf_counter() - started_at) * 1000))
        detail["ai_review_status"] = "failed"
        detail["ai_review_error"] = str(exc)
        detail["ai_generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        detail["ai_generated_by_name"] = operator["name"]
        detail["ai_generated_by_email"] = operator["email"]
        detail["ai_input_hash"] = current_input_hash
        detail["ai_prompt_version"] = get_ai_reviewer_prompt_version()
        detail["ai_generated_latency_ms"] = elapsed_ms
        detail["ai_generation_reason"] = generation_reason
        detail["ai_refresh_reason"] = "" if generation_reason == "first_generate" else generation_reason
        detail["ai_mode"] = ai_mode
        detail["ai_model"] = str(detail.get("ai_model") or ai_cfg.get("model") or "")
        persisted = _persist_workspace_candidate_state(
            rows=rows,
            details=details,
            selected_row=selected_row,
            detail=detail,
            candidate_id=candidate_id,
            batch_id=batch_id,
            review_id=review_id,
            operator=operator,
        )
        if not persisted and batch_id:
            return False, f"AI 审核建议生成失败：{exc}", "warning"
        return False, f"AI 审核建议生成失败：{exc}", "warning"
    finally:
        generation_locks = st.session_state.setdefault("workspace_ai_generation_locks", {})
        generation_locks.pop(lock_key, None)
        st.session_state.workspace_ai_generation_locks = generation_locks


def _apply_ai_suggestions_to_candidate(
    rows: list[dict],
    details: dict[str, dict],
    selected_row: dict,
    detail: dict,
    candidate_id: str,
    batch_id: str,
    review_id: str,
    ai_cfg: dict,
    ai_suggestion: dict,
    *,
    apply_evidence: bool = False,
    apply_timeline: bool = False,
    apply_risk: bool = False,
    apply_scores: bool = False,
) -> tuple[bool, str]:
    _ensure_ai_application_baseline(detail, selected_row)
    applied_actions: list[str] = []

    if apply_evidence:
        added = _apply_ai_evidence_suggestions(detail, ai_suggestion.get("evidence_updates") or [])
        if added > 0:
            applied_actions.append("evidence")

    if apply_timeline:
        added = _apply_ai_timeline_updates(detail, ai_suggestion)
        if added > 0:
            applied_actions.append("timeline")

    if apply_risk and _apply_ai_risk_suggestion(detail, selected_row, ai_suggestion):
        applied_actions.append("risk")

    if apply_scores and _apply_ai_score_suggestions(detail, selected_row, ai_suggestion) > 0:
        applied_actions.append("scores")

    if not applied_actions:
        return False, "当前没有新的 AI 建议可应用。"

    _refresh_candidate_after_ai_application(detail, selected_row, ai_cfg)

    operator = _current_operator()
    detail["ai_applied"] = True
    detail["ai_applied_actions"] = _merge_ai_actions(
        detail.get("ai_applied_actions") if isinstance(detail.get("ai_applied_actions"), list) else [],
        applied_actions,
    )
    detail["ai_applied_by_name"] = operator["name"]
    detail["ai_applied_by_email"] = operator["email"]
    detail["ai_applied_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail["ai_source"] = str((ai_suggestion.get("meta") or {}).get("source") or "")
    detail["ai_mode"] = str(ai_suggestion.get("mode") or ai_cfg.get("ai_reviewer_mode") or "")
    detail["ai_model"] = str((ai_suggestion.get("meta") or {}).get("model") or ai_cfg.get("model") or "")
    detail["ai_reverted"] = False
    detail["ai_reverted_actions"] = []
    detail["ai_reverted_at"] = ""
    detail["ai_reverted_by_name"] = ""
    detail["ai_reverted_by_email"] = ""
    detail["ai_review_summary_snapshot"] = str(ai_suggestion.get("review_summary") or "")
    detail["ai_score_adjustments_snapshot"] = (
        ai_suggestion.get("score_adjustments") if isinstance(ai_suggestion.get("score_adjustments"), list) else []
    )
    detail["ai_risk_adjustment_snapshot"] = (
        ai_suggestion.get("risk_adjustment") if isinstance(ai_suggestion.get("risk_adjustment"), dict) else {}
    )
    _refresh_ai_review_freshness(detail)

    persisted = _persist_workspace_candidate_state(
        rows=rows,
        details=details,
        selected_row=selected_row,
        detail=detail,
        candidate_id=candidate_id,
        batch_id=batch_id,
        review_id=review_id,
        operator=operator,
    )
    if not persisted:
        return False, "当前候选人锁状态已变化，未能应用 AI 建议。"

    action_labels_clean = {
        "evidence": "证据建议",
        "timeline": "时间线建议",
        "risk": "风险建议",
        "scores": "改分建议",
    }
    applied_text_clean = "、".join(action_labels_clean.get(action, action) for action in applied_actions)
    persisted_text_clean = "并已写入当前批次留痕" if persisted else "已更新当前页面状态"
    return True, f"已应用 AI 建议：{applied_text_clean}，{persisted_text_clean}。"




def _revert_ai_application_from_baseline(
    rows: list[dict],
    details: dict[str, dict],
    selected_row: dict,
    detail: dict,
    candidate_id: str,
    batch_id: str,
    review_id: str,
    *,
    full_restore: bool,
) -> tuple[bool, str]:
    if not detail.get("ai_baseline_saved"):
        return False, "当前没有可恢复的原始规则 baseline。"

    applied_actions = detail.get("ai_applied_actions") if isinstance(detail.get("ai_applied_actions"), list) else []
    if not applied_actions and not detail.get("ai_applied"):
        return False, "当前没有已应用的 AI 建议可撤回。"

    restored = _restore_ai_baseline(
        detail,
        selected_row,
        restore_evidence=full_restore,
        restore_timeline=full_restore,
    )
    if not restored:
        return False, "恢复 baseline 失败。"

    operator = _current_operator()
    reverted_actions = deepcopy(applied_actions)
    if full_restore:
        _clear_ai_application_state(detail, clear_baseline=True)
    else:
        remaining_actions = [action for action in applied_actions if action not in {"scores", "risk"}]
        detail["ai_applied"] = bool(remaining_actions)
        detail["ai_applied_actions"] = remaining_actions
        if not remaining_actions:
            detail["ai_applied_by_name"] = ""
            detail["ai_applied_by_email"] = ""
            detail["ai_applied_at"] = ""
    detail["ai_reverted"] = True
    detail["ai_reverted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail["ai_reverted_by_name"] = operator["name"]
    detail["ai_reverted_by_email"] = operator["email"]
    detail["ai_reverted_actions"] = reverted_actions
    _refresh_ai_review_freshness(detail)

    persisted = _persist_workspace_candidate_state(
        rows=rows,
        details=details,
        selected_row=selected_row,
        detail=detail,
        candidate_id=candidate_id,
        batch_id=batch_id,
        review_id=review_id,
        operator=operator,
    )
    if not persisted:
        return False, "当前候选人锁状态已变化，未能撤回 AI 建议。"
    reverted_text = "、".join(reverted_actions) or "AI 建议"
    suffix = "并恢复原始规则结果" if full_restore else "并撤回 AI 改分影响"
    persisted_text = "，且已写入当前批次留痕" if persisted else ""
    return True, f"已撤回 {reverted_text}{suffix}{persisted_text}。"


def _clear_ai_application_state_for_candidate(
    rows: list[dict],
    details: dict[str, dict],
    selected_row: dict,
    detail: dict,
    candidate_id: str,
    batch_id: str,
    review_id: str,
) -> tuple[bool, str]:
    has_state = bool(detail.get("ai_applied") or detail.get("ai_baseline_saved") or detail.get("ai_reverted"))
    if not has_state:
        return False, "当前没有可清除的 AI 应用状态。"

    operator = _current_operator()
    _clear_ai_application_state(detail, clear_baseline=True)
    _refresh_ai_review_freshness(detail)
    persisted = _persist_workspace_candidate_state(
        rows=rows,
        details=details,
        selected_row=selected_row,
        detail=detail,
        candidate_id=candidate_id,
        batch_id=batch_id,
        review_id=review_id,
        operator=operator,
    )
    if not persisted:
        return False, "当前候选人锁状态已变化，未能清除 AI 应用状态。"
    persisted_text = "并已写入当前批次留痕" if persisted else "已更新当前页面状态"
    return True, f"已清除 AI 应用状态，{persisted_text}。"


def _workspace_selection_scope() -> str:
    selected_jd = str(st.session_state.get("workspace_selected_jd_title") or "").strip() or "session"
    batch_id = str(st.session_state.get("workspace_preferred_batch_id") or "").strip() or "session"
    return f"{selected_jd}|{batch_id}"


def _workspace_selection_key(selection_scope: str, candidate_id: str) -> str:
    return f"workspace_batch_select::{selection_scope}::{candidate_id}"


def _get_workspace_selected_candidate_ids(rows: list[dict], selection_scope: str) -> list[str]:
    selected_ids: list[str] = []
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        if candidate_id and st.session_state.get(_workspace_selection_key(selection_scope, candidate_id), False):
            selected_ids.append(candidate_id)
    return selected_ids


def _set_workspace_selected_candidate_ids(
    selection_scope: str,
    candidate_ids: list[str],
    *,
    selected: bool,
) -> int:
    target_ids = {str(candidate_id or "").strip() for candidate_id in candidate_ids if str(candidate_id or "").strip()}
    if not target_ids:
        return 0

    changed = 0
    for candidate_id in target_ids:
        key = _workspace_selection_key(selection_scope, candidate_id)
        if bool(st.session_state.get(key, False)) != selected:
            changed += 1
        st.session_state[key] = selected
    return changed


def _workspace_candidate_flags(row: dict, detail: dict | None) -> dict:
    safe_detail = detail if isinstance(detail, dict) else {}
    current_operator = _current_operator()
    if isinstance(detail, dict):
        _normalize_ai_review_state(detail)
        ai_status = _refresh_ai_review_freshness(detail)
    else:
        ai_status = "not_generated"

    extract_info = safe_detail.get("extract_info") if isinstance(safe_detail.get("extract_info"), dict) else {}
    quality = str(extract_info.get("quality") or row.get("提取质量") or "")
    message = str(extract_info.get("message") or row.get("提取说明") or "")
    parse_status = str(row.get("解析状态") or extract_info.get("parse_status") or "")
    if not parse_status and (quality or message):
        parse_status = _resolve_parse_status(quality, message)

    risk_result = safe_detail.get("risk_result") if isinstance(safe_detail.get("risk_result"), dict) else {}
    risk_level = str(row.get("风险等级") or risk_result.get("risk_level") or "unknown").lower()
    manual_decision = str(safe_detail.get("manual_decision") or row.get("人工最终结论") or "").strip()
    manual_priority = str(safe_detail.get("manual_priority") or row.get("处理优先级") or "普通").strip() or "普通"
    ai_generated = ai_status in {"ready", "outdated"}
    ocr_missing = parse_status == "OCR能力缺失" or _is_ocr_missing_message(message)
    ocr_weak = parse_status == "弱质量识别" or ((quality or "").lower() == "weak" and not ocr_missing)
    lock_owner_user_id = str(safe_detail.get("lock_owner_user_id") or "").strip()
    is_locked_effective = bool(safe_detail.get("is_locked_effective"))
    self_locked = is_locked_effective and lock_owner_user_id == str(current_operator.get("user_id") or "")
    locked_by_other = is_locked_effective and not self_locked
    unlocked = not is_locked_effective

    return {
        "manual_processed": bool(manual_decision),
        "manual_priority": manual_priority,
        "ai_status": ai_status,
        "ai_generated": ai_generated,
        "ai_applied": bool(safe_detail.get("ai_applied") or safe_detail.get("ai_applied_actions")),
        "risk_level": risk_level,
        "ocr_weak": ocr_weak,
        "ocr_missing": ocr_missing,
        "parse_status": parse_status,
        "current_pool": _current_candidate_pool(row) or str(row.get("候选池") or ""),
        "self_locked": self_locked,
        "locked_by_other": locked_by_other,
        "unlocked": unlocked,
    }


def _build_workspace_batch_stats(rows: list[dict], details: dict[str, dict]) -> dict[str, int]:
    stats = {
        "total_candidates": len(rows),
        "manual_processed": 0,
        "manual_unprocessed": 0,
        "ai_generated": 0,
        "ai_applied": 0,
        "high_risk": 0,
        "ocr_weak": 0,
        "ocr_missing": 0,
        "pending_review_remaining": 0,
    }

    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        flags = _workspace_candidate_flags(row, details.get(candidate_id))
        if flags["manual_processed"]:
            stats["manual_processed"] += 1
        if flags["ai_generated"]:
            stats["ai_generated"] += 1
        if flags["ai_applied"]:
            stats["ai_applied"] += 1
        if flags["risk_level"] == "high":
            stats["high_risk"] += 1
        if flags["ocr_weak"]:
            stats["ocr_weak"] += 1
        if flags["ocr_missing"]:
            stats["ocr_missing"] += 1
        if flags["current_pool"] == "待复核候选人":
            stats["pending_review_remaining"] += 1

    stats["manual_unprocessed"] = max(0, stats["total_candidates"] - stats["manual_processed"])
    return stats


def _render_workspace_batch_overview(rows: list[dict], details: dict[str, dict]) -> None:
    stats = _build_workspace_batch_stats(rows, details)
    total_candidates = int(stats.get("total_candidates", 0) or 0)

    st.markdown("### 批次运营看板")
    metric_row_1 = st.columns(4)
    metric_row_1[0].metric("总候选人数", total_candidates)
    metric_row_1[1].metric("人工已处理人数", stats.get("manual_processed", 0))
    metric_row_1[2].metric("人工未处理人数", stats.get("manual_unprocessed", 0))
    metric_row_1[3].metric("AI 建议已生成人数", stats.get("ai_generated", 0))

    metric_row_2 = st.columns(4)
    metric_row_2[0].metric("AI 建议已应用人数", stats.get("ai_applied", 0))
    metric_row_2[1].metric("高风险人数", stats.get("high_risk", 0))
    metric_row_2[2].metric("OCR 弱质量人数", stats.get("ocr_weak", 0))
    metric_row_2[3].metric("OCR 能力缺失人数", stats.get("ocr_missing", 0))

    st.caption(
        f"当前批次已人工处理 {stats.get('manual_processed', 0)} / {total_candidates}"
        f" ｜ 待复核剩余 {stats.get('pending_review_remaining', 0)}"
        f" ｜ AI 建议已覆盖 {stats.get('ai_generated', 0)} 人"
    )
    st.progress((stats.get("manual_processed", 0) / total_candidates) if total_candidates else 0.0)


def _workspace_lock_status_label(lock_row: dict) -> str:
    if bool(lock_row.get("is_locked_effective")):
        return "有效锁"
    if bool(lock_row.get("is_expired")):
        return "已过期"
    if bool(lock_row.get("has_lock_metadata")):
        return "失效待清理"
    return "未领取"


def _workspace_lock_owner_option_label(lock_row: dict) -> str:
    return str(lock_row.get("lock_owner_name") or lock_row.get("lock_owner_email") or "").strip()


def _parse_workspace_timestamp(raw_value: str) -> datetime | None:
    raw = str(raw_value or "").strip()
    if not raw:
        return None

    normalized = raw.replace("T", " ")[:19]
    try:
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _format_workspace_lock_age(lock_acquired_at: str) -> str:
    acquired_at = _parse_workspace_timestamp(lock_acquired_at)
    if acquired_at is None:
        return "-"

    delta_seconds = max(0, int((datetime.now() - acquired_at).total_seconds()))
    if delta_seconds < 60:
        return "1 分钟内"

    total_minutes = delta_seconds // 60
    total_hours = total_minutes // 60
    days = total_hours // 24
    hours = total_hours % 24
    minutes = total_minutes % 60

    if days > 0:
        return f"{days} 天 {hours} 小时" if hours > 0 else f"{days} 天"
    if total_hours > 0:
        return f"{total_hours} 小时 {minutes} 分钟" if minutes > 0 else f"{total_hours} 小时"
    return f"{total_minutes} 分钟"


def _workspace_lock_age_minutes(lock_row: dict) -> int | None:
    acquired_at = _parse_workspace_timestamp(str(lock_row.get("lock_acquired_at") or ""))
    if acquired_at is None:
        return None
    return max(0, int((datetime.now() - acquired_at).total_seconds() // 60))


def _workspace_lock_remaining_minutes(lock_row: dict) -> int | None:
    expires_at = _parse_workspace_timestamp(str(lock_row.get("lock_expires_at") or ""))
    if expires_at is None:
        return None
    return int((expires_at - datetime.now()).total_seconds() // 60)


def _workspace_lock_last_heartbeat_minutes(lock_row: dict) -> int | None:
    last_heartbeat_at = _parse_workspace_timestamp(str(lock_row.get("lock_last_heartbeat_at") or ""))
    if last_heartbeat_at is None:
        return None
    return max(0, int((datetime.now() - last_heartbeat_at).total_seconds() // 60))


def _workspace_is_long_held_lock(lock_row: dict) -> bool:
    if not bool(lock_row.get("is_locked_effective")):
        return False
    age_minutes = _workspace_lock_age_minutes(lock_row)
    return age_minutes is not None and age_minutes >= 60


def _workspace_is_soon_expiring_lock(lock_row: dict) -> bool:
    if not bool(lock_row.get("is_locked_effective")):
        return False
    remaining_minutes = _workspace_lock_remaining_minutes(lock_row)
    return remaining_minutes is not None and remaining_minutes <= 5


def _workspace_is_heartbeat_anomaly(lock_row: dict) -> bool:
    if not bool(lock_row.get("is_locked_effective")):
        return False

    last_heartbeat_at = _parse_workspace_timestamp(str(lock_row.get("lock_last_heartbeat_at") or ""))
    if last_heartbeat_at is None:
        return True

    expires_at = _parse_workspace_timestamp(str(lock_row.get("lock_expires_at") or ""))
    if expires_at is not None and last_heartbeat_at > expires_at:
        return True

    heartbeat_minutes = _workspace_lock_last_heartbeat_minutes(lock_row)
    return heartbeat_minutes is not None and heartbeat_minutes > 10


def _workspace_lock_heartbeat_status_label(lock_row: dict) -> str:
    if not bool(lock_row.get("is_locked_effective")):
        return "-"
    return "异常" if _workspace_is_heartbeat_anomaly(lock_row) else "正常"


WORKSPACE_LOCK_HEALTH_ORDER: list[tuple[str, str]] = [
    ("normal", "正常"),
    ("soon_expiring", "即将过期"),
    ("heartbeat_anomaly", "心跳异常"),
    ("expired", "已过期"),
    ("stale", "失效待清理"),
]


def _workspace_lock_health_bucket(lock_row: dict) -> str:
    if bool(lock_row.get("is_expired")):
        return "expired"
    if bool(lock_row.get("has_lock_metadata")) and not bool(lock_row.get("is_locked_effective")) and not bool(
        lock_row.get("is_expired")
    ):
        return "stale"
    if _workspace_is_heartbeat_anomaly(lock_row):
        return "heartbeat_anomaly"
    if _workspace_is_soon_expiring_lock(lock_row):
        return "soon_expiring"
    return "normal"


def _workspace_lock_health_label(lock_row: dict | str) -> str:
    bucket = lock_row if isinstance(lock_row, str) else _workspace_lock_health_bucket(lock_row)
    for key, label in WORKSPACE_LOCK_HEALTH_ORDER:
        if key == bucket:
            return label
    return "正常"


def _workspace_filter_lock_health(lock_rows: list[dict], health_view: str) -> list[dict]:
    if health_view == "仅看正常":
        return [item for item in lock_rows if _workspace_lock_health_bucket(item) == "normal"]
    if health_view == "仅看即将过期":
        return [item for item in lock_rows if _workspace_lock_health_bucket(item) == "soon_expiring"]
    if health_view == "仅看心跳异常":
        return [item for item in lock_rows if _workspace_lock_health_bucket(item) == "heartbeat_anomaly"]
    if health_view == "仅看已过期":
        return [item for item in lock_rows if _workspace_lock_health_bucket(item) == "expired"]
    if health_view == "仅看失效待清理":
        return [item for item in lock_rows if _workspace_lock_health_bucket(item) == "stale"]
    return lock_rows


def _workspace_group_lock_rows_by_health(lock_rows: list[dict]) -> dict[str, list[dict]]:
    groups = {key: [] for key, _ in WORKSPACE_LOCK_HEALTH_ORDER}
    for item in lock_rows:
        groups[_workspace_lock_health_bucket(item)].append(item)
    return groups


def _workspace_lock_event_source(event: dict) -> str:
    extra = event.get("extra_json") if isinstance(event, dict) else {}
    if not isinstance(extra, dict):
        extra = {}
    return str(extra.get("source") or "-").strip() or "-"


def _workspace_lock_event_action_label(event: dict) -> str:
    action_type = str(event.get("action_type") or "").strip()
    source = _workspace_lock_event_source(event)
    if action_type == "candidate_lock_acquired":
        return "领取锁"
    if action_type == "candidate_lock_released":
        return "释放锁"
    if action_type == "candidate_lock_force_released":
        if source == "admin_expired_cleanup":
            return "清理过期/失效锁"
        return "管理员强制解锁"
    return action_type or "未知动作"


def _workspace_lock_event_summary(event: dict, candidate_label: str) -> str:
    operator_label = str(event.get("operator_name") or event.get("operator_email") or "未知操作人").strip() or "未知操作人"
    action_label = _workspace_lock_event_action_label(event)
    candidate_text = candidate_label or "该候选人"
    admin_operator_label = operator_label if operator_label.startswith("管理员") else f"管理员 {operator_label}"
    if action_label == "领取锁":
        return f"{operator_label} 领取了{candidate_text}"
    if action_label == "释放锁":
        return f"{operator_label} 释放了{candidate_text}"
    if action_label == "管理员强制解锁":
        return f"{admin_operator_label} 强制解锁了{candidate_text}"
    if action_label == "清理过期/失效锁":
        return f"{admin_operator_label} 清理了{candidate_text}的过期/失效锁"
    return f"{operator_label} 对{candidate_text}执行了 {action_label}"


def _workspace_filter_lock_view(lock_rows: list[dict], view_mode: str) -> list[dict]:
    if view_mode == "仅看长时间未释放锁":
        return [item for item in lock_rows if _workspace_is_long_held_lock(item)]
    if view_mode == "仅看即将过期锁":
        return [item for item in lock_rows if _workspace_is_soon_expiring_lock(item)]
    if view_mode == "仅看心跳异常":
        return [item for item in lock_rows if _workspace_is_heartbeat_anomaly(item)]
    if view_mode == "仅看管理员需介入":
        return [
            item
            for item in lock_rows
            if bool(item.get("is_expired"))
            or (
                bool(item.get("has_lock_metadata"))
                and not bool(item.get("is_locked_effective"))
                and not bool(item.get("is_expired"))
            )
            or _workspace_is_long_held_lock(item)
        ]
    return lock_rows


def _workspace_lock_sort_key(lock_row: dict, sort_mode: str) -> tuple:
    candidate_label = str(
        lock_row.get("candidate_name") or lock_row.get("source_name") or lock_row.get("candidate_id") or ""
    ).strip().lower()
    age_minutes = _workspace_lock_age_minutes(lock_row)
    expires_at = _parse_workspace_timestamp(str(lock_row.get("lock_expires_at") or ""))
    expires_ts = expires_at.timestamp() if expires_at is not None else None

    if sort_mode == "锁龄（长到短）":
        return (age_minutes is None, -(age_minutes or 0), candidate_label)
    if sort_mode == "锁龄（短到长）":
        return (age_minutes is None, age_minutes if age_minutes is not None else 10**9, candidate_label)
    if sort_mode == "过期时间（近到远）":
        return (expires_ts is None, expires_ts if expires_ts is not None else float("inf"), candidate_label)
    if sort_mode == "过期时间（远到近）":
        return (expires_ts is None, -(expires_ts or 0), candidate_label)
    return (candidate_label, str(lock_row.get("candidate_id") or "").strip().lower())


def _filter_workspace_lock_rows(
    lock_rows: list[dict],
    *,
    status_filter: str,
    owner_filter: str,
) -> list[dict]:
    result: list[dict] = []
    for item in lock_rows:
        status_label = _workspace_lock_status_label(item)
        owner_label = _workspace_lock_owner_option_label(item)
        if status_filter == "仅有效锁" and status_label != "有效锁":
            continue
        if status_filter == "仅已过期" and status_label != "已过期":
            continue
        if status_filter == "仅失效待清理" and status_label != "失效待清理":
            continue
        if owner_filter != "全部" and owner_label != owner_filter:
            continue
        result.append(item)
    return result


def _render_workspace_admin_lock_panel(batch_id: str) -> None:
    operator = _current_operator()
    if not batch_id or not bool(operator.get("is_admin")):
        return

    lock_rows = list_batch_candidate_lock_states(batch_id)
    visible_lock_rows = [item for item in lock_rows if bool(item.get("is_locked_effective")) or bool(item.get("has_lock_metadata"))]
    visible_lock_rows.sort(
        key=lambda item: (
            0
            if bool(item.get("is_locked_effective"))
            else 1
            if bool(item.get("is_expired"))
            else 2,
            str(item.get("lock_expires_at") or ""),
            str(item.get("candidate_name") or item.get("source_name") or item.get("candidate_id") or ""),
        )
    )
    effective_count = sum(1 for item in visible_lock_rows if bool(item.get("is_locked_effective")))
    expired_count = sum(1 for item in visible_lock_rows if bool(item.get("is_expired")))
    stale_count = sum(
        1
        for item in visible_lock_rows
        if bool(item.get("has_lock_metadata")) and not bool(item.get("is_locked_effective")) and not bool(item.get("is_expired"))
    )
    long_held_count = sum(1 for item in visible_lock_rows if _workspace_is_long_held_lock(item))
    heartbeat_anomaly_count = sum(1 for item in visible_lock_rows if _workspace_is_heartbeat_anomaly(item))
    health_groups = _workspace_group_lock_rows_by_health(visible_lock_rows)

    with st.expander("管理员锁列表", expanded=False):
        st.caption(
            f"当前批次有效锁 {effective_count} 个 ｜ 已过期 {expired_count} 个 ｜ 可清理失效锁 {expired_count + stale_count} 个 ｜ 长时间未释放锁 {long_held_count} 个 ｜ 心跳异常 {heartbeat_anomaly_count} 个"
        )
        st.caption("锁健康摘要")
        summary_cols = st.columns(5)
        for idx, (bucket_key, label) in enumerate(WORKSPACE_LOCK_HEALTH_ORDER):
            summary_cols[idx].metric(label, len(health_groups.get(bucket_key, [])))
        action_cols = st.columns(2)
        with action_cols[0]:
            if st.button("刷新当前批次锁状态", key=f"workspace_refresh_lock_panel_{batch_id}", use_container_width=True):
                st.session_state.workspace_action_feedback = "已刷新当前批次锁状态。"
                st.session_state.workspace_action_feedback_kind = "info"
                st.rerun()
        with action_cols[1]:
            if st.button(
                "清理过期 / 失效锁",
                key=f"workspace_cleanup_expired_locks_{batch_id}",
                use_container_width=True,
                disabled=(expired_count + stale_count) <= 0,
            ):
                cleaned_count = cleanup_expired_candidate_locks(
                    batch_id,
                    operator_user_id=str(operator["user_id"] or ""),
                    operator_name=str(operator["name"] or ""),
                    operator_email=str(operator["email"] or ""),
                    is_admin=True,
                )
                st.session_state.workspace_action_feedback = (
                    f"已清理 {cleaned_count} 个过期 / 失效锁。"
                    if cleaned_count > 0
                    else "当前批次没有可清理的过期 / 失效锁。"
                )
                st.session_state.workspace_action_feedback_kind = "success" if cleaned_count > 0 else "info"
                st.rerun()

        row_map = {
            str(row.get("candidate_id") or "").strip(): row
            for row in st.session_state.get("v2_rows", [])
            if isinstance(row, dict) and row.get("candidate_id")
        }

        st.markdown("#### 最近锁变更")
        recent_limit_key = f"workspace_recent_lock_events_limit_{batch_id}"
        recent_view_key = f"workspace_recent_lock_events_view_{batch_id}"
        recent_cols = st.columns(2)
        with recent_cols[0]:
            recent_limit = st.selectbox(
                "显示条数",
                options=[10, 20, 30],
                key=recent_limit_key,
            )
        with recent_cols[1]:
            recent_view = st.selectbox(
                "记录视图",
                options=["全部锁变更", "仅看管理员强制处理"],
                key=recent_view_key,
            )

        recent_events = list_recent_lock_events(
            batch_id,
            limit=int(recent_limit or 30),
            force_only=recent_view == "仅看管理员强制处理",
        )
        if not recent_events:
            st.caption("当前批次暂无最近锁变更记录。")
        else:
            for event in recent_events:
                candidate_id = str(event.get("candidate_id") or "").strip()
                candidate_row = row_map.get(candidate_id)
                candidate_label = (
                    str(candidate_row.get("姓名") or candidate_row.get("候选人") or "").strip()
                    if isinstance(candidate_row, dict)
                    else str(candidate_id or "未命名候选人")
                )
                operator_label = str(event.get("operator_name") or event.get("operator_email") or "-").strip() or "-"
                source_label = _workspace_lock_event_source(event)
                action_label = _workspace_lock_event_action_label(event)
                summary_text = _workspace_lock_event_summary(event, candidate_label)

                st.markdown("<div class='module-box'>", unsafe_allow_html=True)
                event_cols = st.columns([0.22, 0.22, 0.18, 0.18, 0.20])
                with event_cols[0]:
                    st.caption("时间")
                    st.write(str(event.get("created_at") or "-"))
                with event_cols[1]:
                    st.caption("候选人")
                    st.write(candidate_label)
                    st.caption(f"candidate_id：{candidate_id[:12] + '...' if len(candidate_id) > 12 else (candidate_id or '-')}")
                with event_cols[2]:
                    st.caption("动作")
                    st.write(action_label)
                with event_cols[3]:
                    st.caption("操作人")
                    st.write(operator_label)
                with event_cols[4]:
                    st.caption("来源")
                    st.write(source_label)
                st.caption(summary_text)
                if st.button(
                    "跳转到该候选人",
                    key=f"workspace_recent_lock_event_focus_{batch_id}_{event.get('action_id') or candidate_id}",
                    use_container_width=True,
                    disabled=not isinstance(candidate_row, dict),
                ):
                    if not isinstance(candidate_row, dict):
                        st.session_state.workspace_action_feedback = "未在当前批次中找到该候选人，无法跳转。"
                        st.session_state.workspace_action_feedback_kind = "warning"
                    else:
                        _focus_workspace_candidate(candidate_id, candidate_row, reset_filters=True)
                        st.session_state.workspace_action_feedback = f"已跳转到候选人：{candidate_label}"
                        st.session_state.workspace_action_feedback_kind = "success"
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

        if not visible_lock_rows:
            st.caption("当前批次暂无锁记录。")
            return

        status_filter_key = f"workspace_admin_lock_status_filter_{batch_id}"
        owner_filter_key = f"workspace_admin_lock_owner_filter_{batch_id}"
        view_mode_key = f"workspace_admin_lock_view_mode_{batch_id}"
        health_view_key = f"workspace_admin_lock_health_view_{batch_id}"
        sort_mode_key = f"workspace_admin_lock_sort_mode_{batch_id}"
        group_by_health_key = f"workspace_admin_lock_group_by_health_{batch_id}"
        status_options = ["全部", "仅有效锁", "仅已过期", "仅失效待清理"]
        view_options = ["全部锁", "仅看长时间未释放锁", "仅看即将过期锁", "仅看心跳异常", "仅看管理员需介入"]
        health_view_options = ["全部健康状态", "仅看正常", "仅看即将过期", "仅看心跳异常", "仅看已过期", "仅看失效待清理"]
        sort_options = ["锁龄（长到短）", "锁龄（短到长）", "过期时间（近到远）", "过期时间（远到近）", "候选人名称"]
        owner_options = ["全部"]
        seen_owners: set[str] = set()
        for item in visible_lock_rows:
            owner_label = _workspace_lock_owner_option_label(item)
            if owner_label and owner_label not in seen_owners:
                seen_owners.add(owner_label)
                owner_options.append(owner_label)

        if st.session_state.get(status_filter_key) not in status_options:
            st.session_state[status_filter_key] = "全部"
        if st.session_state.get(owner_filter_key) not in owner_options:
            st.session_state[owner_filter_key] = "全部"
        if st.session_state.get(view_mode_key) not in view_options:
            st.session_state[view_mode_key] = "全部锁"
        if st.session_state.get(health_view_key) not in health_view_options:
            st.session_state[health_view_key] = "全部健康状态"
        if st.session_state.get(sort_mode_key) not in sort_options:
            st.session_state[sort_mode_key] = "锁龄（长到短）"

        filter_cols = st.columns(5)
        with filter_cols[0]:
            status_filter = st.selectbox(
                "状态筛选",
                options=status_options,
                key=status_filter_key,
            )
        with filter_cols[1]:
            owner_filter = st.selectbox(
                "锁定人筛选",
                options=owner_options,
                key=owner_filter_key,
            )
        with filter_cols[2]:
            view_mode = st.selectbox(
                "锁视图",
                options=view_options,
                key=view_mode_key,
            )
        with filter_cols[3]:
            health_view = st.selectbox(
                "健康视图",
                options=health_view_options,
                key=health_view_key,
            )
        with filter_cols[4]:
            sort_mode = st.selectbox(
                "排序方式",
                options=sort_options,
                key=sort_mode_key,
            )
        group_by_health = st.checkbox("按健康状态分组显示", key=group_by_health_key)

        filtered_lock_rows = _filter_workspace_lock_rows(
            visible_lock_rows,
            status_filter=status_filter,
            owner_filter=owner_filter,
        )
        filtered_lock_rows = _workspace_filter_lock_view(filtered_lock_rows, view_mode)
        filtered_lock_rows = _workspace_filter_lock_health(filtered_lock_rows, health_view)
        filtered_lock_rows = sorted(filtered_lock_rows, key=lambda item: _workspace_lock_sort_key(item, sort_mode))

        if not filtered_lock_rows:
            st.caption("当前筛选条件下暂无锁记录。")
            return

        grouped_rows: dict[str, list[dict]] = {}
        display_lock_rows = filtered_lock_rows
        if group_by_health:
            grouped_rows = _workspace_group_lock_rows_by_health(filtered_lock_rows)
            display_lock_rows = []
            for bucket_key, _ in WORKSPACE_LOCK_HEALTH_ORDER:
                display_lock_rows.extend(grouped_rows.get(bucket_key, []))
        rendered_health_buckets: set[str] = set()

        for item in display_lock_rows:
            if group_by_health:
                health_bucket = _workspace_lock_health_bucket(item)
                if health_bucket not in rendered_health_buckets:
                    rendered_health_buckets.add(health_bucket)
                    st.markdown(f"**{_workspace_lock_health_label(health_bucket)}（{len(grouped_rows.get(health_bucket, []))}）**")
            candidate_id = str(item.get("candidate_id") or "").strip()
            candidate_row = row_map.get(candidate_id)
            status_label = _workspace_lock_status_label(item)
            candidate_label = str(item.get("candidate_name") or item.get("source_name") or candidate_id or "未命名候选人")
            candidate_id_short = candidate_id[:12] + "..." if len(candidate_id) > 12 else candidate_id
            lock_owner = str(item.get("lock_owner_name") or item.get("lock_owner_email") or "-")
            lock_age = _format_workspace_lock_age(str(item.get("lock_acquired_at") or ""))
            heartbeat_status = _workspace_lock_heartbeat_status_label(item)
            last_heartbeat_at = str(item.get("lock_last_heartbeat_at") or "-")
            lock_reason = str(item.get("lock_reason") or "-")

            st.markdown("<div class='module-box'>", unsafe_allow_html=True)
            info_cols = st.columns([0.34, 0.16, 0.16, 0.17, 0.17])
            with info_cols[0]:
                st.markdown(f"**{candidate_label}**")
                st.caption(f"candidate_id：{candidate_id_short or '-'}")
            with info_cols[1]:
                st.caption("状态")
                st.write(status_label)
            with info_cols[2]:
                st.caption("锁定人")
                st.write(lock_owner)
            with info_cols[3]:
                st.caption("锁到")
                st.write(str(item.get("lock_expires_at") or "-"))
            with info_cols[4]:
                st.caption("锁龄")
                st.write(lock_age)

            st.caption(
                f"完整 candidate_id：{candidate_id or '-'} ｜ 领取时间：{item.get('lock_acquired_at') or '-'} ｜ 锁原因：{lock_reason}"
            )
            st.caption(
                f"最后心跳时间：{last_heartbeat_at} ｜ 心跳状态：{heartbeat_status}"
            )
            action_cols = st.columns([0.52, 0.48])
            with action_cols[0]:
                if st.button(
                    "跳转到该候选人",
                    key=f"workspace_admin_focus_candidate_{batch_id}_{candidate_id}",
                    use_container_width=True,
                    disabled=not isinstance(candidate_row, dict),
                ):
                    if not isinstance(candidate_row, dict):
                        st.session_state.workspace_action_feedback = "未在当前批次中找到该候选人，无法跳转。"
                        st.session_state.workspace_action_feedback_kind = "warning"
                    else:
                        _focus_workspace_candidate(candidate_id, candidate_row, reset_filters=True)
                        st.session_state.workspace_action_feedback = f"已跳转到候选人：{candidate_label}"
                        st.session_state.workspace_action_feedback_kind = "success"
                    st.rerun()
            with action_cols[1]:
                if st.button(
                    "强制解锁",
                    key=f"workspace_admin_force_unlock_{batch_id}_{candidate_id}",
                    use_container_width=True,
                ):
                    ok, message = release_candidate_lock(
                        batch_id,
                        candidate_id,
                        operator_user_id=str(operator["user_id"] or ""),
                        operator_name=str(operator["name"] or ""),
                        operator_email=str(operator["email"] or ""),
                        is_admin=True,
                        force=True,
                    )
                    refreshed_lock_state = get_candidate_lock_state(batch_id, candidate_id) or _empty_workspace_lock_state()
                    _sync_workspace_candidate_lock_in_session(candidate_id, refreshed_lock_state)
                    st.session_state.workspace_action_feedback = (
                        f"已强制解锁：{candidate_label}"
                        if ok and bool(item.get("is_locked_effective"))
                        else message
                    )
                    st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                    st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)


def _apply_workspace_quick_filter(
    rows: list[dict],
    details: dict[str, dict],
    quick_filter: str,
) -> list[dict]:
    selected_filter = str(quick_filter or "全部").strip() or "全部"
    if selected_filter == "全部":
        return rows

    filtered_rows: list[dict] = []
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        flags = _workspace_candidate_flags(row, details.get(candidate_id))

        if selected_filter == "仅看未人工处理" and not flags["manual_processed"]:
            filtered_rows.append(row)
        elif selected_filter == "仅看高优先级" and flags["manual_priority"] == "高":
            filtered_rows.append(row)
        elif selected_filter == "仅看 AI 建议未生成" and flags["ai_status"] == "not_generated":
            filtered_rows.append(row)
        elif selected_filter == "仅看 AI 建议已生成但未应用" and flags["ai_generated"] and not flags["ai_applied"]:
            filtered_rows.append(row)
        elif selected_filter == "仅看 OCR 弱质量 / OCR 能力缺失" and (flags["ocr_weak"] or flags["ocr_missing"]):
            filtered_rows.append(row)
        elif selected_filter == "仅看高风险且待复核" and flags["risk_level"] == "high" and flags["current_pool"] == "待复核候选人":
            filtered_rows.append(row)
        elif selected_filter == "仅看我处理中" and flags["self_locked"]:
            filtered_rows.append(row)
        elif selected_filter == "仅看他人锁定" and flags["locked_by_other"]:
            filtered_rows.append(row)
        elif selected_filter == "仅看未领取" and flags["unlocked"]:
            filtered_rows.append(row)

    return filtered_rows


def _build_workspace_review_metadata(detail: dict, row: dict) -> dict:
    metadata = _build_ai_review_metadata(detail)
    metadata["manual_priority"] = str(detail.get("manual_priority") or row.get("处理优先级") or "普通")
    return metadata


def _apply_batch_manual_decision(
    rows: list[dict],
    details: dict[str, dict],
    candidate_ids: list[str],
    *,
    manual_decision: str,
    batch_id: str,
) -> dict[str, int]:
    operator = _current_operator()
    active_jd_title = str(st.session_state.get("workspace_selected_jd_title") or "").strip()
    review_notes = st.session_state.get("v2_manual_review_notes", {})
    review_status = st.session_state.get("v2_manual_review_status", {})
    row_map = {str(row.get("candidate_id") or "").strip(): row for row in rows if row.get("candidate_id")}
    result = {"updated_count": 0, "skipped_locked_count": 0}

    for candidate_id in candidate_ids:
        row = row_map.get(str(candidate_id or "").strip())
        detail = details.get(str(candidate_id or "").strip())
        if row is None or not isinstance(detail, dict):
            continue

        if batch_id:
            can_operate, lock_state = can_user_operate_candidate(
                batch_id=batch_id,
                candidate_id=candidate_id,
                operator_user_id=str(operator["user_id"] or ""),
                is_admin=bool(operator.get("is_admin")),
            )
            _sync_candidate_lock_state(row, detail, lock_state, str(operator["user_id"] or ""))
            if not can_operate:
                result["skipped_locked_count"] += 1
                continue

        current_decision = str(detail.get("manual_decision") or row.get("人工最终结论") or "").strip()
        if current_decision == manual_decision:
            review_status[candidate_id] = manual_decision
            continue

        note_value = str(review_notes.get(candidate_id) or detail.get("manual_note") or row.get("人工备注") or "")
        current_priority = str(detail.get("manual_priority") or row.get("处理优先级") or "普通")
        store_ok = True
        if batch_id:
            store_ok = upsert_candidate_manual_review(
                batch_id=batch_id,
                candidate_id=candidate_id,
                manual_decision=manual_decision,
                manual_note=note_value,
                manual_priority=current_priority,
                operator_user_id=operator["user_id"],
                operator_name=operator["name"],
                operator_email=operator["email"],
                review_id=str(detail.get("review_id") or ""),
                jd_title=active_jd_title,
                source="batch_action",
                is_admin=bool(operator.get("is_admin")),
                enforce_lock=True,
            )
            if not store_ok:
                refreshed_lock = get_candidate_lock_state(batch_id, candidate_id)
                _sync_candidate_lock_state(row, detail, refreshed_lock or _empty_workspace_lock_state(), str(operator["user_id"] or ""))
                result["skipped_locked_count"] += 1
                continue

        detail["manual_decision"] = manual_decision
        detail["manual_note"] = note_value
        row["人工最终结论"] = manual_decision
        row["人工备注"] = note_value
        review_status[candidate_id] = manual_decision

        review_id = str(detail.get("review_id") or "")
        if review_id:
            upsert_manual_review(
                review_id=review_id,
                manual_decision=manual_decision,
                manual_note=note_value,
                reviewed_by_user_id=operator["user_id"],
                reviewed_by_name=operator["name"],
                reviewed_by_email=operator["email"],
                metadata_updates=_build_workspace_review_metadata(detail, row),
            )
        result["updated_count"] += 1

    st.session_state.v2_manual_review_status = review_status
    st.session_state.v2_rows = rows
    st.session_state.v2_details = details
    return result


def _apply_batch_priority(
    rows: list[dict],
    details: dict[str, dict],
    candidate_ids: list[str],
    *,
    manual_priority: str,
    batch_id: str,
) -> dict[str, int]:
    operator = _current_operator()
    active_jd_title = str(st.session_state.get("workspace_selected_jd_title") or "").strip()
    row_map = {str(row.get("candidate_id") or "").strip(): row for row in rows if row.get("candidate_id")}
    result = {"updated_count": 0, "skipped_locked_count": 0}

    for candidate_id in candidate_ids:
        row = row_map.get(str(candidate_id or "").strip())
        detail = details.get(str(candidate_id or "").strip())
        if row is None or not isinstance(detail, dict):
            continue

        if batch_id:
            can_operate, lock_state = can_user_operate_candidate(
                batch_id=batch_id,
                candidate_id=candidate_id,
                operator_user_id=str(operator["user_id"] or ""),
                is_admin=bool(operator.get("is_admin")),
            )
            _sync_candidate_lock_state(row, detail, lock_state, str(operator["user_id"] or ""))
            if not can_operate:
                result["skipped_locked_count"] += 1
                continue

        current_priority = str(detail.get("manual_priority") or row.get("处理优先级") or "普通").strip() or "普通"
        if current_priority == manual_priority:
            continue

        store_ok = True
        if batch_id:
            store_ok = upsert_candidate_manual_review(
                batch_id=batch_id,
                candidate_id=candidate_id,
                manual_priority=manual_priority,
                operator_user_id=operator["user_id"],
                operator_name=operator["name"],
                operator_email=operator["email"],
                review_id=str(detail.get("review_id") or ""),
                jd_title=active_jd_title,
                source="batch_action",
                is_admin=bool(operator.get("is_admin")),
                enforce_lock=True,
            )
            if not store_ok:
                refreshed_lock = get_candidate_lock_state(batch_id, candidate_id)
                _sync_candidate_lock_state(row, detail, refreshed_lock or _empty_workspace_lock_state(), str(operator["user_id"] or ""))
                result["skipped_locked_count"] += 1
                continue

        detail["manual_priority"] = manual_priority
        row["处理优先级"] = manual_priority

        review_id = str(detail.get("review_id") or "")
        if review_id:
            upsert_manual_review(
                review_id=review_id,
                reviewed_by_user_id=operator["user_id"],
                reviewed_by_name=operator["name"],
                reviewed_by_email=operator["email"],
                metadata_updates=_build_workspace_review_metadata(detail, row),
            )
        result["updated_count"] += 1

    st.session_state.v2_rows = rows
    st.session_state.v2_details = details
    return result


def _batch_generate_ai_reviews(
    rows: list[dict],
    details: dict[str, dict],
    candidate_ids: list[str],
    *,
    batch_id: str,
) -> dict[str, int]:
    row_map = {str(row.get("candidate_id") or "").strip(): row for row in rows if row.get("candidate_id")}
    result = {
        "generated": 0,
        "stub": 0,
        "skipped_ready": 0,
        "skipped_ineligible": 0,
        "skipped_locked": 0,
        "failed": 0,
    }
    operator = _current_operator()

    for candidate_id in candidate_ids:
        row = row_map.get(str(candidate_id or "").strip())
        detail = details.get(str(candidate_id or "").strip())
        if row is None or not isinstance(detail, dict):
            result["skipped_ineligible"] += 1
            continue

        if batch_id:
            can_operate, lock_state = can_user_operate_candidate(
                batch_id=batch_id,
                candidate_id=candidate_id,
                operator_user_id=str(operator["user_id"] or ""),
                is_admin=bool(operator.get("is_admin")),
            )
            _sync_candidate_lock_state(row, detail, lock_state, str(operator["user_id"] or ""))
            if not can_operate:
                result["skipped_locked"] += 1
                continue

        _normalize_ai_review_state(detail)
        ai_status = _refresh_ai_review_freshness(detail)
        if ai_status not in {"not_generated", "outdated"}:
            if ai_status == "ready":
                result["skipped_ready"] += 1
            else:
                result["skipped_ineligible"] += 1
            continue

        ok, _, feedback_kind = _generate_ai_review_for_candidate(
            rows=rows,
            details=details,
            selected_row=row,
            detail=detail,
            candidate_id=candidate_id,
            batch_id=batch_id,
            review_id=str(detail.get("review_id") or ""),
            force_refresh=False,
        )
        if ok:
            result["generated"] += 1
            if str(detail.get("ai_source") or "") == "stub":
                result["stub"] += 1
        elif feedback_kind == "info":
            result["skipped_ready"] += 1
        else:
            result["failed"] += 1

    st.session_state.v2_rows = rows
    st.session_state.v2_details = details
    return result


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
    visible = [item for item in snippets if isinstance(item, dict) and not item.get("hide_in_summary")]
    if not visible:
        visible = snippets

    for item in visible:
        if not isinstance(item, dict):
            item = {"source": "其他", "text": str(item or "")}
        source = item.get("source", "其他")
        text = item.get("text", "")
        tag = str(item.get("tag") or "").strip()
        related_dimensions = item.get("related_dimensions") if isinstance(item.get("related_dimensions"), list) else []
        chips = f"<span class='chip'>{source}</span>"
        if tag:
            chips += f"<span class='chip'>{tag}</span>"
        for dim_name in related_dimensions[:3]:
            chips += f"<span class='chip'>{_dimension_chip_label(str(dim_name))}</span>"
        st.markdown(
            f"<div class='module-box'>{chips}{text}</div>",
            unsafe_allow_html=True,
        )


def _render_analysis_confidence_panel(analysis_payload: dict, extract_info: dict) -> None:
    analysis = analysis_payload if isinstance(analysis_payload, dict) else {}
    quality = str((extract_info or {}).get("quality") or "").lower()
    analysis_mode = str(analysis.get("analysis_mode") or "normal")
    ocr_conf = float(analysis.get("ocr_confidence") or 0.0)
    structure_conf = float(analysis.get("structure_confidence") or 0.0)
    parse_conf = float(analysis.get("parse_confidence") or 0.0)

    cols = st.columns(4)
    cols[0].metric("OCR 置信度", f"{ocr_conf:.2f}")
    cols[1].metric("结构置信度", f"{structure_conf:.2f}")
    cols[2].metric("解析置信度", f"{parse_conf:.2f}")
    cols[3].metric("分析模式", analysis_mode)

    if analysis_mode in {"weak_text", "manual_first"} or quality == "weak":
        st.warning("当前 OCR/解析质量偏弱，建议优先人工复核关键信息。")


def _render_ai_structured_profile(profile: dict) -> None:
    if not isinstance(profile, dict) or not profile:
        st.caption("暂无结构化候选人画像。")
        return

    st.write(f"教育概览：{profile.get('education_summary') or '未提取'}")
    internships = profile.get("internship_summary") or []
    if internships:
        st.write("实习概览：")
        for item in internships[:3]:
            st.markdown(f"- {item}")
    projects = profile.get("project_summary") or []
    if projects:
        st.write("项目概览：")
        for item in projects[:3]:
            st.markdown(f"- {item}")
    st.write(f"技能清单：{profile.get('skill_inventory') or '未提取'}")
    st.write(f"岗位族群猜测：{profile.get('role_family_guess') or 'unknown'}")
    st.write(f"资历猜测：{profile.get('seniority_guess') or 'unknown'}")


def _render_grounding_evidence(analysis_payload: dict) -> None:
    analysis = analysis_payload if isinstance(analysis_payload, dict) else {}
    evidence_for = analysis.get("evidence_for") if isinstance(analysis.get("evidence_for"), list) else []
    evidence_against = analysis.get("evidence_against") if isinstance(analysis.get("evidence_against"), list) else []
    missing_points = analysis.get("missing_info_points") if isinstance(analysis.get("missing_info_points"), list) else []

    st.markdown("**正向证据**")
    if evidence_for:
        _render_evidence_snippets(evidence_for)
    else:
        st.caption("暂无正向证据。")

    st.markdown("**反证 / 质疑点**")
    if evidence_against:
        _render_evidence_snippets(evidence_against)
    else:
        st.caption("暂无反证。")

    st.markdown("**缺失点**")
    if missing_points:
        for item in missing_points[:6]:
            st.markdown(f"- {item}")
    else:
        st.caption("未识别到明显缺失点。")


def _render_ai_adoption_status(detail: dict) -> None:
    ai_status = str(detail.get("ai_review_status") or "not_generated")
    ai_mode = str(detail.get("ai_mode") or "")
    applied = detail.get("ai_applied_actions") if isinstance(detail.get("ai_applied_actions"), list) else []
    applied_label = "已采纳" if applied else "未采纳"
    st.write(f"AI reviewer 状态：{_ai_review_status_label(ai_status)}")
    if ai_mode:
        st.write(f"AI reviewer 模式：{ai_mode}")
    st.write(f"AI 建议采纳状态：{applied_label}")
    if applied:
        st.caption("已采纳项：" + "、".join(str(item) for item in applied))

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
        reviewer = row.get("reviewed_by_name") or row.get("reviewed_by_email") or "未填写"
        st.markdown(
            f"- {row.get('timestamp', '')}｜"
            f"{row.get('resume_name', '未命名候选人')}｜"
            f"自动：{auto_decision}｜人工：{manual_decision}｜操作人：{reviewer}"
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
    st.write(f"操作人：{row.get('reviewed_by_name') or '未填写'}")
    st.write(f"操作人邮箱：{row.get('reviewed_by_email') or '未填写'}")
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
    operator = _current_operator()
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
        "ai_input_hash": str(result.get("ai_input_hash") or ""),
        "ai_prompt_version": str(result.get("ai_prompt_version") or ""),
        "ai_generated_latency_ms": int(result.get("ai_generated_latency_ms") or 0),
        "ai_generation_reason": str(result.get("ai_generation_reason") or ""),
        "ai_refresh_reason": str(result.get("ai_refresh_reason") or ""),
        "ai_generated_at": str(result.get("ai_generated_at") or ""),
        "ai_generated_by_name": str(result.get("ai_generated_by_name") or ""),
        "ai_generated_by_email": str(result.get("ai_generated_by_email") or ""),
        "ai_review_error": str(result.get("ai_review_error") or ""),
    }


def _normalize_jd_title(input_title: str) -> str:
    """优先使用输入标题；未输入时回退到当前已选择 JD 标题。"""
    clean = (input_title or "").strip()
    if clean:
        return clean
    return (st.session_state.get("selected_jd_title") or "").strip()


def _session_user() -> dict[str, object] | None:
    return get_current_user(st.session_state)


def _restore_authenticated_user() -> dict[str, object] | None:
    session_user = _session_user()
    if not session_user:
        return None

    user_id = str(session_user.get("user_id") or "").strip()
    fresh_user = get_user_by_id(user_id)
    if not fresh_user or not bool(fresh_user.get("is_active")):
        logout_user(st.session_state)
        return None

    login_user(st.session_state, fresh_user)
    return get_current_user(st.session_state)


def _current_user_is_admin() -> bool:
    session_user = _session_user()
    return bool(session_user and session_user.get("is_admin"))


def _format_user_datetime(value: str) -> str:
    return str(value or "").strip() or "-"


def _resolve_user_data_dir() -> Path:
    db_path = os.getenv("HIREMATE_DB_PATH", "/app/data/hiremate.db")
    return Path(db_path).resolve().parent


def _user_api_key_store_path() -> Path:
    return _resolve_user_data_dir() / "user_api_keys.json"


def _load_user_api_key_store() -> dict:
    path = _user_api_key_store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_user_api_key_store(payload: dict) -> None:
    path = _user_api_key_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_user_api_key(provider: str) -> str:
    session_user = _session_user() or {}
    user_id = str(session_user.get("user_id") or "").strip()
    if not user_id:
        return ""
    store = _load_user_api_key_store()
    user_bucket = store.get(user_id) if isinstance(store.get(user_id), dict) else {}
    return str(user_bucket.get(str(provider or "openai").lower()) or "")


def _set_user_api_key(provider: str, api_key: str) -> bool:
    session_user = _session_user() or {}
    user_id = str(session_user.get("user_id") or "").strip()
    if not user_id:
        return False
    clean_key = str(api_key or "").strip()
    store = _load_user_api_key_store()
    if not isinstance(store.get(user_id), dict):
        store[user_id] = {}
    bucket = store[user_id]
    if clean_key:
        bucket[str(provider or "openai").lower()] = clean_key
    else:
        bucket.pop(str(provider or "openai").lower(), None)
    _save_user_api_key_store(store)
    return True


def _format_user_option(user: dict) -> str:
    email = str(user.get("email") or "").strip() or "-"
    name = str(user.get("name") or "").strip()
    return f"{email} ({name})" if name else email


def _count_active_admins(users: list[dict]) -> int:
    return sum(1 for user in users if bool(user.get("is_admin")) and bool(user.get("is_active")))


def _is_last_active_admin(users: list[dict], user_id: str) -> bool:
    lookup = str(user_id or "").strip()
    target = next((user for user in users if str(user.get("user_id") or "").strip() == lookup), None)
    if not target:
        return False
    if not bool(target.get("is_admin")) or not bool(target.get("is_active")):
        return False
    return _count_active_admins(users) <= 1


def _render_admin_account_management() -> None:
    if not _current_user_is_admin():
        return

    users = list_users()
    users_by_id = {
        str(user.get("user_id") or "").strip(): user
        for user in users
        if str(user.get("user_id") or "").strip()
    }
    user_ids = list(users_by_id.keys())
    current_user = _session_user() or {}
    current_user_id = str(current_user.get("user_id") or "").strip()

    with st.expander("管理员账号管理", expanded=False):
        st.caption("仅管理员可见。公开注册保持关闭，账号统一由管理员创建、重置密码和调整权限。")
        st.caption("安全提示：不会展示 password_hash，也不会在页面回显明文密码。")

        metric_cols = st.columns(4)
        metric_cols[0].metric("账号总数", len(users))
        metric_cols[1].metric("启用中", sum(1 for user in users if bool(user.get("is_active"))))
        metric_cols[2].metric("管理员", sum(1 for user in users if bool(user.get("is_admin"))))
        metric_cols[3].metric("启用中的管理员", _count_active_admins(users))

        tabs = st.tabs(["账号列表", "新建账号", "账号维护"])

        with tabs[0]:
            if not users:
                st.info("当前还没有可管理的账号。")
            else:
                st.dataframe(
                    [
                        {
                            "email": str(user.get("email") or ""),
                            "name": str(user.get("name") or ""),
                            "is_admin": "是" if bool(user.get("is_admin")) else "否",
                            "is_active": "启用" if bool(user.get("is_active")) else "停用",
                            "created_at": _format_user_datetime(str(user.get("created_at") or "")),
                            "last_login_at": _format_user_datetime(str(user.get("last_login_at") or "")),
                        }
                        for user in users
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

        with tabs[1]:
            with st.form("admin_create_user_form", clear_on_submit=True):
                new_email = st.text_input("邮箱", placeholder="name@example.com")
                new_name = st.text_input("姓名", placeholder="请输入姓名")
                new_password = st.text_input("密码", type="password", placeholder="至少 8 位")
                create_cols = st.columns(2)
                new_is_admin = create_cols[0].checkbox("设为管理员", value=False)
                new_is_active = create_cols[1].checkbox("创建后立即启用", value=True)
                create_submitted = st.form_submit_button("新建账号", type="primary", use_container_width=True)

            if create_submitted:
                try:
                    created_user = create_user_account(
                        email=new_email,
                        name=new_name,
                        password=new_password,
                        is_active=bool(new_is_active),
                        is_admin=bool(new_is_admin),
                    )
                    st.session_state.joblib_flash_success = f"账号已创建：{created_user.get('email') or new_email}"
                    st.rerun()
                except ValueError as err:
                    st.warning(str(err))

        with tabs[2]:
            if not user_ids:
                st.info("当前还没有可维护的账号。")
            else:
                selected_user_id = st.selectbox(
                    "按邮箱选择账号",
                    options=user_ids,
                    format_func=lambda uid: _format_user_option(users_by_id.get(uid, {})),
                    key="admin_account_selected_user",
                )
                target_user = users_by_id.get(selected_user_id, {})
                target_email = str(target_user.get("email") or "")
                target_name = str(target_user.get("name") or "").strip() or "-"
                target_is_active = bool(target_user.get("is_active"))
                target_is_admin = bool(target_user.get("is_admin"))

                st.caption(
                    f"当前账号：{target_email} ｜ 姓名：{target_name} ｜ "
                    f"状态：{'启用' if target_is_active else '停用'} ｜ "
                    f"权限：{'管理员' if target_is_admin else '普通用户'}"
                )
                if _is_last_active_admin(users, selected_user_id):
                    st.info("该账号是当前最后一个启用中的管理员，不能被停用，也不能取消管理员权限。")

                with st.form("admin_reset_password_form", clear_on_submit=True):
                    reset_password_value = st.text_input("新密码", type="password", placeholder="请输入新密码，至少 8 位")
                    reset_submitted = st.form_submit_button("重置密码", use_container_width=True)

                if reset_submitted:
                    try:
                        if reset_user_password(selected_user_id, reset_password_value):
                            st.session_state.joblib_flash_success = f"已重置密码：{target_email}"
                            st.rerun()
                        st.warning("密码重置失败，请确认目标账号是否存在。")
                    except ValueError as err:
                        st.warning(str(err))

                action_cols = st.columns(2)
                with action_cols[0]:
                    active_label = "停用账号" if target_is_active else "启用账号"
                    if st.button(active_label, key="admin_toggle_user_active_btn", use_container_width=True):
                        next_active = not target_is_active
                        if not next_active and _is_last_active_admin(users, selected_user_id):
                            st.warning("不能停用当前最后一个启用中的管理员账号。请先保留或新增其他管理员。")
                        elif set_user_active(selected_user_id, next_active):
                            if not next_active and selected_user_id == current_user_id:
                                st.session_state.auth_flash_message = "当前账号已被停用，请联系其他管理员。"
                            else:
                                st.session_state.joblib_flash_success = f"已{'启用' if next_active else '停用'}账号：{target_email}"
                            st.rerun()
                        else:
                            st.warning("账号状态更新失败，请稍后重试。")

                with action_cols[1]:
                    admin_label = "取消管理员" if target_is_admin else "设为管理员"
                    if st.button(admin_label, key="admin_toggle_user_admin_btn", use_container_width=True):
                        next_is_admin = not target_is_admin
                        if not next_is_admin and _is_last_active_admin(users, selected_user_id):
                            st.warning("不能取消当前最后一个启用中的管理员权限。请先保留或新增其他管理员。")
                        elif set_user_admin(selected_user_id, next_is_admin):
                            st.session_state.joblib_flash_success = (
                                f"已将 {target_email} {'设为管理员' if next_is_admin else '取消管理员权限'}。"
                            )
                            st.rerun()
                        else:
                            st.warning("管理员权限更新失败，请稍后重试。")


def _health_check_result(label: str, status: str, message: str, *, details: dict | None = None) -> dict[str, object]:
    return {
        "label": str(label or "").strip(),
        "status": str(status or "fail").strip().lower() or "fail",
        "message": str(message or "").strip(),
        "details": details if isinstance(details, dict) else {},
    }


def _health_status_label(status: str) -> str:
    mapping = {
        "pass": "通过",
        "warning": "告警",
        "fail": "失败",
    }
    return mapping.get(str(status or "").strip().lower(), "失败")


def _run_rw_probe_for_users_table() -> dict[str, object]:
    probe_user_id = f"health_user_{uuid4().hex}"
    probe_email = f"health_{uuid4().hex[:12]}@local.invalid"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(1) AS total_count FROM users").fetchone()
        total_count = int(row["total_count"] or 0) if row is not None else 0
        conn.execute(
            """
            INSERT INTO users(
                user_id, email, name, password_hash,
                is_active, is_admin, created_at, updated_at, last_login_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                probe_user_id,
                probe_email,
                "Health Check Probe",
                "health_check_probe_hash",
                1,
                0,
                ts,
                ts,
                "",
            ),
        )
        inserted = conn.execute("SELECT email FROM users WHERE user_id = ?", (probe_user_id,)).fetchone()
        if inserted is None:
            raise RuntimeError("users probe row not readable after insert")
        conn.rollback()
        return _health_check_result(
            "users 表可读写",
            "pass",
            f"users 表读写正常，当前共有 {total_count} 个账号；写入探针已回滚。",
            details={"user_count": total_count, "probe_email": probe_email},
        )
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _health_check_result("users 表可读写", "fail", f"users 表读写检查失败：{exc}")
    finally:
        conn.close()


def _run_rw_probe_for_jobs_table() -> dict[str, object]:
    probe_title = f"SMOKE_HEALTH_JOB_{uuid4().hex[:10]}"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(1) AS total_count FROM jobs").fetchone()
        total_count = int(row["total_count"] or 0) if row is not None else 0
        conn.execute(
            """
            INSERT INTO jobs(
                title, jd_text, openings,
                created_by_user_id, created_by_name, created_by_email,
                updated_by_user_id, updated_by_name, updated_by_email,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                probe_title,
                "Health check JD probe",
                1,
                "health_probe",
                "Health Check",
                "health@local.invalid",
                "health_probe",
                "Health Check",
                "health@local.invalid",
                ts,
                ts,
            ),
        )
        inserted = conn.execute("SELECT title FROM jobs WHERE title = ?", (probe_title,)).fetchone()
        if inserted is None:
            raise RuntimeError("jobs probe row not readable after insert")
        conn.rollback()
        return _health_check_result(
            "jobs 表可读写",
            "pass",
            f"jobs 表读写正常，当前共有 {total_count} 个岗位；写入探针已回滚。",
            details={"job_count": total_count, "probe_title": probe_title},
        )
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _health_check_result("jobs 表可读写", "fail", f"jobs 表读写检查失败：{exc}")
    finally:
        conn.close()


def _collect_ai_provider_health_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for provider in get_ai_provider_options():
        api_key_env_name = get_default_ai_api_key_env_name(provider)
        env_detected = bool(os.getenv(api_key_env_name or "", "").strip()) if api_key_env_name else False
        api_base = resolve_ai_api_base(provider, "")
        api_base_required = provider_requires_explicit_api_base(provider)
        live_supported = provider in {"openai", "openai_compatible", "deepseek"}
        if provider == "mock":
            status = "pass"
            note = "本地 mock，可直接用于结构化 fallback。"
        elif live_supported and env_detected and (bool(api_base) or not api_base_required):
            status = "pass"
            note = "真实调用基础配置已具备。"
        elif live_supported:
            status = "warning"
            note = "真实调用配置不完整，运行时可能 fallback 到 stub。"
        else:
            status = "warning"
            note = "当前 provider 仅保留配置入口，未实现真实调用。"

        rows.append(
            {
                "provider": provider,
                "status": _health_status_label(status),
                "live_call": "是" if live_supported else "否",
                "api_key_env_name": api_key_env_name or "-",
                "env_detected": "是" if env_detected else "否",
                "api_base": api_base or "-",
                "api_base_required": "是" if api_base_required else "否",
                "note": note,
            }
        )
    return rows


def _run_system_health_checks() -> dict[str, object]:
    results: list[dict[str, object]] = []

    try:
        with get_connection() as conn:
            row = conn.execute("SELECT 1 AS ok").fetchone()
            backend = str(getattr(conn, "backend", "unknown") or "unknown")
            if row is None or int(row["ok"] or 0) != 1:
                raise RuntimeError("database ping returned unexpected result")
        results.append(_health_check_result("数据库连接", "pass", f"数据库连接正常，backend={backend}。", details={"backend": backend}))
    except Exception as exc:  # noqa: BLE001
        results.append(_health_check_result("数据库连接", "fail", f"数据库连接失败：{exc}"))

    results.append(_run_rw_probe_for_users_table())
    results.append(_run_rw_probe_for_jobs_table())

    try:
        ocr_caps = check_ocr_capabilities()
        image_ok = bool(ocr_caps.get("image_ocr_available"))
        pdf_ok = bool(ocr_caps.get("pdf_ocr_available"))
        status = "pass" if image_ok and pdf_ok else "warning"
        results.append(
            _health_check_result(
                "OCR capability",
                status,
                f"图片 OCR={'可用' if image_ok else '不可用'}，PDF OCR fallback={'可用' if pdf_ok else '不可用'}。",
                details=ocr_caps,
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_health_check_result("OCR capability", "fail", f"OCR 能力检查失败：{exc}"))
        ocr_caps = {}

    ai_rows = _collect_ai_provider_health_rows()
    live_ready = sum(1 for row in ai_rows if row.get("live_call") == "是" and row.get("status") == "通过")
    ai_status = "pass" if live_ready > 0 else "warning"
    results.append(
        _health_check_result(
            "AI provider 配置状态",
            ai_status,
            f"当前共有 {live_ready} 个可真实调用的 provider 配置已就绪；其余 provider 可能走 stub 或仅保留配置入口。",
            details={"providers": ai_rows},
        )
    )

    try:
        jobs = list_jds()
        job_count = len(jobs)
        results.append(
            _health_check_result(
                "当前是否存在岗位",
                "pass" if job_count > 0 else "warning",
                f"当前岗位数量：{job_count}。",
                details={"job_count": job_count, "sample_titles": jobs[:5]},
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_health_check_result("当前是否存在岗位", "fail", f"岗位读取失败：{exc}"))

    try:
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(1) AS total_count FROM candidate_batches").fetchone()
            batch_count = int(row["total_count"] or 0) if row is not None else 0
        batch_titles = list_candidate_jd_titles()
        results.append(
            _health_check_result(
                "当前是否存在批次",
                "pass" if batch_count > 0 else "warning",
                f"当前批次数量：{batch_count}。",
                details={"batch_count": batch_count, "jd_titles": batch_titles[:5]},
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_health_check_result("当前是否存在批次", "fail", f"批次读取失败：{exc}"))

    summary = {
        "pass": sum(1 for item in results if item.get("status") == "pass"),
        "warning": sum(1 for item in results if item.get("status") == "warning"),
        "fail": sum(1 for item in results if item.get("status") == "fail"),
    }
    return {"summary": summary, "results": results, "ocr": ocr_caps, "ai_rows": ai_rows}


def _build_smoke_test_jd_text() -> str:
    return (
        "岗位名称：Smoke Test 产品经理实习生\n"
        "岗位职责：\n"
        "1. 协助整理岗位需求与候选人评估标准。\n"
        "2. 支持基础数据整理、跨团队沟通和文档输出。\n"
        "3. 参与候选人信息校验与工作台流转。\n"
        "任职要求：\n"
        "1. 本科及以上学历。\n"
        "2. 具备基础数据分析、文档整理与沟通能力。\n"
        "3. 熟悉 Python、SQL 或产品文档者优先。\n"
    )


def _build_smoke_candidate_payload(candidate_id: str) -> tuple[dict[str, object], dict[str, object]]:
    score_details = {
        "教育背景匹配度": {"score": 4, "reason": "smoke", "evidence": ["学历信息完整"]},
        "相关经历匹配度": {"score": 4, "reason": "smoke", "evidence": ["有基础项目经历"]},
        "技能匹配度": {"score": 4, "reason": "smoke", "evidence": ["具备基础技能"]},
        "表达完整度": {"score": 4, "reason": "smoke", "evidence": ["简历结构完整"]},
        "综合推荐度": {"score": 4, "reason": "smoke", "evidence": ["用于 smoke 测试"]},
    }
    row = {
        "candidate_id": candidate_id,
        "姓名": "Smoke 候选人",
        "文件名": "smoke_resume.txt",
        "解析状态": "正常识别",
        "初筛结论": "建议人工复核",
        "风险等级": "low",
        "候选池": "待复核候选人",
        "人工最终结论": "",
        "人工备注": "",
        "处理优先级": "中",
        "审核摘要": "Smoke 主流程测试候选人",
    }
    detail = {
        "parsed_jd": {"job_title": "Smoke Test 产品经理实习生", "scoring_config": {}},
        "parsed_resume": {"name": "Smoke 候选人"},
        "score_details": score_details,
        "score_values": {
            "教育背景匹配度": 4,
            "相关经历匹配度": 4,
            "技能匹配度": 4,
            "表达完整度": 4,
            "综合推荐度": 4,
        },
        "risk_result": {"risk_level": "low", "risk_summary": "smoke", "risk_points": []},
        "screening_result": {
            "screening_result": "建议人工复核",
            "screening_reasons": ["Smoke 测试批次"],
            "gating_signals": {},
        },
        "interview_plan": {"interview_questions": [], "focus_points": [], "interview_summary": "smoke"},
        "evidence_snippets": [],
        "ai_review_suggestion": {},
        "ai_review_status": "not_generated",
        "extract_info": {
            "file_name": "smoke_resume.txt",
            "method": "text",
            "quality": "ok",
            "message": "smoke test",
            "parse_status": "正常识别",
            "can_evaluate": True,
            "should_skip": False,
        },
        "manual_priority": "中",
        "review_id": "",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return row, detail


def _run_app_flow_smoke(*, creator: dict[str, object] | None = None, cleanup: bool = True) -> dict[str, object]:
    smoke_job_title = f"SMOKE_FLOW_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
    candidate_id = f"smoke_cand_{uuid4().hex[:8]}"
    batch_id = ""
    steps: list[dict[str, str]] = []
    cleanup_notes: list[str] = []
    operator = creator if isinstance(creator, dict) else {}
    jd_text = _build_smoke_test_jd_text()

    def _step(name: str, status: str, message: str) -> None:
        steps.append({"name": name, "status": status, "message": message})

    try:
        save_jd(
            smoke_job_title,
            jd_text,
            openings=1,
            created_by_user_id=str(operator.get("user_id") or ""),
            created_by_name=str(operator.get("name") or "Smoke Runner"),
            created_by_email=str(operator.get("email") or "smoke@local.invalid"),
            updated_by_user_id=str(operator.get("user_id") or ""),
            updated_by_name=str(operator.get("name") or "Smoke Runner"),
            updated_by_email=str(operator.get("email") or "smoke@local.invalid"),
        )
        if smoke_job_title not in list_jds():
            raise RuntimeError("test JD not visible in jobs list")
        _step("新建测试 JD", "pass", f"已创建测试岗位：{smoke_job_title}")

        row, detail = _build_smoke_candidate_payload(candidate_id)
        batch_id = save_candidate_batch(
            jd_title=smoke_job_title,
            rows=[row],
            details={candidate_id: detail},
            created_by_user_id=str(operator.get("user_id") or ""),
            created_by_name=str(operator.get("name") or "Smoke Runner"),
            created_by_email=str(operator.get("email") or "smoke@local.invalid"),
        )
        if not batch_id:
            raise RuntimeError("save_candidate_batch returned empty batch id")
        batches = list_candidate_batches_by_jd(smoke_job_title)
        if not any(str(item.get("batch_id") or "") == batch_id for item in batches):
            raise RuntimeError("test batch not visible in batch history")
        _step("创建最小批次", "pass", f"已创建测试批次：{batch_id[:12]}…")

        payload = load_candidate_batch(batch_id)
        if not payload:
            raise RuntimeError("workspace payload is empty")
        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        if len(rows) != 1 or candidate_id not in details:
            raise RuntimeError("workspace payload missing expected candidate row/detail")
        if smoke_job_title not in list_candidate_jd_titles():
            raise RuntimeError("workspace JD title index missing test job")
        _step("读取候选人工作台", "pass", "候选人工作台数据读取正常。")
    except Exception as exc:  # noqa: BLE001
        failed_step = next((item["name"] for item in reversed(steps) if item.get("status") == "fail"), "")
        _step(failed_step or "主流程 smoke", "fail", str(exc))
    finally:
        if cleanup:
            if batch_id:
                if delete_candidate_batch(batch_id):
                    cleanup_notes.append("已清理测试批次。")
                else:
                    cleanup_notes.append("测试批次清理失败或已不存在。")
            try:
                delete_jd(smoke_job_title)
                cleanup_notes.append("已清理测试岗位。")
            except Exception:  # noqa: BLE001
                cleanup_notes.append("测试岗位清理失败或已不存在。")

    success = all(step.get("status") == "pass" for step in steps) and bool(steps)
    warning = any("失败" in note for note in cleanup_notes)
    summary = {
        "pass": sum(1 for step in steps if step.get("status") == "pass"),
        "fail": sum(1 for step in steps if step.get("status") == "fail"),
        "warning": 1 if warning else 0,
    }
    return {
        "success": success,
        "steps": steps,
        "summary": summary,
        "cleanup_notes": cleanup_notes,
        "artifacts": {"job_title": smoke_job_title, "batch_id": batch_id},
    }


def _ai_source_label(source: str) -> str:
    mapping = {
        "api": "API",
        "stub": "Stub Fallback",
    }
    return mapping.get(str(source or "").strip().lower(), str(source or "-").strip() or "-")


def _current_ai_environment_rows() -> list[dict[str, object]]:
    base_cfg = _normalize_scoring_config(
        st.session_state.get("joblib_draft_scoring_config")
        or build_default_scoring_config("AI产品经理 / 大模型产品经理")
    )
    feature_cfgs = [
        ("AI 评分细则建议", {**_default_ai_rule_suggester_config(), **(base_cfg.get("ai_rule_suggester") or {})}),
        ("AI reviewer", {**_default_ai_reviewer_config(), **(base_cfg.get("ai_reviewer") or {})}),
    ]
    rows: list[dict[str, object]] = []
    for feature_label, cfg in feature_cfgs:
        runtime_cfg = resolve_ai_runtime_config(cfg)
        env_name = str(runtime_cfg.get("api_key_env_name") or "")
        env_exists = bool(os.getenv(env_name, "").strip()) if env_name else False
        api_key_mode = str(runtime_cfg.get("api_key_mode") or "env_name")
        api_key_config = (
            "当前使用直接输入 key 模式"
            if api_key_mode == "direct_input"
            else f"{env_name or '-'}（{'已检测到' if env_exists else '未检测到'}）"
        )
        rows.append(
            {
                "功能": feature_label,
                "provider": str(runtime_cfg.get("provider") or "-"),
                "model": str(runtime_cfg.get("model") or "-"),
                "api_base": str(runtime_cfg.get("api_base") or "-") or "-",
                "API Key 配置": api_key_config,
            }
        )
    return rows


def _render_environment_health_panel() -> None:
    if not _current_user_is_admin():
        return

    ocr_caps = check_ocr_capabilities()
    dependency_status = ocr_caps.get("dependency_status") if isinstance(ocr_caps.get("dependency_status"), dict) else {}
    runtime_status = ocr_caps.get("runtime_status") if isinstance(ocr_caps.get("runtime_status"), dict) else {}
    latest_ai = get_latest_ai_call_status()
    ai_rows = _current_ai_environment_rows()

    with st.expander("AI + OCR 环境健康面板", expanded=False):
        st.caption("仅管理员可见。用于部署后快速定位 OCR 为什么不工作、AI 为什么没有走真实接口。")

        st.markdown("**OCR 环境**")
        ocr_metric_cols = st.columns(2)
        ocr_metric_cols[0].metric("Image OCR", "可用" if bool(ocr_caps.get("image_ocr_available")) else "不可用")
        ocr_metric_cols[1].metric("PDF OCR fallback", "可用" if bool(ocr_caps.get("pdf_ocr_available")) else "不可用")
        st.dataframe(
            [
                {
                    "组件": "pillow",
                    "状态": "已安装" if bool(dependency_status.get("pillow")) else "缺失",
                    "影响": "图片 OCR 依赖",
                },
                {
                    "组件": "pytesseract",
                    "状态": "已安装" if bool(dependency_status.get("pytesseract")) else "缺失",
                    "影响": "图片 OCR / PDF OCR fallback 调用入口",
                },
                {
                    "组件": "pdf2image",
                    "状态": "已安装" if bool(dependency_status.get("pdf2image")) else "缺失",
                    "影响": "扫描版 PDF 转图片",
                },
                {
                    "组件": "tesseract",
                    "状态": "已检测到" if bool(runtime_status.get("tesseract")) else "未检测到",
                    "影响": "图片 OCR / PDF OCR fallback 运行时",
                },
                {
                    "组件": "poppler",
                    "状态": "已检测到" if bool(runtime_status.get("poppler")) else "未检测到",
                    "影响": "PDF 转图片运行时",
                },
            ],
            use_container_width=True,
            hide_index=True,
        )
        for hint in (ocr_caps.get("hints") if isinstance(ocr_caps.get("hints"), list) else []):
            if "缺少" in str(hint) or "未检测到" in str(hint):
                st.warning(str(hint))
            else:
                st.info(str(hint))

        st.markdown("**AI 环境**")
        st.dataframe(ai_rows, use_container_width=True, hide_index=True)
        if latest_ai:
            api_key_mode = str(latest_ai.get("api_key_mode") or "env_name")
            env_name = str(latest_ai.get("api_key_env_name") or "-")
            env_present = bool(latest_ai.get("api_key_env_detected"))
            key_present = bool(latest_ai.get("api_key_present"))
            source = str(latest_ai.get("source") or "")
            failure_reason = str(latest_ai.get("failure_reason") or "")
            latest_cols = st.columns(6)
            latest_cols[0].metric("最近调用功能", str(latest_ai.get("purpose") or "-"))
            latest_cols[1].metric("最近调用来源", _ai_source_label(source))
            latest_cols[2].metric("provider", str(latest_ai.get("provider") or "-"))
            latest_cols[3].metric("model", str(latest_ai.get("model") or "-"))
            latest_cols[4].metric(
                "API Key",
                "直接输入已填写" if api_key_mode == "direct_input" and key_present else
                "直接输入未填写" if api_key_mode == "direct_input" else
                f"{env_name}（{'已检测到' if env_present else '未检测到'}）",
            )
            latest_cols[5].metric("api_base", str(latest_ai.get("api_base") or "-") or "-")
            st.caption(
                f"对应 env：{env_name} ｜ 最近一次失败原因：{failure_reason or '-'}"
            )
            if source == "stub":
                st.warning(f"最近一次 AI 调用走了 stub fallback。原因：{failure_reason or latest_ai.get('reason') or '-'}")
            elif failure_reason:
                st.warning(f"最近一次 AI 调用失败：{failure_reason}")
            else:
                st.success("最近一次 AI 调用走了真实 API。")
        else:
            st.info("当前还没有 AI 调用记录。可先在 AI 评分细则建议或 AI reviewer 中触发一次调用。")


def _render_system_health_panel() -> None:
    if not _current_user_is_admin():
        return

    with st.expander("系统健康检查 / 主流程 smoke", expanded=False):
        st.caption("仅管理员可见。用于快速检查数据库、OCR、AI 配置状态，以及岗位配置 → 批量初筛 → 候选人工作台的最小主流程。")
        st.caption("命令行 smoke 脚本：`uv run python scripts/smoke_app_flow.py`")

        action_cols = st.columns(2)
        with action_cols[0]:
            if st.button("运行系统健康检查", key="admin_run_system_health_btn", use_container_width=True):
                st.session_state.admin_system_health_result = _run_system_health_checks()
        with action_cols[1]:
            if st.button("运行主流程 smoke", key="admin_run_app_flow_smoke_btn", use_container_width=True):
                st.session_state.admin_app_flow_smoke_result = _run_app_flow_smoke(creator=_current_operator(), cleanup=True)

        health_result = st.session_state.get("admin_system_health_result")
        if isinstance(health_result, dict):
            summary = health_result.get("summary") if isinstance(health_result.get("summary"), dict) else {}
            metric_cols = st.columns(3)
            metric_cols[0].metric("通过", int(summary.get("pass", 0) or 0))
            metric_cols[1].metric("告警", int(summary.get("warning", 0) or 0))
            metric_cols[2].metric("失败", int(summary.get("fail", 0) or 0))

            result_rows = health_result.get("results") if isinstance(health_result.get("results"), list) else []
            st.dataframe(
                [
                    {
                        "检查项": str(item.get("label") or ""),
                        "状态": _health_status_label(str(item.get("status") or "")),
                        "说明": str(item.get("message") or ""),
                    }
                    for item in result_rows
                ],
                use_container_width=True,
                hide_index=True,
            )

            ai_rows = health_result.get("ai_rows") if isinstance(health_result.get("ai_rows"), list) else []
            if ai_rows:
                with st.expander("AI provider 配置详情", expanded=False):
                    st.dataframe(ai_rows, use_container_width=True, hide_index=True)

            ocr_caps = health_result.get("ocr") if isinstance(health_result.get("ocr"), dict) else {}
            if ocr_caps:
                with st.expander("OCR capability 原始信息", expanded=False):
                    st.json(ocr_caps)

        smoke_result = st.session_state.get("admin_app_flow_smoke_result")
        if isinstance(smoke_result, dict):
            if smoke_result.get("success"):
                st.success("主流程 smoke 通过。")
            else:
                st.warning("主流程 smoke 未通过，请查看步骤详情。")

            smoke_summary = smoke_result.get("summary") if isinstance(smoke_result.get("summary"), dict) else {}
            smoke_metrics = st.columns(3)
            smoke_metrics[0].metric("通过步骤", int(smoke_summary.get("pass", 0) or 0))
            smoke_metrics[1].metric("失败步骤", int(smoke_summary.get("fail", 0) or 0))
            smoke_metrics[2].metric("清理告警", int(smoke_summary.get("warning", 0) or 0))

            st.dataframe(
                [
                    {
                        "步骤": str(item.get("name") or ""),
                        "状态": _health_status_label(str(item.get("status") or "")),
                        "说明": str(item.get("message") or ""),
                    }
                    for item in (smoke_result.get("steps") if isinstance(smoke_result.get("steps"), list) else [])
                ],
                use_container_width=True,
                hide_index=True,
            )

            cleanup_notes = smoke_result.get("cleanup_notes") if isinstance(smoke_result.get("cleanup_notes"), list) else []
            if cleanup_notes:
                for note in cleanup_notes:
                    st.caption(note)


def _render_login_page() -> None:
    st.markdown("## 登录 HireMate")
    st.caption("请先登录后再访问岗位配置、批量初筛与候选人工作台。")

    flash_message = str(st.session_state.pop("auth_flash_message", "") or "").strip()
    if flash_message:
        st.info(flash_message)

    total_users = count_users()
    if total_users <= 0:
        st.warning("当前系统尚未初始化管理员账号，请先在服务器或容器内执行管理员初始化命令。")
        st.code(
            'uv run -- python scripts/bootstrap_admin.py --email admin@example.com --name "管理员" --password "StrongPass123!"',
            language="bash",
        )
        st.caption("公开注册默认关闭。请由部署人员完成首次管理员初始化。")
        return

    with st.form("hiremate_login_form", clear_on_submit=False):
        email = st.text_input("登录邮箱", placeholder="name@example.com")
        password = st.text_input("登录密码", type="password", placeholder="请输入密码")
        submitted = st.form_submit_button("登录", type="primary", use_container_width=True)

    if submitted:
        user, error_message = authenticate_user(email, password)
        if user is None:
            st.error(error_message or "登录失败，请稍后重试。")
            return

        mark_login_success(str(user.get("user_id") or ""))
        fresh_user = get_user_by_id(str(user.get("user_id") or "")) or user
        login_user(st.session_state, fresh_user)
        st.session_state.auth_flash_message = f"欢迎回来，{fresh_user.get('name') or fresh_user.get('email')}"
        st.rerun()

    st.caption("公开注册默认关闭。账号请由管理员统一初始化。")


def _render_sidebar_user_panel() -> None:
    current_user = _session_user() or {}
    role_label = "管理员" if bool(current_user.get("is_admin")) else "招聘成员"

    st.sidebar.markdown("### 当前登录用户")
    st.sidebar.write(str(current_user.get("name") or "-"))
    st.sidebar.caption(str(current_user.get("email") or "-"))
    st.sidebar.caption(f"角色：{role_label}")

    if st.sidebar.button("退出登录", key="auth_logout_btn", use_container_width=True):
        logout_user(st.session_state)
        st.session_state.auth_flash_message = "你已退出登录。"
        st.rerun()


def _current_operator() -> dict[str, str | bool]:
    current_user = _session_user() or {}
    return {
        "user_id": str(current_user.get("user_id") or "").strip(),
        "name": str(current_user.get("name") or "").strip(),
        "email": str(current_user.get("email") or "").strip(),
        "is_admin": bool(current_user.get("is_admin")),
    }


def _run_pipeline(jd_text: str, resume_text: str, jd_title: str = "") -> dict:
    normalized_resume_text = normalize_resume_ocr_text(resume_text)
    parsed_jd = parse_jd(jd_text)
    if jd_title:
        parsed_jd["scoring_config"] = load_jd_scoring_config(jd_title)
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

    evidence_snippets = _collect_evidence_snippets(parsed_resume, parsed_jd=parsed_jd)
    analysis_payload = run_analysis_pipeline(
        parsed_resume=parsed_resume,
        parsed_jd=parsed_jd,
        extract_result={},
        normalized_text=normalized_resume_text,
        raw_text=resume_text,
        evidence_snippets=evidence_snippets,
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
        "ai_review_suggestion": {},
        "ai_review_status": "not_generated",
        "ai_input_hash": "",
        "ai_prompt_version": "",
        "ai_generated_latency_ms": 0,
        "ai_generation_reason": "",
        "ai_refresh_reason": "",
        "ai_generated_at": "",
        "ai_generated_by_name": "",
        "ai_generated_by_email": "",
        "ai_review_error": "",
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
    clean_title = (title or "").strip()
    if not clean_title:
        return
    jd_text = load_jd(clean_title) or ""
    st.session_state.v2_selected_jd_prev = clean_title
    st.session_state.v2_jd_text_area = jd_text
    st.session_state.batch_selected_jd_prev = clean_title
    st.session_state.batch_jd_text_area_pending = jd_text
    st.session_state.workspace_selected_jd_title = clean_title


def _apply_pending_batch_jd_text_area() -> None:
    if "batch_jd_text_area_pending" not in st.session_state:
        return
    pending_text = str(st.session_state.pop("batch_jd_text_area_pending") or "")
    st.session_state.batch_jd_text_area = pending_text
    st.session_state.v2_jd_text_area = pending_text


def _sync_batch_screening_jd_context(jd_titles: list[str]) -> str:
    available_titles = [str(title or "").strip() for title in (jd_titles or []) if str(title or "").strip()]
    workspace_jd = str(st.session_state.get("workspace_selected_jd_title") or "").strip()
    current_batch_jd = str(st.session_state.get("batch_selected_jd_prev") or "").strip()

    if workspace_jd and workspace_jd in available_titles and workspace_jd != current_batch_jd:
        _apply_jd_to_workspace(workspace_jd)
        return workspace_jd

    if current_batch_jd and current_batch_jd in available_titles:
        if not workspace_jd:
            st.session_state.workspace_selected_jd_title = current_batch_jd
        if str(st.session_state.get("batch_saved_jd_select") or "").strip() != current_batch_jd:
            st.session_state.batch_saved_jd_select = current_batch_jd
        return current_batch_jd

    if available_titles:
        fallback_title = workspace_jd if workspace_jd in available_titles else available_titles[0]
        _apply_jd_to_workspace(fallback_title)
        return fallback_title

    st.session_state.batch_selected_jd_prev = ""
    st.session_state.batch_saved_jd_select = ""
    st.session_state.batch_jd_text_area_pending = ""
    if not workspace_jd:
        st.session_state.workspace_selected_jd_title = ""
    return ""


def _apply_batch_to_workspace(jd_title: str, batch_id: str, preferred_pool: str | None = None) -> None:
    """将岗位和批次设为候选人工作台默认上下文。"""
    clean_title = (jd_title or "").strip()
    clean_batch_id = (batch_id or "").strip()
    if clean_title:
        _apply_jd_to_workspace(clean_title)
        st.session_state.workspace_selected_jd_title = clean_title
    st.session_state.workspace_preferred_batch_id = clean_batch_id
    st.session_state.v2_current_batch_id = clean_batch_id
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
    st.session_state.pending_navigation_page_nav = target
    if "active_page_nav" not in st.session_state:
        st.session_state.active_page_nav = target


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
    _apply_scoring_widget_state(selected_job, st.session_state.joblib_draft_scoring_config)
    st.session_state.joblib_selected_title_prev = job


def _jd_file_signature(file_obj) -> str:
    if file_obj is None:
        return ""
    file_name = str(getattr(file_obj, "name", "") or "")
    file_size = getattr(file_obj, "size", None)
    if file_size is None:
        try:
            file_size = len(file_obj.getvalue())
        except Exception:  # noqa: BLE001
            file_size = 0
    return f"{file_name}:{file_size}"


def _suggest_jd_title_from_file(file_name: str) -> str:
    stem = os.path.splitext(os.path.basename(file_name or ""))[0].strip()
    return stem


def _build_jd_upload_meta(file_obj, result: dict, *, error: str = "") -> dict:
    return {
        "file_name": str(getattr(file_obj, "name", "") or ""),
        "method": str(result.get("method") or "") if result else "",
        "quality": str(result.get("quality") or "") if result else "weak",
        "message": error or str(result.get("message") or "") if result else error,
        "success": not bool(error),
    }


def _render_jd_upload_feedback(meta: dict | None, *, context_label: str) -> None:
    if not meta:
        return
    file_name = str(meta.get("file_name") or "JD 文件")
    message = str(meta.get("message") or "")
    quality = str(meta.get("quality") or "weak")
    method = str(meta.get("method") or "text")

    if meta.get("success"):
        st.success(f"{context_label}已导入：{file_name}。文本已填充到当前 JD 草稿，可继续手工修改后再保存。")
        st.caption(f"提取方式：{_extract_method_label(method)} ｜ 提取质量：{_extract_quality_label(quality)}")
        if message:
            st.caption(f"提取说明：{message}")
        if quality.lower() == "weak":
            st.warning("当前 JD 提取质量较弱，建议在保存前人工校对文本。")
    else:
        st.warning(f"{context_label}导入失败：{message or '未能读取 JD 文件。'}")


def _handle_new_jd_upload(file_obj) -> None:
    if file_obj is None:
        return
    signature = _jd_file_signature(file_obj)
    if signature and signature == st.session_state.get("joblib_new_jd_upload_signature", ""):
        return

    try:
        result = load_jd_file(file_obj)
        st.session_state.joblib_new_text = str(result.get("text") or "")
        if not str(st.session_state.get("joblib_new_title") or "").strip():
            suggested_title = _suggest_jd_title_from_file(str(getattr(file_obj, "name", "") or ""))
            if suggested_title:
                st.session_state.joblib_new_title = suggested_title
        st.session_state.joblib_new_jd_upload_meta = _build_jd_upload_meta(file_obj, result)
    except Exception as err:  # noqa: BLE001
        st.session_state.joblib_new_jd_upload_meta = _build_jd_upload_meta(file_obj, {}, error=str(err))
    st.session_state.joblib_new_jd_upload_signature = signature


def _handle_edit_jd_upload(selected_job: str, file_obj) -> None:
    if not selected_job or file_obj is None:
        return
    signature = _jd_file_signature(file_obj)
    sig_map = st.session_state.setdefault("joblib_edit_jd_upload_signature_by_job", {})
    if signature and signature == sig_map.get(selected_job, ""):
        return

    text_key = f"joblib_edit_text_input_{selected_job}"
    try:
        result = load_jd_file(file_obj)
        extracted_text = str(result.get("text") or "")
        st.session_state.joblib_draft_text = extracted_text
        st.session_state[text_key] = extracted_text
        meta_map = st.session_state.setdefault("joblib_edit_jd_upload_meta_by_job", {})
        meta_map[selected_job] = _build_jd_upload_meta(file_obj, result)
        st.session_state.joblib_edit_jd_upload_meta_by_job = meta_map
    except Exception as err:  # noqa: BLE001
        meta_map = st.session_state.setdefault("joblib_edit_jd_upload_meta_by_job", {})
        meta_map[selected_job] = _build_jd_upload_meta(file_obj, {}, error=str(err))
        st.session_state.joblib_edit_jd_upload_meta_by_job = meta_map
    sig_map[selected_job] = signature
    st.session_state.joblib_edit_jd_upload_signature_by_job = sig_map


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
        "ai_reviewer_mode": "suggest_only",
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


def _default_batch_ai_reviewer_runtime_config() -> dict:
    return {
        "enable_ai_reviewer": False,
        "ai_reviewer_mode": "suggest_only",
        "provider": "openai",
        "model": get_default_ai_model("openai"),
        "api_base": get_default_ai_api_base("openai"),
        "api_key_env_name": get_default_ai_api_key_env_name("openai"),
        "auto_generate_for_new_batch": False,
    }


def _looks_like_api_key_input(value: str) -> bool:
    clean = str(value or "").strip().lower()
    if not clean:
        return False
    if clean.startswith(("sk-", "sk_proj_", "sk-proj-", "dsk_", "dsk-", "bearer ")):
        return True
    return len(clean) >= 24 and "-" in clean and "_" not in clean[:8]


def _default_ai_key_mode_for_ui(provider: str, current_cfg: dict | None) -> str:
    return "direct_input"


def _sanitize_ai_runtime_cfg_for_storage(ai_cfg: dict | None) -> dict:
    payload = dict(ai_cfg or {})
    payload.pop("api_key_value", None)
    return payload


def _remember_batch_ai_direct_key(batch_id: str, runtime_cfg: dict | None) -> None:
    clean_batch_id = str(batch_id or "").strip()
    cfg = dict(runtime_cfg or {})
    direct_key = str(cfg.get("api_key_value") or "").strip()
    mode = str(cfg.get("api_key_mode") or "").strip().lower()
    secret_map = st.session_state.setdefault("batch_ai_reviewer_direct_key_by_batch_id", {})
    if not clean_batch_id:
        return
    if mode == "direct_input" and direct_key:
        secret_map[clean_batch_id] = direct_key
    elif clean_batch_id in secret_map:
        secret_map.pop(clean_batch_id, None)
    st.session_state.batch_ai_reviewer_direct_key_by_batch_id = secret_map


def _resolve_batch_ai_direct_key(batch_id: str) -> str:
    clean_batch_id = str(batch_id or "").strip()
    secret_map = st.session_state.get("batch_ai_reviewer_direct_key_by_batch_id", {})
    if not isinstance(secret_map, dict) or not clean_batch_id:
        return _get_user_api_key("openai")
    direct_key = str(secret_map.get(clean_batch_id) or "").strip()
    if direct_key:
        return direct_key
    runtime_provider = str(st.session_state.get("batch_ai_reviewer_provider") or "openai").strip().lower()
    return _get_user_api_key(runtime_provider)


def _jd_ai_reviewer_defaults_for_title(jd_title: str = "") -> dict:
    clean_title = str(jd_title or "").strip()
    if clean_title:
        scoring_cfg = _normalize_scoring_config(load_jd_scoring_config(clean_title))
        reviewer_cfg = scoring_cfg.get("ai_reviewer") if isinstance(scoring_cfg.get("ai_reviewer"), dict) else {}
    else:
        reviewer_cfg = {}

    defaults = {
        **_default_ai_reviewer_config(),
        **(reviewer_cfg or {}),
        "capabilities": {
            **_default_ai_reviewer_config().get("capabilities", {}),
            **((reviewer_cfg or {}).get("capabilities") or {}),
        },
        "score_adjustment_limit": {
            **_default_ai_reviewer_config().get("score_adjustment_limit", {}),
            **((reviewer_cfg or {}).get("score_adjustment_limit") or {}),
        },
    }
    provider = str(defaults.get("provider") or "openai").strip().lower() or "openai"
    return {
        **defaults,
        "enable_ai_reviewer": bool(defaults.get("enable_ai_reviewer", False)),
        "ai_reviewer_mode": "suggest_only",
        "provider": provider,
        "model": str(defaults.get("model") or get_default_ai_model(provider)).strip() or get_default_ai_model(provider),
        "api_base": str(defaults.get("api_base") or get_default_ai_api_base(provider)).strip(),
        "api_key_mode": str(defaults.get("api_key_mode") or "env_name").strip().lower() or "env_name",
        "api_key_env_name": str(
            defaults.get("api_key_env_name") or get_default_ai_api_key_env_name(provider)
        ).strip()
        or get_default_ai_api_key_env_name(provider),
        "auto_generate_for_new_batch": bool(defaults.get("auto_generate_for_new_batch", False)),
    }


def _normalize_batch_ai_reviewer_runtime_config(runtime_cfg: dict | None, *, jd_title: str = "") -> dict:
    defaults = _default_batch_ai_reviewer_runtime_config()
    jd_defaults = _jd_ai_reviewer_defaults_for_title(jd_title)
    incoming = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    provider = (
        str(incoming.get("provider") or jd_defaults.get("provider") or defaults.get("provider") or "openai")
        .strip()
        .lower()
        or "openai"
    )
    return {
        "enable_ai_reviewer": bool(
            incoming.get("enable_ai_reviewer", jd_defaults.get("enable_ai_reviewer", defaults["enable_ai_reviewer"]))
        ),
        "ai_reviewer_mode": "suggest_only",
        "provider": provider,
        "model": str(incoming.get("model") or jd_defaults.get("model") or get_default_ai_model(provider)).strip()
        or get_default_ai_model(provider),
        "api_base": str(incoming.get("api_base") or jd_defaults.get("api_base") or get_default_ai_api_base(provider)).strip(),
        "api_key_mode": str(incoming.get("api_key_mode") or jd_defaults.get("api_key_mode") or "env_name").strip().lower()
        or "env_name",
        "api_key_env_name": str(
            incoming.get("api_key_env_name")
            or jd_defaults.get("api_key_env_name")
            or get_default_ai_api_key_env_name(provider)
        ).strip()
        or get_default_ai_api_key_env_name(provider),
        "api_key_value": str(incoming.get("api_key_value") or ""),
        "auto_generate_for_new_batch": bool(
            incoming.get(
                "auto_generate_for_new_batch",
                jd_defaults.get("auto_generate_for_new_batch", defaults.get("auto_generate_for_new_batch", False)),
            )
        ),
    }


def _extract_batch_ai_reviewer_runtime_from_detail(detail: dict | None) -> dict:
    payload = detail if isinstance(detail, dict) else {}
    direct = payload.get("batch_ai_reviewer_runtime")
    if isinstance(direct, dict):
        return direct
    metadata = payload.get("batch_metadata")
    if isinstance(metadata, dict):
        nested = metadata.get("ai_reviewer_runtime")
        if isinstance(nested, dict):
            return nested
    return {}


def _apply_batch_ai_reviewer_runtime_to_detail(detail: dict, runtime_cfg: dict | None, *, jd_title: str = "") -> dict:
    payload = detail if isinstance(detail, dict) else {}
    runtime = _normalize_batch_ai_reviewer_runtime_config(runtime_cfg, jd_title=jd_title)
    safe_runtime = _sanitize_ai_runtime_cfg_for_storage(runtime)
    parsed_jd = payload.get("parsed_jd") if isinstance(payload.get("parsed_jd"), dict) else {}
    base_scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    effective_scoring_cfg = _normalize_scoring_config(base_scoring_cfg)
    reviewer_defaults = (
        effective_scoring_cfg.get("ai_reviewer") if isinstance(effective_scoring_cfg.get("ai_reviewer"), dict) else {}
    )
    effective_scoring_cfg["ai_reviewer"] = {
        **_default_ai_reviewer_config(),
        **reviewer_defaults,
        "enable_ai_reviewer": bool(runtime.get("enable_ai_reviewer", False)),
        "ai_reviewer_mode": "suggest_only",
        "provider": str(safe_runtime.get("provider") or reviewer_defaults.get("provider") or "openai"),
        "model": str(safe_runtime.get("model") or reviewer_defaults.get("model") or ""),
        "api_base": str(safe_runtime.get("api_base") or reviewer_defaults.get("api_base") or ""),
        "api_key_mode": str(safe_runtime.get("api_key_mode") or reviewer_defaults.get("api_key_mode") or "env_name"),
        "api_key_env_name": str(
            safe_runtime.get("api_key_env_name") or reviewer_defaults.get("api_key_env_name") or ""
        ),
        "capabilities": {
            **_default_ai_reviewer_config().get("capabilities", {}),
            **(reviewer_defaults.get("capabilities") or {}),
        },
        "score_adjustment_limit": {
            **_default_ai_reviewer_config().get("score_adjustment_limit", {}),
            **(reviewer_defaults.get("score_adjustment_limit") or {}),
        },
    }
    parsed_jd["scoring_config"] = effective_scoring_cfg
    payload["parsed_jd"] = parsed_jd
    metadata = payload.get("batch_metadata") if isinstance(payload.get("batch_metadata"), dict) else {}
    metadata["ai_reviewer_runtime"] = dict(safe_runtime)
    payload["batch_metadata"] = metadata
    payload["batch_ai_reviewer_runtime"] = dict(safe_runtime)
    _normalize_ai_review_state(payload)
    return runtime


def _build_runtime_ai_reviewer_scoring_config(
    scoring_cfg: dict | None,
    runtime_cfg: dict | None,
    *,
    jd_title: str = "",
    batch_id: str = "",
) -> dict:
    effective_scoring_cfg = deepcopy(_normalize_scoring_config(scoring_cfg or {}))
    reviewer_defaults = (
        effective_scoring_cfg.get("ai_reviewer") if isinstance(effective_scoring_cfg.get("ai_reviewer"), dict) else {}
    )
    runtime = _normalize_batch_ai_reviewer_runtime_config(runtime_cfg, jd_title=jd_title)
    direct_key = str(runtime.get("api_key_value") or "").strip()
    if not direct_key and str(runtime.get("api_key_mode") or "").strip().lower() == "direct_input":
        direct_key = _resolve_batch_ai_direct_key(batch_id)

    effective_ai_cfg = {
        **_default_ai_reviewer_config(),
        **(reviewer_defaults or {}),
        "enable_ai_reviewer": bool(runtime.get("enable_ai_reviewer", False)),
        "ai_reviewer_mode": "suggest_only",
        "provider": str(runtime.get("provider") or reviewer_defaults.get("provider") or "openai"),
        "model": str(runtime.get("model") or reviewer_defaults.get("model") or ""),
        "api_base": str(runtime.get("api_base") or reviewer_defaults.get("api_base") or ""),
        "api_key_mode": str(runtime.get("api_key_mode") or reviewer_defaults.get("api_key_mode") or "env_name"),
        "api_key_env_name": str(runtime.get("api_key_env_name") or reviewer_defaults.get("api_key_env_name") or ""),
        "capabilities": {
            **_default_ai_reviewer_config().get("capabilities", {}),
            **((reviewer_defaults or {}).get("capabilities") or {}),
        },
        "score_adjustment_limit": {
            **_default_ai_reviewer_config().get("score_adjustment_limit", {}),
            **((reviewer_defaults or {}).get("score_adjustment_limit") or {}),
        },
    }
    if direct_key:
        effective_ai_cfg["api_key_value"] = direct_key
    else:
        effective_ai_cfg.pop("api_key_value", None)

    effective_scoring_cfg["ai_reviewer"] = effective_ai_cfg
    return effective_scoring_cfg


def _save_batch_ai_reviewer_defaults_for_jd(jd_title: str, runtime_cfg: dict | None) -> tuple[bool, str]:
    clean_title = str(jd_title or "").strip()
    if not clean_title:
        return False, "当前未选择岗位，无法保存默认设置。"

    runtime = _normalize_batch_ai_reviewer_runtime_config(runtime_cfg, jd_title=clean_title)
    safe_runtime = _sanitize_ai_runtime_cfg_for_storage(runtime)
    scoring_cfg = _normalize_scoring_config(load_jd_scoring_config(clean_title))
    existing_ai_cfg = scoring_cfg.get("ai_reviewer") if isinstance(scoring_cfg.get("ai_reviewer"), dict) else {}
    scoring_cfg["ai_reviewer"] = {
        **_default_ai_reviewer_config(),
        **existing_ai_cfg,
        "enable_ai_reviewer": bool(safe_runtime.get("enable_ai_reviewer", False)),
        "ai_reviewer_mode": "suggest_only",
        "provider": str(safe_runtime.get("provider") or existing_ai_cfg.get("provider") or "openai"),
        "model": str(safe_runtime.get("model") or existing_ai_cfg.get("model") or ""),
        "api_base": str(safe_runtime.get("api_base") or existing_ai_cfg.get("api_base") or ""),
        "api_key_mode": str(safe_runtime.get("api_key_mode") or existing_ai_cfg.get("api_key_mode") or "env_name"),
        "api_key_env_name": str(safe_runtime.get("api_key_env_name") or existing_ai_cfg.get("api_key_env_name") or ""),
        "auto_generate_for_new_batch": bool(safe_runtime.get("auto_generate_for_new_batch", False)),
        "capabilities": {
            **_default_ai_reviewer_config().get("capabilities", {}),
            **(existing_ai_cfg.get("capabilities") or {}),
        },
        "score_adjustment_limit": {
            **_default_ai_reviewer_config().get("score_adjustment_limit", {}),
            **(existing_ai_cfg.get("score_adjustment_limit") or {}),
        },
    }
    upsert_jd_scoring_config(clean_title, scoring_cfg)

    if str(st.session_state.get("joblib_selected_title") or "").strip() == clean_title:
        st.session_state.joblib_draft_scoring_config = _normalize_scoring_config(scoring_cfg)

    if str(runtime.get("api_key_mode") or "").strip().lower() == "direct_input":
        return True, "已保存当前岗位默认设置。出于安全考虑，直接输入的 API Key 不会被保存，下次仍需重新输入。"
    return True, "已保存当前岗位默认设置；下次切到该岗位时会自动带出这套配置。"


def _batch_ai_reviewer_widget_sync_payload(runtime: dict, *, jd_title: str) -> dict:
    payload = dict(runtime or {})
    provider = str(payload.get("provider") or "openai")
    return {
        "batch_ai_reviewer_enable": bool(payload.get("enable_ai_reviewer", False)),
        "batch_ai_reviewer_provider": provider,
        "batch_ai_reviewer_auto_generate": bool(payload.get("auto_generate_for_new_batch", False)),
        "batch_ai_reviewer_runtime_api_key_mode": _default_ai_key_mode_for_ui(provider, payload),
        "batch_ai_reviewer_runtime_model": str(payload.get("model") or ""),
        "batch_ai_reviewer_runtime_model_preset": str(payload.get("model") or ""),
        "batch_ai_reviewer_runtime_api_base": str(payload.get("api_base") or ""),
        "batch_ai_reviewer_runtime_api_key_env": str(payload.get("api_key_env_name") or ""),
        "batch_ai_reviewer_runtime_api_key_value": str(payload.get("api_key_value") or ""),
        "batch_ai_reviewer_runtime_provider_prev": provider,
        "batch_ai_reviewer_runtime_jd_prev": str(jd_title or "").strip(),
    }


def _apply_pending_batch_ai_reviewer_widget_state() -> None:
    pending = st.session_state.pop("batch_ai_reviewer_widget_sync_pending", None)
    if not isinstance(pending, dict):
        return
    for key, value in pending.items():
        st.session_state[key] = value


def _sync_batch_ai_reviewer_widget_state(jd_title: str) -> None:
    clean_title = str(jd_title or "").strip()
    prev_title = str(st.session_state.get("batch_ai_reviewer_runtime_jd_prev") or "").strip()
    if prev_title == clean_title:
        return

    runtime = _normalize_batch_ai_reviewer_runtime_config({}, jd_title=clean_title)
    st.session_state["batch_ai_reviewer_widget_sync_pending"] = _batch_ai_reviewer_widget_sync_payload(
        runtime,
        jd_title=clean_title,
    )


def _current_batch_ai_reviewer_runtime(jd_title: str) -> dict:
    runtime_cfg = {
        "enable_ai_reviewer": bool(st.session_state.get("batch_ai_reviewer_enable", False)),
        "provider": str(st.session_state.get("batch_ai_reviewer_provider") or "openai"),
        "model": str(st.session_state.get("batch_ai_reviewer_runtime_model") or ""),
        "api_base": str(st.session_state.get("batch_ai_reviewer_runtime_api_base") or ""),
        "api_key_mode": str(st.session_state.get("batch_ai_reviewer_runtime_api_key_mode") or "direct_input"),
        "api_key_env_name": str(st.session_state.get("batch_ai_reviewer_runtime_api_key_env") or ""),
        "api_key_value": str(st.session_state.get("batch_ai_reviewer_runtime_api_key_value") or ""),
        "auto_generate_for_new_batch": bool(st.session_state.get("batch_ai_reviewer_auto_generate", False)),
    }
    return _normalize_batch_ai_reviewer_runtime_config(runtime_cfg, jd_title=jd_title)


def _hydrate_batch_ai_reviewer_runtime(payload: dict | None, jd_title: str) -> dict:
    batch_payload = payload if isinstance(payload, dict) else {}
    details = batch_payload.get("details") if isinstance(batch_payload.get("details"), dict) else {}
    stored_runtime = batch_payload.get("batch_ai_reviewer_runtime")
    if not isinstance(stored_runtime, dict):
        stored_runtime = {}
    if not stored_runtime:
        for detail in details.values():
            stored_runtime = _extract_batch_ai_reviewer_runtime_from_detail(detail)
            if stored_runtime:
                break

    runtime = _normalize_batch_ai_reviewer_runtime_config(stored_runtime, jd_title=jd_title)
    batch_id = str(batch_payload.get("batch_id") or "").strip()
    if str(runtime.get("api_key_mode") or "").strip().lower() == "direct_input":
        runtime["api_key_value"] = _resolve_batch_ai_direct_key(batch_id)
    batch_payload["batch_ai_reviewer_runtime"] = dict(runtime)
    for detail in details.values():
        if isinstance(detail, dict):
            _apply_batch_ai_reviewer_runtime_to_detail(detail, runtime, jd_title=jd_title)
    return runtime


def _all_ai_preset_models() -> set[str]:
    models: set[str] = set()
    for provider in get_ai_provider_options():
        for item in get_ai_model_presets(provider):
            value = str(item.get("value") or "").strip()
            if value:
                models.add(value)
    return models


def _sync_ai_config_defaults(prefix: str, provider: str, *, model_fallback: str = "") -> None:
    provider_key = f"{prefix}_provider_prev"
    model_key = f"{prefix}_model"
    model_preset_key = f"{prefix}_model_preset"
    api_base_key = f"{prefix}_api_base"
    env_key = f"{prefix}_api_key_env"
    current_provider = str(provider or "openai").strip().lower() or "openai"
    previous_provider = str(st.session_state.get(provider_key) or "").strip().lower()
    if previous_provider == current_provider:
        return

    known_models = _all_ai_preset_models()
    current_model = str(st.session_state.get(model_key) or "").strip()
    current_api_base = str(st.session_state.get(api_base_key) or "").strip()
    current_env = str(st.session_state.get(env_key) or "").strip()

    if not current_model or current_model == get_default_ai_model(previous_provider or "openai") or current_model in known_models:
        st.session_state[model_key] = model_fallback or get_default_ai_model(current_provider)
        st.session_state[model_preset_key] = st.session_state[model_key]
    if not current_api_base or current_api_base == get_default_ai_api_base(previous_provider or "openai"):
        st.session_state[api_base_key] = get_default_ai_api_base(current_provider)
    if not current_env or current_env == get_default_ai_api_key_env_name(previous_provider or "openai"):
        st.session_state[env_key] = get_default_ai_api_key_env_name(current_provider)

    st.session_state[provider_key] = current_provider


def _render_ai_model_selector(prefix: str, provider: str, current_model: str, *, label: str = "model") -> str:
    presets = get_ai_model_presets(provider)
    preset_map = {str(item.get("value") or ""): str(item.get("label") or item.get("value") or "") for item in presets}
    preset_options = ["__custom__"] + list(preset_map.keys())
    current_model_clean = str(current_model or "").strip()
    preset_default = current_model_clean if current_model_clean in preset_map else "__custom__"

    preset_choice = st.selectbox(
        f"{label} 预设",
        options=preset_options,
        index=preset_options.index(preset_default),
        format_func=lambda value: "自定义输入" if value == "__custom__" else preset_map.get(value, value),
        key=f"{prefix}_model_preset",
    )
    if preset_choice != "__custom__":
        st.session_state[f"{prefix}_model"] = preset_choice

    return st.text_input(
        label,
        value=st.session_state.get(f"{prefix}_model", current_model_clean or get_default_ai_model(provider)),
        key=f"{prefix}_model",
    ).strip()


def _render_ai_api_key_config_inputs(prefix: str, provider: str, current_cfg: dict | None) -> dict:
    cfg = current_cfg if isinstance(current_cfg, dict) else {}
    mode_key = f"{prefix}_api_key_mode"
    direct_key_key = f"{prefix}_api_key_value"
    env_key = f"{prefix}_api_key_env"

    st.session_state[mode_key] = "direct_input"
    stored_key = _get_user_api_key(provider)
    if not str(st.session_state.get(direct_key_key) or "").strip() and stored_key:
        st.session_state[direct_key_key] = stored_key

    direct_value = st.text_input(
        "API Key（当前账号）",
        value=str(st.session_state.get(direct_key_key) or ""),
        key=direct_key_key,
        type="password",
        help="将保存到当前账号，仅本账号可见。不会在页面明文显示。",
    ).strip()

    cols = st.columns(2)
    if cols[0].button("保存到我的账号", key=f"{prefix}_save_user_key"):
        if direct_value:
            _set_user_api_key(provider, direct_value)
            st.success("已保存到当前账号。")
        else:
            st.warning("请先输入 API Key。")
    if cols[1].button("清除已保存的 Key", key=f"{prefix}_clear_user_key"):
        _set_user_api_key(provider, "")
        st.session_state[direct_key_key] = ""
        st.success("已清除当前账号保存的 Key。")

    env_name = ""
    mode = "direct_input"

    return {
        "api_key_mode": mode,
        "api_key_env_name": env_name,
        "api_key_value": direct_value,
    }


def _render_ai_runtime_hint(
    provider: str,
    api_base: str,
    api_key_env_name: str,
    *,
    api_key_mode: str = "env_name",
    api_key_value: str = "",
) -> None:
    runtime_cfg = resolve_ai_runtime_config(
        {
            "provider": provider,
            "api_base": api_base,
            "api_key_env_name": api_key_env_name,
            "api_key_mode": api_key_mode,
            "api_key_value": api_key_value,
        }
    )
    resolved_base = str(runtime_cfg.get("api_base") or "")
    resolved_env = str(runtime_cfg.get("api_key_env_name") or "")
    env_exists = bool(os.getenv(resolved_env or "", "").strip()) if resolved_env else False
    direct_present = bool(str(runtime_cfg.get("api_key_value") or "").strip())
    base_required = provider_requires_explicit_api_base(provider)
    base_missing = base_required and not str(api_base or "").strip()
    if str(runtime_cfg.get("api_key_mode") or "env_name") == "direct_input":
        api_key_hint = f"API Key：当前使用直接输入 key 模式（{'已填写' if direct_present else '未填写'}）"
    else:
        api_key_hint = f"环境变量：{resolved_env or '-'} {'已检测到' if env_exists else '未检测到'}"

    st.caption(
        f"{api_key_hint} ｜ api_base：{resolved_base or '-'}{'（required）' if base_required else ''} ｜ "
        f"api_base 状态：{'未填写' if base_missing else '已就绪'}"
    )


def _render_ai_runtime_warning(
    provider: str,
    api_base: str,
    api_key_env_name: str,
    *,
    api_key_mode: str = "env_name",
    api_key_value: str = "",
    enabled: bool,
    feature_label: str,
) -> None:
    if not enabled:
        return

    runtime_cfg = resolve_ai_runtime_config(
        {
            "provider": provider,
            "api_base": api_base,
            "api_key_env_name": api_key_env_name,
            "api_key_mode": api_key_mode,
            "api_key_value": api_key_value,
        }
    )
    provider_norm = str(runtime_cfg.get("provider") or "").strip().lower()
    resolved_env = str(runtime_cfg.get("api_key_env_name") or "")
    env_exists = bool(os.getenv(resolved_env or "", "").strip()) if resolved_env else False
    direct_present = bool(str(runtime_cfg.get("api_key_value") or "").strip())
    base_missing = provider_requires_explicit_api_base(provider_norm) and not str(api_base or "").strip()

    if provider_norm == "mock":
        st.info(f"当前 {feature_label} 使用 mock provider，会直接返回结构化 stub，不发真实网络请求。")
        return

    if provider_norm not in {"openai", "openai_compatible", "deepseek"}:
        st.info(
            f"当前 {feature_label} provider={provider_norm or '-'} 暂未实现真实调用，运行时会 fallback 到 stub，并在 meta.reason 标明原因。"
        )
        return

    if base_missing:
        st.info(f"当前 {feature_label} 需要填写 api_base；留空时不会发起真实请求，会 fallback 到 stub。")

    if str(runtime_cfg.get("api_key_mode") or "env_name") == "direct_input":
        if not direct_present:
            st.info(f"当前 {feature_label} 使用直接输入 key 模式，但尚未输入 API Key；测试连接或真实调用会失败。")
    else:
        if _looks_like_api_key_input(resolved_env):
            st.warning("环境变量名输入框里看起来填入了真实 API key。请改填例如 DEEPSEEK_API_KEY，或切换到“直接输入 API Key”模式。")
        elif not env_exists:
            st.info(f"当前未检测到 {feature_label} 对应的环境变量 {resolved_env or '-'}，运行时会 fallback 到 stub。")


def _render_ai_connection_result(result: dict | None) -> None:
    if not result:
        return
    if result.get("success"):
        st.success(f"测试 AI 连接成功：{result.get('reason') or '请求成功'}")
    else:
        st.warning(f"测试 AI 连接失败：{result.get('reason') or '请求失败'}")
    api_key_mode = str(result.get("api_key_mode") or "env_name")
    api_key_desc = (
        f"直接输入 API Key（{'已填写' if bool(result.get('api_key_present')) else '未填写'}）"
        if api_key_mode == "direct_input"
        else f"环境变量名：{result.get('api_key_env_name') or '-'}（{'已检测到' if bool(result.get('api_key_env_detected')) else '未检测到'}）"
    )
    st.json(
        {
            "provider": result.get("provider"),
            "model": result.get("model"),
            "api_base": result.get("api_base"),
            "api_key_config": api_key_desc,
            "success": bool(result.get("success")),
            "reason": result.get("reason") or "",
            "request_id": result.get("request_id") or "",
            "latency_ms": int(result.get("latency_ms") or 0),
        }
    )


WEIGHT_SUM_TOLERANCE = 1e-6
WEIGHT_FIELD_HELP = {
    "教育背景匹配度": {
        "summary": "看候选人的学历层级、专业相关性，以及教育背景是否能支撑岗位起点。",
        "guidance": "适合校招、研究导向、专业门槛强的岗位。",
        "bias": "拉得太高会放大学历优势，可能低估实践能力强但专业不完全对口的人。",
    },
    "相关经历匹配度": {
        "summary": "看实习、项目、职责和产出，是否真正贴近岗位要解决的问题。",
        "guidance": "适合强调项目落地、岗位上手速度、同类经验复用的岗位。",
        "bias": "拉得太高会更偏爱有直接同类经历的人，转岗潜力型候选人会吃亏。",
    },
    "技能匹配度": {
        "summary": "看岗位关键技能是否命中，比如 SQL、Python、PRD、研究方法或工具链。",
        "guidance": "适合技能门槛明确、入岗就要用到核心工具的岗位。",
        "bias": "拉得太高会更偏向工具命中，可能低估方法论或业务理解较强的人。",
    },
    "表达完整度": {
        "summary": "看简历是否结构清楚、信息完整，能不能快速支持判断。",
        "guidance": "适合作为辅助维度，帮助区分信息充分和信息缺失的简历。",
        "bias": "拉得太高会放大简历写作差异，可能误伤经历不错但表达一般的人。",
    },
}


def _weight_widget_key(selected_job: str, dimension: str) -> str:
    return f"joblib_weight_{selected_job}_{dimension}"


def _read_weight_widget_values(selected_job: str, default_weights: dict[str, float]) -> dict[str, float]:
    values: dict[str, float] = {}
    for dim in BASE_WEIGHT_KEYS:
        raw_value = st.session_state.get(_weight_widget_key(selected_job, dim), default_weights.get(dim, 0.25))
        try:
            values[dim] = max(0.0, min(1.0, float(raw_value or 0.0)))
        except (TypeError, ValueError):
            values[dim] = max(0.0, min(1.0, float(default_weights.get(dim, 0.25) or 0.25)))
    return values


def _apply_weight_widget_state(selected_job: str, weights: dict[str, float]) -> None:
    for dim in BASE_WEIGHT_KEYS:
        st.session_state[_weight_widget_key(selected_job, dim)] = round(float(weights.get(dim, 0.25) or 0.25), 2)


def _apply_scoring_widget_state(selected_job: str, scoring_cfg: dict) -> None:
    weights = scoring_cfg.get("weights") if isinstance(scoring_cfg.get("weights"), dict) else {}
    thresholds = (
        scoring_cfg.get("screening_thresholds")
        if isinstance(scoring_cfg.get("screening_thresholds"), dict)
        else scoring_cfg.get("thresholds")
    ) or {}
    hard_cfg = scoring_cfg.get("hard_thresholds") if isinstance(scoring_cfg.get("hard_thresholds"), dict) else {}

    _apply_weight_widget_state(selected_job, weights)
    st.session_state[f"joblib_thr_pass_{selected_job}"] = int(thresholds.get("pass_line", 4) or 4)
    st.session_state[f"joblib_thr_review_{selected_job}"] = int(thresholds.get("review_line", 3) or 3)
    st.session_state[f"joblib_thr_exp_{selected_job}"] = int(thresholds.get("min_experience", 2) or 2)
    st.session_state[f"joblib_thr_skill_{selected_job}"] = int(thresholds.get("min_skill", 2) or 2)
    st.session_state[f"joblib_thr_expr_{selected_job}"] = int(thresholds.get("min_expression", 2) or 2)

    all_hard_keys: set[str] = set()
    for profile_name in get_profile_options():
        for hard_key, _ in _profile_hard_flag_options(profile_name):
            all_hard_keys.add(hard_key)
    for hard_key in all_hard_keys:
        st.session_state[f"joblib_hard_{selected_job}_{hard_key}"] = bool(hard_cfg.get(hard_key, False))


def _normalize_scoring_config(scoring_cfg: dict | None) -> dict:
    cfg = scoring_cfg if isinstance(scoring_cfg, dict) else {}
    profile_name = cfg.get("role_template") or cfg.get("profile_name") or "AI产品经理 / 大模型产品经理"
    default_cfg = build_default_scoring_config(profile_name)
    thresholds = cfg.get("screening_thresholds") if isinstance(cfg.get("screening_thresholds"), dict) else cfg.get("thresholds")
    hard_flags = cfg.get("hard_thresholds") if isinstance(cfg.get("hard_thresholds"), dict) else cfg.get("hard_flags")
    normalized = {
        "profile_name": profile_name,
        "role_template": profile_name,
        "weights": {
            dim: float((cfg.get("weights") or {}).get(dim, (default_cfg.get("weights") or {}).get(dim, 0.25)) or 0.0)
            for dim in BASE_WEIGHT_KEYS
        },
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
            creator_name = rec.get("created_by_name") or ""
            creator_email = rec.get("created_by_email") or ""
            creator_display = creator_name or creator_email or "未填写"
            st.caption(
                f"最近批次时间：{snapshot.get('latest_time', '-')}"
                f" ｜ 当前空缺人数：{openings}"
                f" ｜ 当前候选池：通过 {snapshot.get('pass_count', 0)}"
                f" / 待复核 {snapshot.get('review_count', 0)}"
                f" / 淘汰 {snapshot.get('reject_count', 0)}"
            )
            st.caption(f"创建人：{creator_display}")
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

        edit_upload = st.file_uploader(
            "重新导入 JD 文档（txt / pdf / docx）",
            type=["txt", "pdf", "docx"],
            key=f"joblib_edit_jd_upload_{selected_job}",
            help="上传后会覆盖当前编辑草稿，但不会自动保存到数据库。",
        )
        _handle_edit_jd_upload(selected_job, edit_upload)
        edit_meta_map = st.session_state.get("joblib_edit_jd_upload_meta_by_job", {})
        _render_jd_upload_feedback(
            edit_meta_map.get(selected_job) if isinstance(edit_meta_map, dict) else None,
            context_label="编辑区 JD ",
        )

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

        if selected_profile != current_profile:
            scoring_cfg = _normalize_scoring_config(build_default_scoring_config(selected_profile))
            st.session_state.joblib_draft_scoring_config = scoring_cfg
            _apply_scoring_widget_state(selected_job, scoring_cfg)

        st.caption("四项基础权重")
        st.caption("权重越高，该维度对总分影响越大。当前总和必须为 1；如果不确定，建议先使用模板默认值。")
        weight_cfg = scoring_cfg.get("weights") or {}
        weight_action_cols = st.columns([1, 1, 2])
        with weight_action_cols[0]:
            if st.button("恢复模板默认值", key=f"joblib_restore_profile_defaults_{selected_job}", use_container_width=True):
                scoring_cfg = _normalize_scoring_config(build_default_scoring_config(selected_profile))
                st.session_state.joblib_draft_scoring_config = scoring_cfg
                _apply_scoring_widget_state(selected_job, scoring_cfg)
                st.rerun()
        with weight_action_cols[1]:
            if st.button("自动归一化", key=f"joblib_normalize_weights_{selected_job}", use_container_width=True):
                normalized_weights = normalize_weights(
                    _read_weight_widget_values(selected_job, weight_cfg),
                    fallback=weight_cfg,
                )
                _apply_weight_widget_state(selected_job, normalized_weights)
                st.rerun()
        with weight_action_cols[2]:
            with st.expander("权重说明", expanded=False):
                for wk in BASE_WEIGHT_KEYS:
                    field_help = WEIGHT_FIELD_HELP.get(wk, {})
                    st.markdown(
                        f"**{wk}**\n\n"
                        f"代表什么：{field_help.get('summary', '-')}\n\n"
                        f"建议如何调：{field_help.get('guidance', '-')}\n\n"
                        f"极端设置的偏差：{field_help.get('bias', '-')}"
                    )

        wcols = st.columns(2)
        weight_values = {}
        for idx, wk in enumerate(BASE_WEIGHT_KEYS):
            weight_values[wk] = wcols[idx % 2].slider(
                wk,
                min_value=0.0,
                max_value=1.0,
                step=0.01,
                value=float(weight_cfg.get(wk, 0.25) or 0.25),
                key=_weight_widget_key(selected_job, wk),
                help=WEIGHT_FIELD_HELP.get(wk, {}).get("summary"),
            )

        current_weight_total = weight_total(weight_values)
        weights_valid = is_weight_total_valid(weight_values, tolerance=WEIGHT_SUM_TOLERANCE)
        st.caption(f"当前总和：{current_weight_total:.2f} / 1.00")
        if weights_valid:
            st.success("当前权重总和正确，可保存修改。")
        else:
            st.warning("四项权重总和必须等于 1.00。请手动调整，或点击“自动归一化”；未修正前无法保存。")

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
        st.caption("用于基于岗位 JD 生成评分细则建议；规则评分器仍为主，AI 只给建议。")
        ai_cols = st.columns(2)
        enable_ai_rule_suggester = ai_cols[0].toggle(
            "启用 AI 评分细则建议",
            value=bool(ai_rule_cfg.get("enable_ai_rule_suggester", False)),
            key=f"joblib_ai_enable_{selected_job}",
        )
        provider_options = get_ai_provider_options()
        current_rule_provider = str(ai_rule_cfg.get("provider", "openai") or "openai")
        provider = ai_cols[1].selectbox(
            "provider",
            options=provider_options,
            index=provider_options.index(current_rule_provider) if current_rule_provider in provider_options else 0,
            key=f"joblib_ai_rule_provider_{selected_job}",
        )
        rule_runtime_prefix = f"joblib_ai_rule_runtime_{selected_job}"
        _sync_ai_config_defaults(
            rule_runtime_prefix,
            provider,
            model_fallback=str(ai_rule_cfg.get("model") or get_default_ai_model(provider)),
        )
        ai_cols2 = st.columns(2)
        with ai_cols2[0]:
            model_name = _render_ai_model_selector(
                rule_runtime_prefix,
                provider,
                str(ai_rule_cfg.get("model") or get_default_ai_model(provider)),
                label="model",
            )
        with ai_cols2[1]:
            api_base = st.text_input(
                "api_base（可选）",
                value=st.session_state.get(
                    f"{rule_runtime_prefix}_api_base",
                    str(ai_rule_cfg.get("api_base") or get_default_ai_api_base(provider)),
                ),
                key=f"{rule_runtime_prefix}_api_base",
            ).strip()
        key_cfg = _render_ai_api_key_config_inputs(rule_runtime_prefix, provider, ai_rule_cfg)

        _render_ai_runtime_hint(
            provider,
            api_base,
            str(key_cfg.get("api_key_env_name") or ""),
            api_key_mode=str(key_cfg.get("api_key_mode") or "direct_input"),
            api_key_value=str(key_cfg.get("api_key_value") or ""),
        )
        _render_ai_runtime_warning(
            provider,
            api_base,
            str(key_cfg.get("api_key_env_name") or ""),
            api_key_mode=str(key_cfg.get("api_key_mode") or "direct_input"),
            api_key_value=str(key_cfg.get("api_key_value") or ""),
            enabled=bool(enable_ai_rule_suggester),
            feature_label="AI 评分细则建议",
        )
        rule_runtime_cfg = {
            "enable_ai_rule_suggester": bool(enable_ai_rule_suggester),
            "provider": provider,
            "model": model_name,
            "api_base": api_base,
            **key_cfg,
        }
        rule_connection_key = f"joblib_ai_rule_connection_test_{selected_job}"
        rule_action_cols = st.columns(2)
        with rule_action_cols[0]:
            if st.button("测试 AI 连接", key=f"joblib_ai_rule_test_btn_{selected_job}", use_container_width=True):
                st.session_state[rule_connection_key] = test_ai_connection(rule_runtime_cfg, purpose="ai_rule_suggester")
        with rule_action_cols[1]:
            if st.button("AI 生成评分细则建议", key=f"joblib_ai_suggest_btn_{selected_job}", use_container_width=True):
                suggestion = run_ai_rule_suggester(selected_profile, scoring_cfg, edited_text, rule_runtime_cfg)
                st.session_state[f"joblib_ai_suggestion_{selected_job}"] = suggestion
                st.session_state[f"joblib_ai_suggestion_text_{selected_job}"] = json.dumps(suggestion, ensure_ascii=False, indent=2)
                suggestion_meta = suggestion.get("meta") if isinstance(suggestion.get("meta"), dict) else {}
                if str(suggestion_meta.get("source") or "") == "stub":
                    st.warning(f"当前返回为 stub fallback：{suggestion_meta.get('reason') or '未获取到真实模型结果'}")
                else:
                    st.success("AI 评分细则建议生成成功。")

        _render_ai_connection_result(st.session_state.get(rule_connection_key))

        ai_suggestion = st.session_state.get(f"joblib_ai_suggestion_{selected_job}")
        if ai_suggestion:
            ai_suggestion_meta = ai_suggestion.get("meta") if isinstance(ai_suggestion.get("meta"), dict) else {}
            st.caption(
                f"source：{ai_suggestion_meta.get('source') or '-'} ｜ "
                f"provider：{ai_suggestion_meta.get('provider') or '-'} ｜ "
                f"model：{ai_suggestion_meta.get('model') or '-'}"
            )
            if ai_suggestion_meta.get("reason"):
                st.caption(f"说明：{ai_suggestion_meta.get('reason')}")
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

        st.markdown("**AI reviewer 默认配置**")
        st.caption("JD 页面只保留 reviewer 的默认 provider / model / 能力开关；批次运行期开关和 API 配置请到“批量初筛”页面设置。")
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

        st.info("AI reviewer 的运行期开关、API 配置和“新批次自动生成 AI 建议”已移到“批量初筛”页面。候选人工作台会继承批次设置。")
        reviewer_default_cols = st.columns(2)
        reviewer_provider_options = get_ai_provider_options()
        current_reviewer_provider = str(reviewer_cfg.get("provider", "openai") or "openai")
        reviewer_provider = reviewer_default_cols[0].selectbox(
            "默认 provider（审核员）",
            options=reviewer_provider_options,
            index=reviewer_provider_options.index(current_reviewer_provider) if current_reviewer_provider in reviewer_provider_options else 0,
            key=f"joblib_ai_reviewer_provider_{selected_job}",
        )
        reviewer_default_prefix = f"joblib_ai_reviewer_default_{selected_job}"
        _sync_ai_config_defaults(
            reviewer_default_prefix,
            reviewer_provider,
            model_fallback=str(reviewer_cfg.get("model") or get_default_ai_model(reviewer_provider)),
        )
        with reviewer_default_cols[1]:
            reviewer_model = _render_ai_model_selector(
                reviewer_default_prefix,
                reviewer_provider,
                str(reviewer_cfg.get("model") or get_default_ai_model(reviewer_provider)),
                label="默认 model（审核员）",
            )

        st.caption("AI 可操作范围（默认建议层）")
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

        reviewer_api_base = str(reviewer_cfg.get("api_base") or get_default_ai_api_base(reviewer_provider)).strip()
        reviewer_api_key_env_name = str(
            reviewer_cfg.get("api_key_env_name") or get_default_ai_api_key_env_name(reviewer_provider)
        ).strip() or get_default_ai_api_key_env_name(reviewer_provider)
        enable_ai_reviewer = bool(reviewer_cfg.get("enable_ai_reviewer", False))
        ai_reviewer_mode = "suggest_only"

        weights_valid = is_weight_total_valid(weight_values, tolerance=WEIGHT_SUM_TOLERANCE)
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
                **_default_ai_rule_suggester_config(),
                **_sanitize_ai_runtime_cfg_for_storage(rule_runtime_cfg),
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

        is_admin_user = _current_user_is_admin()
        if not is_admin_user:
            st.caption("删除类高风险操作仅管理员可执行。")

        action_cols = st.columns(5)
        with action_cols[0]:
            if st.button("保存修改", use_container_width=True, key="joblib_update_btn", disabled=not weights_valid):
                try:
                    operator = _current_operator()
                    update_jd(
                        selected_job,
                        edited_text,
                        openings=int(edited_openings),
                        created_by_user_id=operator["user_id"],
                        created_by_name=operator["name"],
                        created_by_email=operator["email"],
                        updated_by_user_id=operator["user_id"],
                        updated_by_name=operator["name"],
                        updated_by_email=operator["email"],
                    )
                    upsert_jd_scoring_config(selected_job, st.session_state.get("joblib_draft_scoring_config", {}))
                    _apply_jd_to_workspace(selected_job)
                    _sync_job_management_drafts(selected_job)
                    st.session_state.joblib_flash_success = "岗位已更新。"
                    st.rerun()
                except ValueError as err:
                    st.warning(str(err))
        with action_cols[1]:
            if st.button("仅更新空缺人数", use_container_width=True, key="joblib_update_openings_btn", disabled=not weights_valid):
                try:
                    operator = _current_operator()
                    upsert_jd_openings(
                        selected_job,
                        int(edited_openings),
                        updated_by_user_id=operator["user_id"],
                        updated_by_name=operator["name"],
                        updated_by_email=operator["email"],
                    )
                    upsert_jd_scoring_config(selected_job, st.session_state.get("joblib_draft_scoring_config", {}))
                    _sync_job_management_drafts(selected_job)
                    st.session_state.joblib_flash_success = "空缺人数与评分设置已更新。"
                    st.rerun()
                except ValueError as err:
                    st.warning(str(err))
        with action_cols[2]:
            if st.button("删除岗位", use_container_width=True, key="joblib_delete_btn", disabled=not is_admin_user):
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
                        st.session_state.batch_jd_text_area_pending = ""
                    _sync_job_management_drafts("")
                    st.session_state.joblib_flash_success = "岗位已删除。"
                    st.rerun()
                except ValueError as err:
                    st.warning(str(err))
        with action_cols[3]:
            if st.button("进入批量初筛", use_container_width=True, key="joblib_use_v1_btn"):
                _apply_jd_to_workspace(selected_job)
                _request_page_navigation("批量初筛")
                st.rerun()
        with action_cols[4]:
            if st.button("进入候选人工作台", use_container_width=True, key="joblib_use_v2_btn"):
                _apply_jd_to_workspace(selected_job)
                _request_page_navigation("候选人工作台")
                st.rerun()

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
                _request_page_navigation("候选人工作台")
                st.rerun()

            batch_delete_cols = st.columns(2)
            with batch_delete_cols[0]:
                if st.button(
                    "删除所选批次（不可恢复）",
                    key="joblib_delete_batch_btn",
                    use_container_width=True,
                    disabled=not is_admin_user,
                ):
                    if delete_candidate_batch(batch_choice):
                        _after_batch_deleted(selected_job, batch_choice)
                        st.session_state.joblib_flash_success = f"已删除批次：{batch_choice[:12]}…"
                        st.rerun()
                    else:
                        st.warning("未找到可删除的批次，可能已被删除。")
            with batch_delete_cols[1]:
                if st.button(
                    "清空该岗位所有批次（高风险）",
                    key="joblib_delete_all_batches_btn",
                    use_container_width=True,
                    disabled=not is_admin_user,
                ):
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
    new_upload = st.file_uploader(
        "上传 JD 文档（txt / pdf / docx）",
        type=["txt", "pdf", "docx"],
        key="joblib_new_jd_upload",
        help="上传后会自动提取文本并填充到下方 JD 内容草稿。",
    )
    _handle_new_jd_upload(new_upload)
    _render_jd_upload_feedback(st.session_state.get("joblib_new_jd_upload_meta"), context_label="新建岗位 JD ")
    new_text = st.text_area("JD 内容", height=180, key="joblib_new_text")
    if st.button("新建岗位", type="primary", key="joblib_create_btn"):
        try:
            operator = _current_operator()
            save_jd(
                new_title,
                new_text,
                openings=int(new_openings),
                created_by_user_id=operator["user_id"],
                created_by_name=operator["name"],
                created_by_email=operator["email"],
                updated_by_user_id=operator["user_id"],
                updated_by_name=operator["name"],
                updated_by_email=operator["email"],
            )
            _apply_jd_to_workspace((new_title or "").strip())
            _sync_job_management_drafts((new_title or "").strip())
            st.session_state.joblib_flash_success = "岗位创建成功，已同步到批量初筛。"
            st.rerun()
        except ValueError as err:
            st.warning(str(err))

    _render_admin_account_management()
    _render_environment_health_panel()
    _render_system_health_panel()
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

        is_admin_user = _current_user_is_admin()
        if not is_admin_user:
            st.caption("删除类高风险操作仅管理员可执行。")

        action_cols = st.columns(3)
        with action_cols[0]:
            if st.button("保存 JD", use_container_width=True):
                try:
                    operator = _current_operator()
                    save_jd(
                        effective_title,
                        st.session_state.jd_text,
                        created_by_user_id=operator["user_id"],
                        created_by_name=operator["name"],
                        created_by_email=operator["email"],
                        updated_by_user_id=operator["user_id"],
                        updated_by_name=operator["name"],
                        updated_by_email=operator["email"],
                    )
                    st.success("JD 已保存。")
                except ValueError as err:
                    st.warning(str(err))

        with action_cols[1]:
            if st.button("更新当前 JD", use_container_width=True):
                try:
                    operator = _current_operator()
                    update_jd(
                        effective_title,
                        st.session_state.jd_text,
                        created_by_user_id=operator["user_id"],
                        created_by_name=operator["name"],
                        created_by_email=operator["email"],
                        updated_by_user_id=operator["user_id"],
                        updated_by_name=operator["name"],
                        updated_by_email=operator["email"],
                    )
                    st.success("JD 已更新。")
                except ValueError as err:
                    st.warning(str(err))

        with action_cols[2]:
            if st.button("删除当前 JD", use_container_width=True, disabled=not is_admin_user):
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
            notice = _extract_notice(quality or "weak", message)
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
                    _show_decision(
                        result["screening_result"]["screening_result"],
                        result["screening_result"].get("screening_reasons", []),
                    )
                    for reason in result["screening_result"].get("screening_reasons", []):
                        st.markdown(f"- {reason}")

                    st.markdown("<div class='section-title'>2) 五维评分</div>", unsafe_allow_html=True)
                    _render_score_cards(result["score_details"])

                    st.markdown("<div class='section-title'>3) 维度代表证据</div>", unsafe_allow_html=True)
                    _render_dimension_evidence_summary(
                        result["score_details"],
                        result.get("evidence_bridge", {}),
                    )

                    st.markdown("<div class='section-title'>4) 风险与建议动作</div>", unsafe_allow_html=True)
                    st.markdown("<div class='module-box'>", unsafe_allow_html=True)
                    risk_result = result["risk_result"]
                    risk_level = risk_result.get("risk_level", "unknown")
                    st.info(f"风险等级：**{_risk_level_label(risk_level)}**")
                    st.caption(risk_result.get("risk_summary", ""))
                    st.markdown(f"**建议动作：** {_risk_action(risk_level)}")
                    for rp in risk_result.get("risk_points", []):
                        st.markdown(f"- ⚠️ {rp}")
                    st.markdown("</div>", unsafe_allow_html=True)

                    st.markdown("<div class='section-title'>5) 面试建议</div>", unsafe_allow_html=True)
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

                    st.markdown("<div class='section-title'>6) 关键证据片段摘要</div>", unsafe_allow_html=True)
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


def _run_batch_screening(
    jd_title: str,
    jd_text: str,
    uploaded_files: list,
    *,
    batch_ai_runtime_cfg: dict | None = None,
    force_allow_weak: bool = False,
) -> None:
    """批量初筛执行器：逐文件提取、评估并输出清晰反馈。"""
    operator = _current_operator()
    rows: list[dict] = []
    details: dict[str, dict] = {}
    total = len(uploaded_files)
    batch_ai_runtime = _normalize_batch_ai_reviewer_runtime_config(batch_ai_runtime_cfg, jd_title=jd_title)

    st.session_state.v2_rows = []
    st.session_state.v2_details = {}
    st.session_state.v2_current_batch_id = ""

    progress = st.progress(0.0)
    status_box = st.empty()

    failed_files: list[str] = []
    failed_reasons: list[str] = []
    weak_files: list[str] = []
    ocr_missing_files: list[str] = []
    skipped_files: list[str] = []
    skipped_reasons: list[str] = []
    ai_auto_generated_files: list[str] = []
    ai_auto_failed_files: list[str] = []
    parse_stat_counts = {"正常识别": 0, "弱质量识别": 0, "OCR能力缺失": 0, "读取失败": 0}
    success_count = 0

    for idx, file_obj in enumerate(uploaded_files, start=1):
        file_name = str(getattr(file_obj, "name", "") or f"文件{idx}")
        status_box.info(f"正在处理 {idx}/{total}：{file_name}")

        try:
            extract_result = load_resume_file(file_obj)
            resume_text = str(extract_result.get("text") or "")
            raw_resume_text = str(extract_result.get("raw_ocr_text") or resume_text)
            normalized_resume_text = str(
                extract_result.get("normalized_ocr_text") or normalize_resume_ocr_text(resume_text)
            )
            method = str(extract_result.get("method") or "text")
            quality = str(extract_result.get("quality") or "weak")
            message = str(extract_result.get("message") or "")
            parse_status = _extract_parse_status(extract_result)
            can_evaluate = _can_enter_batch_screening(extract_result)

            if quality.lower() == "weak":
                weak_files.append(file_name)
            if parse_status == "OCR能力缺失":
                ocr_missing_files.append(file_name)
            parse_stat_counts[parse_status] = parse_stat_counts.get(parse_status, 0) + 1
            if not can_evaluate and force_allow_weak:
                message = f"{message}（已强制进入初筛）"
                parse_status = "弱质量识别"
                can_evaluate = True

            if not can_evaluate:
                skipped_files.append(file_name)
                skipped_reasons.append(f"{file_name}：{message or '该文件未进入稳定评估，建议跳过或人工处理。'}")
                continue

            result = _run_pipeline(jd_text, normalized_resume_text, jd_title=jd_title)
            row = build_candidate_row(result, source_name=file_name, index=idx - 1)
            row["提取方式"] = method
            row["提取质量"] = quality
            row["提取提示"] = (
                "⚠ 当前 OCR 识别仍偏弱，建议人工复核"
                if quality.lower() == "weak" and method.lower() == "ocr"
                else "⚠ 建议人工检查提取文本" if quality.lower() == "weak" else ""
            )
            row["提取说明"] = message
            row["解析状态"] = parse_status
            row["解析标签"] = "⚠ OCR缺失" if parse_status == "OCR能力缺失" else ""
            row["处理优先级"] = "普通"
            row["审核摘要"] = _review_summary(
                decision=row.get("初筛结论", ""),
                risk_level=row.get("风险等级", "unknown"),
                risk_summary=row.get("风险摘要", ""),
            )
            row["候选池"] = _candidate_pool_label(row.get("初筛结论", ""))

            detail_payload = dict(result)
            _apply_batch_ai_reviewer_runtime_to_detail(detail_payload, batch_ai_runtime, jd_title=jd_title)
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

            review_record = _build_review_record(result, jd_title=jd_title or "批量初筛岗位", resume_file=file_name)
            append_review(review_record)
            detail_payload["review_id"] = review_record.get("review_id", "")

            if batch_ai_runtime.get("enable_ai_reviewer") and batch_ai_runtime.get("auto_generate_for_new_batch"):
                generated, ai_note = _generate_ai_review_for_batch_detail(
                    detail_payload,
                    runtime_cfg=batch_ai_runtime,
                    operator=operator,
                )
                if generated:
                    ai_auto_generated_files.append(file_name)
                elif ai_note:
                    ai_auto_failed_files.append(f"{file_name}：{ai_note}")

            rows.append(row)
            details[row["candidate_id"]] = detail_payload
            success_count += 1
        except Exception as err:  # noqa: BLE001
            failed_files.append(file_name)
            failed_reasons.append(f"{file_name}：{_friendly_upload_error(err)}")
            parse_stat_counts["读取失败"] += 1
        finally:
            progress.progress(idx / total)

    summary_cols = st.columns(5)
    summary_cols[0].metric("成功数", success_count)
    summary_cols[1].metric("弱质量数", len(set(weak_files)))
    summary_cols[2].metric("OCR 缺失数", len(set(ocr_missing_files)))
    summary_cols[3].metric("读取失败数", len(failed_files))
    summary_cols[4].metric("跳过数", len(skipped_files))

    st.caption(
        f"执行汇总：正常识别 {parse_stat_counts.get('正常识别', 0)} ｜"
        f"弱质量识别 {parse_stat_counts.get('弱质量识别', 0)} ｜"
        f"OCR能力缺失 {parse_stat_counts.get('OCR能力缺失', 0)} ｜"
        f"读取失败 {parse_stat_counts.get('读取失败', 0)}"
    )

    if not rows:
        status_box.warning("批量初筛已结束，但当前没有可进入稳定评估的文件。")
        st.warning("当前批次没有可进入稳定评估的文件。txt/docx 和可提文本的 PDF 可继续；纯图片或扫描版 PDF 若 OCR 缺失，建议先补齐 OCR 环境或人工处理。")
        if skipped_reasons:
            st.markdown("**未进入稳定评估的文件**")
            for reason in skipped_reasons:
                st.caption(reason)
        if failed_reasons:
            st.markdown("**读取失败原因**")
            for reason in failed_reasons:
                st.caption(reason)
        return

    st.session_state.v2_rows = rows
    st.session_state.v2_details = details
    st.session_state.v2_batch_ai_reviewer_runtime = dict(batch_ai_runtime)

    batch_id = save_candidate_batch(
        jd_title=jd_title,
        rows=rows,
        details=details,
        created_by_user_id=operator["user_id"],
        created_by_name=operator["name"],
        created_by_email=operator["email"],
    )
    _remember_batch_ai_direct_key(batch_id, batch_ai_runtime)
    st.session_state.v2_current_batch_id = batch_id
    st.session_state.workspace_selected_jd_title = (jd_title or "").strip() or "未命名岗位"

    st.success(f"批量初筛完成：已生成批次 {batch_id[:12]}...")
    if ocr_missing_files:
        st.warning("检测到 OCR 能力缺失文件：这些文件里只有可提文本的部分被继续评估；纯扫描件/图片已明确跳过。")
        st.caption("OCR 缺失文件：" + ", ".join(sorted(set(ocr_missing_files))))
    if weak_files:
        st.warning(f"以下文件提取质量较弱，建议人工复核：{', '.join(sorted(set(weak_files)))}")
    if skipped_files:
        st.info(f"以下文件未进入稳定评估：{', '.join(skipped_files)}")
        for reason in skipped_reasons:
            st.caption(reason)
    if failed_files:
        st.warning(f"以下文件读取失败：{', '.join(failed_files)}")
        for reason in failed_reasons:
            st.caption(reason)
    if batch_ai_runtime.get("enable_ai_reviewer") and batch_ai_runtime.get("auto_generate_for_new_batch"):
        if ai_auto_generated_files:
            st.info(f"已对 {len(ai_auto_generated_files)} 份简历自动生成 AI reviewer 建议。")
        if ai_auto_failed_files:
            st.warning("部分简历的 AI reviewer 自动生成失败，可在候选人工作台内手动刷新。")
            for reason in ai_auto_failed_files:
                st.caption(reason)
    status_box.success("批量初筛执行完成。")


def _render_candidate_workspace_panel(rows: list[dict], details: dict[str, dict]) -> None:
    """候选人工作台：左侧名单浏览，右侧审核报告。"""
    list_col, detail_col = st.columns([0.9, 1.4], gap="large")

    pass_count = sum(1 for row in rows if _current_candidate_pool(row) == "通过候选人")
    review_count = sum(1 for row in rows if _current_candidate_pool(row) == "待复核候选人")
    reject_count = sum(1 for row in rows if _current_candidate_pool(row) == "淘汰候选人")
    current_operator = _current_operator()
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
        action_feedback_kind = st.session_state.pop("workspace_action_feedback_kind", "success")
        pool_move_feedback = st.session_state.pop("workspace_pool_move_feedback", "")
        pool_empty_feedback = st.session_state.pop("workspace_pool_empty_feedback", "")
        if action_feedback:
            if action_feedback_kind == "warning":
                st.warning(action_feedback)
            elif action_feedback_kind == "info":
                st.info(action_feedback)
            else:
                st.success(action_feedback)
        if pool_move_feedback:
            st.info(pool_move_feedback)
        if pool_empty_feedback:
            st.warning(pool_empty_feedback)

        quick_filter = st.selectbox(
            "快捷筛选（今日处理视角）",
            options=[
                "全部",
                "仅看未人工处理",
                "仅看高优先级",
                "仅看我处理中",
                "仅看他人锁定",
                "仅看未领取",
                "仅看 AI 建议未生成",
                "仅看 AI 建议已生成但未应用",
                "仅看 OCR 弱质量 / OCR 能力缺失",
                "仅看高风险且待复核",
            ],
            key="workspace_quick_filter",
        )
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
        filtered_rows = _apply_workspace_quick_filter(filtered_rows, details, quick_filter)
        filtered_rows = search_by_name(filtered_rows, search_kw)
        filtered_rows = filter_by_risk(filtered_rows, risk_filter)
        filtered_rows = sort_rows(filtered_rows, sort_key)

        selection_scope = _workspace_selection_scope()
        all_candidate_ids = [str(row.get("candidate_id") or "").strip() for row in rows if row.get("candidate_id")]
        filtered_candidate_ids = [str(row.get("candidate_id") or "").strip() for row in filtered_rows if row.get("candidate_id")]
        selected_candidate_ids_all = _get_workspace_selected_candidate_ids(rows, selection_scope)
        selected_candidate_id_set = set(selected_candidate_ids_all)
        selected_rows_all = [row for row in rows if str(row.get("candidate_id") or "").strip() in selected_candidate_id_set]

        st.markdown("**批量操作**")
        st.caption(f"当前已勾选 {len(selected_candidate_ids_all)} 人；当前筛选结果 {len(filtered_rows)} 人。")

        selection_cols = st.columns(2)
        with selection_cols[0]:
            if st.button(
                "勾选当前筛选结果",
                key="workspace_select_filtered_candidates",
                use_container_width=True,
                disabled=not filtered_candidate_ids,
            ):
                changed = _set_workspace_selected_candidate_ids(
                    selection_scope,
                    filtered_candidate_ids,
                    selected=True,
                )
                st.session_state.workspace_action_feedback = f"已勾选当前筛选结果中的 {len(filtered_candidate_ids)} 人。"
                st.session_state.workspace_action_feedback_kind = "success" if changed > 0 else "info"
                st.rerun()
        with selection_cols[1]:
            if st.button(
                "清空当前批次勾选",
                key="workspace_clear_selected_candidates",
                use_container_width=True,
                disabled=not selected_candidate_ids_all,
            ):
                _set_workspace_selected_candidate_ids(
                    selection_scope,
                    all_candidate_ids,
                    selected=False,
                )
                st.session_state.workspace_action_feedback = "已清空当前批次勾选候选人。"
                st.session_state.workspace_action_feedback_kind = "info"
                st.rerun()

        batch_action_cols = st.columns(2)
        active_batch_id = str(st.session_state.get("workspace_preferred_batch_id") or "").strip()
        with batch_action_cols[0]:
            if st.button(
                "批量标记为待复核",
                key="workspace_batch_mark_pending",
                use_container_width=True,
                disabled=not selected_candidate_ids_all,
            ):
                batch_result = _apply_batch_manual_decision(
                    rows=rows,
                    details=details,
                    candidate_ids=selected_candidate_ids_all,
                    manual_decision="待复核",
                    batch_id=active_batch_id,
                )
                updated_count = int(batch_result.get("updated_count", 0))
                skipped_locked_count = int(batch_result.get("skipped_locked_count", 0))
                st.session_state.workspace_action_feedback = (
                    f"已更新 {updated_count} 个，跳过 {skipped_locked_count} 个（被其他 HR 锁定）。"
                    if (updated_count > 0 or skipped_locked_count > 0)
                    else "勾选候选人当前已处于“待复核”状态，无需重复标记。"
                )
                st.session_state.workspace_action_feedback_kind = "success" if updated_count > 0 else "info"
                st.rerun()
        with batch_action_cols[1]:
            if st.button(
                "批量标记为淘汰",
                key="workspace_batch_mark_reject",
                use_container_width=True,
                disabled=not selected_candidate_ids_all,
            ):
                batch_result = _apply_batch_manual_decision(
                    rows=rows,
                    details=details,
                    candidate_ids=selected_candidate_ids_all,
                    manual_decision="淘汰",
                    batch_id=active_batch_id,
                )
                updated_count = int(batch_result.get("updated_count", 0))
                skipped_locked_count = int(batch_result.get("skipped_locked_count", 0))
                st.session_state.workspace_action_feedback = (
                    f"已更新 {updated_count} 个，跳过 {skipped_locked_count} 个（被其他 HR 锁定）。"
                    if (updated_count > 0 or skipped_locked_count > 0)
                    else "勾选候选人当前已处于“淘汰”状态，无需重复标记。"
                )
                st.session_state.workspace_action_feedback_kind = "success" if updated_count > 0 else "info"
                st.rerun()

        priority_cols = st.columns([0.58, 0.42])
        with priority_cols[0]:
            batch_priority = st.selectbox(
                "批量设置处理优先级",
                options=["高", "中", "普通", "低"],
                index=2,
                key="workspace_batch_priority_select",
            )
        with priority_cols[1]:
            if st.button(
                "应用优先级",
                key="workspace_batch_apply_priority",
                use_container_width=True,
                disabled=not selected_candidate_ids_all,
            ):
                batch_result = _apply_batch_priority(
                    rows=rows,
                    details=details,
                    candidate_ids=selected_candidate_ids_all,
                    manual_priority=batch_priority,
                    batch_id=active_batch_id,
                )
                updated_count = int(batch_result.get("updated_count", 0))
                skipped_locked_count = int(batch_result.get("skipped_locked_count", 0))
                st.session_state.workspace_action_feedback = (
                    f"已更新 {updated_count} 个，跳过 {skipped_locked_count} 个（被其他 HR 锁定）。"
                    if (updated_count > 0 or skipped_locked_count > 0)
                    else f"勾选候选人的处理优先级已是“{batch_priority}”。"
                )
                st.session_state.workspace_action_feedback_kind = "success" if updated_count > 0 else "info"
                st.rerun()

        generate_cols = st.columns(2)
        with generate_cols[0]:
            if st.button(
                "批量生成 AI 建议",
                key="workspace_batch_generate_ai",
                use_container_width=True,
                disabled=not selected_candidate_ids_all,
            ):
                with st.spinner("正在为勾选候选人批量生成 AI 建议..."):
                    batch_result = _batch_generate_ai_reviews(
                        rows=rows,
                        details=details,
                        candidate_ids=selected_candidate_ids_all,
                        batch_id=active_batch_id,
                    )
                generated_count = batch_result.get("generated", 0)
                skipped_ready = batch_result.get("skipped_ready", 0)
                skipped_ineligible = batch_result.get("skipped_ineligible", 0)
                skipped_locked = batch_result.get("skipped_locked", 0)
                failed_count = batch_result.get("failed", 0)
                stub_count = batch_result.get("stub", 0)
                if generated_count > 0:
                    st.session_state.workspace_action_feedback = (
                        f"批量生成 AI 建议完成：新增 {generated_count} 人，"
                        f"跳过已有效 {skipped_ready} 人，"
                        f"跳过被锁定 {skipped_locked} 人，跳过其他状态 {skipped_ineligible} 人，失败 {failed_count} 人。"
                        f"{' 其中 stub fallback ' + str(stub_count) + ' 人。' if stub_count > 0 else ''}"
                    )
                    st.session_state.workspace_action_feedback_kind = "warning" if (failed_count > 0 or stub_count > 0) else "success"
                else:
                    st.session_state.workspace_action_feedback = (
                        f"本次未新增 AI 建议：跳过已有效 {skipped_ready} 人，"
                        f"跳过被锁定 {skipped_locked} 人，跳过其他状态 {skipped_ineligible} 人，失败 {failed_count} 人。"
                    )
                    st.session_state.workspace_action_feedback_kind = "info" if failed_count == 0 else "warning"
                st.rerun()
        with generate_cols[1]:
            st.download_button(
                "批量导出勾选候选人 CSV",
                data=rows_to_csv_bytes(selected_rows_all),
                file_name=f"hiremate_selected_candidates_{active_batch_id[:12] or 'session'}.csv",
                mime="text/csv",
                use_container_width=True,
                disabled=not selected_candidate_ids_all,
            )

        context_key = _workspace_context_cache_key(
            selected_jd=str(st.session_state.get("workspace_selected_jd_title", "")),
            batch_id=str(st.session_state.get("workspace_preferred_batch_id", "")),
            pool_label=str(selected_pool_label),
            quick_filter=str(quick_filter),
            search_kw=str(search_kw or ""),
            risk_filter=str(risk_filter),
            sort_key=str(sort_key),
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
                row_pick_cols = st.columns([0.18, 0.82], gap="small")
                with row_pick_cols[0]:
                    st.checkbox(
                        "勾选候选人",
                        key=_workspace_selection_key(selection_scope, row_candidate_id),
                        label_visibility="collapsed",
                    )
                with row_pick_cols[1]:
                    if st.button(
                        f"{label_prefix}{display_name}",
                        key=f"workspace_list_pick_{context_key}_{row_candidate_id}",
                        use_container_width=True,
                        type="primary" if is_active else "secondary",
                    ):
                        selected_cache[context_key] = row_candidate_id
                        st.session_state.workspace_selected_candidate_by_context = selected_cache
                        st.rerun()

                parse_status = row.get("解析状态", "-")
                parse_tag = row.get("解析标签", "")
                detail_for_row = details.get(row_candidate_id) if isinstance(details.get(row_candidate_id), dict) else {}
                lock_badge = str(row.get("锁定状态") or detail_for_row.get("lock_status") or "未领取")
                if bool(detail_for_row.get("is_locked_effective")):
                    if str(detail_for_row.get("lock_owner_user_id") or "") == str(current_operator.get("user_id") or ""):
                        lock_badge = "我处理中"
                    else:
                        lock_badge = "他人锁定"
                elif not str(row.get("锁定状态") or "").strip():
                    lock_badge = "未领取"
                lock_owner = str(row.get("锁定人") or detail_for_row.get("lock_owner_name") or detail_for_row.get("lock_owner_email") or "-")
                row_note = [
                    f"候选池：{_current_candidate_pool(row)}",
                    f"协作：{lock_badge}",
                    f"风险：{_risk_level_label(row.get('风险等级', 'unknown'))}",
                    f"优先级：{row.get('处理优先级', '普通')}",
                    f"解析状态：{parse_status}{(' ' + parse_tag) if parse_tag else ''}",
                ]
                st.caption(" ｜ ".join(row_note))
                if lock_badge != "未领取":
                    st.caption(f"锁定人：{lock_owner} ｜ 锁截止：{row.get('锁过期时间') or detail_for_row.get('lock_expires_at') or '-'}")
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
            st.info("当前筛选结果为空，请切换候选池、快捷筛选、风险筛选、搜索条件或批次后继续。")

    with detail_col:
        st.subheader("候选人审核报告")
        if not filtered_rows or not selected_candidate_id or selected_candidate_id not in candidate_ids:
            st.info("当前无可审核候选人。请先在左侧名单中选择候选人。")
            return

        detail = details.get(selected_candidate_id)
        if not detail:
            st.info("当前候选人详情不可用，请在左侧重新选择候选人。")
            return

        _normalize_ai_review_state(detail)
        cand_id = selected_candidate_id
        selected_row = next((item for item in rows if item.get("candidate_id") == cand_id), {})
        parsed_resume = detail.get("parsed_resume", {})
        extract_info = detail.get("extract_info", {})
        risk_result = detail.get("risk_result", {})
        parse_status = str(selected_row.get("解析状态") or extract_info.get("parse_status") or "-")
        operator = current_operator
        active_batch_id = str(st.session_state.get("workspace_preferred_batch_id") or "").strip()
        lock_state = _refresh_workspace_candidate_lock_state(active_batch_id, cand_id, selected_row, detail)
        can_edit_claimed, lock_edit_message = _can_edit_claimed_candidate(active_batch_id, lock_state, operator)
        operator_display = operator["name"] or operator["email"] or "未填写"

        current_pool = _current_candidate_pool(selected_row) or "未分配"
        auto_decision = detail["screening_result"]["screening_result"]
        manual_decision = detail.get("manual_decision") or "未处理"
        risk_level = risk_result.get("risk_level", "unknown")
        suggested_action = _risk_action(risk_level)
        timeline_summary = _build_timeline_summary(parsed_resume, risk_result)
        timeline_clear = "是" if timeline_summary.get("时间线不清晰风险", "否") == "否" else "否"

        st.caption(f"当前操作人：{operator_display}")
        st.caption("审核摘要条")
        if active_batch_id:
            lock_status_label = "未领取"
            if bool(lock_state.get("is_locked_effective")):
                lock_status_label = "我处理中" if _is_candidate_self_locked(lock_state, operator) else "他人锁定"
            st.markdown("**协作处理状态**")
            st.caption(
                f"状态：{lock_status_label} ｜ 锁定人：{_lock_owner_display(lock_state)} ｜ "
                f"锁到：{lock_state.get('lock_expires_at') or '-'} ｜ 我是否可编辑：{'可编辑' if can_edit_claimed else '只读'}"
            )
            lock_action_cols = st.columns(3)
            with lock_action_cols[0]:
                if st.button(
                    "领取并开始处理",
                    key=f"claim_candidate_lock_{cand_id}",
                    use_container_width=True,
                    disabled=bool(lock_state.get("is_locked_effective")),
                ):
                    ok, new_lock_state = acquire_candidate_lock(
                        active_batch_id,
                        cand_id,
                        operator_user_id=str(operator["user_id"] or ""),
                        operator_name=str(operator["name"] or ""),
                        operator_email=str(operator["email"] or ""),
                        ttl_minutes=WORKSPACE_LOCK_TTL_MINUTES,
                        force=False,
                    )
                    _sync_candidate_lock_state(selected_row, detail, new_lock_state, str(operator["user_id"] or ""))
                    st.session_state.workspace_action_feedback = (
                        "已领取当前候选人，进入可编辑状态。"
                        if ok
                        else "领取失败，当前候选人可能已被其他 HR 抢先领取。"
                    )
                    st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                    st.rerun()
            with lock_action_cols[1]:
                if st.button(
                    "释放锁",
                    key=f"release_candidate_lock_{cand_id}",
                    use_container_width=True,
                    disabled=not _is_candidate_self_locked(lock_state, operator),
                ):
                    ok, message = release_candidate_lock(
                        active_batch_id,
                        cand_id,
                        operator_user_id=str(operator["user_id"] or ""),
                        operator_name=str(operator["name"] or ""),
                        operator_email=str(operator["email"] or ""),
                        is_admin=bool(operator.get("is_admin")),
                        force=False,
                    )
                    refreshed_lock_state = get_candidate_lock_state(active_batch_id, cand_id) or _empty_workspace_lock_state()
                    _sync_candidate_lock_state(selected_row, detail, refreshed_lock_state, str(operator["user_id"] or ""))
                    st.session_state.workspace_action_feedback = message
                    st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                    st.rerun()
            with lock_action_cols[2]:
                if st.button(
                    "管理员强制解锁",
                    key=f"force_release_candidate_lock_{cand_id}",
                    use_container_width=True,
                    disabled=not (
                        bool(operator.get("is_admin"))
                        and bool(lock_state.get("is_locked_effective"))
                        and not _is_candidate_self_locked(lock_state, operator)
                    ),
                ):
                    ok, message = release_candidate_lock(
                        active_batch_id,
                        cand_id,
                        operator_user_id=str(operator["user_id"] or ""),
                        operator_name=str(operator["name"] or ""),
                        operator_email=str(operator["email"] or ""),
                        is_admin=bool(operator.get("is_admin")),
                        force=True,
                    )
                    refreshed_lock_state = get_candidate_lock_state(active_batch_id, cand_id) or _empty_workspace_lock_state()
                    _sync_candidate_lock_state(selected_row, detail, refreshed_lock_state, str(operator["user_id"] or ""))
                    st.session_state.workspace_action_feedback = message
                    st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                    st.rerun()
            if not can_edit_claimed:
                st.info(lock_edit_message)
        else:
            can_edit_claimed = True
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
        st.caption(f"解析状态：{parse_status}")

        st.markdown("**2) 关键时间线**")
        st.write(f"毕业时间：{timeline_summary.get('毕业时间', '未识别')}")
        st.write(f"最近实习/项目时间：{timeline_summary.get('最近实习/项目时间', '未识别')}")
        st.write(f"时间线是否清晰：{timeline_clear}")
        if timeline_summary.get("时间线不清晰风险", "否") == "是":
            st.warning("检测到时间线不清晰风险，建议优先人工核验时间字段。")

        evidence_bridge = _sync_detail_evidence_bridge(detail)
        score_details = detail.get("score_details") or {}

        analysis_payload = detail.get("analysis_payload") if isinstance(detail.get("analysis_payload"), dict) else {}
        extract_info = detail.get("extract_info") if isinstance(detail.get("extract_info"), dict) else {}
        profile = analysis_payload.get("candidate_profile") if isinstance(analysis_payload.get("candidate_profile"), dict) else {}

        st.markdown("**3) AI 结构化画像**")
        _render_ai_structured_profile(profile)

        st.markdown("**4) 解析质量 / 置信度**")
        _render_analysis_confidence_panel(analysis_payload, extract_info)

        st.markdown("**5) 维度代表证据**")
        _render_dimension_evidence_summary(score_details, evidence_bridge)

        st.markdown("**6) 正向证据 / 反证 / 缺失点**")
        _render_grounding_evidence(analysis_payload)

        st.markdown("**7) 关键证据片段摘要**")
        _render_evidence_snippets((detail.get("evidence_snippets", []) or [])[:5])

        st.markdown("**8) 风险与建议动作**")
        st.write(f"风险等级：{_risk_level_label(risk_level)}")
        st.write(f"建议动作：{suggested_action}")
        for rp in risk_result.get("risk_points", []):
            st.markdown(f"- ⚠️ {rp}")

        st.markdown("**9) 面试建议**")
        interview_plan = detail.get("interview_plan", {})
        st.caption("建议追问问题")
        for q in interview_plan.get("interview_questions", []):
            st.markdown(f"- {q}")
        st.caption("重点核实点")
        for fp in interview_plan.get("focus_points", []):
            st.markdown(f"- {fp}")
        st.caption(f"面试总结：{interview_plan.get('interview_summary', '')}")

        st.markdown("**10) AI 建议采纳状态**")
        _render_ai_adoption_status(detail)

        st.markdown("**11) 五维评分（辅助信息）**")
        st.caption(
            _score_brief_summary(
                score_details=score_details,
                timeline_summary=timeline_summary,
            )
        )
        if "scores" in (detail.get("ai_applied_actions") or []):
            st.caption("当前评分包含已人工确认的 AI 建议修正。")

        st.markdown("**12) 证据池调试面板（仅调试）**")
        debug_flag = st.checkbox(
            "显示证据池重排调试信息",
            value=bool(os.getenv("HIREMATE_EVIDENCE_DEBUG", "0").strip() in {"1", "true", "yes", "on"}),
            key=f"workspace_show_evidence_debug_{candidate_id}",
        )
        if debug_flag:
            dim_debug_rows: list[dict] = []
            for dim_key, dim_detail in (score_details or {}).items():
                if not isinstance(dim_detail, dict):
                    continue
                meta = dim_detail.get("meta") if isinstance(dim_detail.get("meta"), dict) else {}
                debug_rows = meta.get("evidence_pool_debug") if isinstance(meta.get("evidence_pool_debug"), list) else []
                if not debug_rows:
                    continue
                dim_debug_rows.append(
                    {
                        "dimension": dim_key,
                        "rows": debug_rows,
                        "thresholds": meta.get("evidence_pool_thresholds"),
                    }
                )

            if not dim_debug_rows:
                st.caption("当前没有证据池调试数据。请在环境变量中设置 HIREMATE_EVIDENCE_DEBUG=1 后重新生成报告。")
            else:
                for item in dim_debug_rows:
                    st.markdown(f"**{_dimension_chip_label(item['dimension'])}**")
                    if item.get("thresholds"):
                        st.caption(f"阈值：{item.get('thresholds')}")
                    st.table(item.get("rows"))
        with st.expander("展开查看五维评分详情", expanded=False):
            ordered_dims = ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度", "综合推荐度"]
            for dim_name in ordered_dims:
                dim_detail = score_details.get(dim_name) or {}
                score_value = dim_detail.get("score", "-")
                st.markdown(f"**{dim_name}：{score_value}/5**")
                reason = dim_detail.get("reason") or ""
                if reason:
                    st.caption(f"说明：{reason}")
                representative = _normalize_representative_evidence(
                    dim_detail.get("representative_evidence")
                    if isinstance(dim_detail.get("representative_evidence"), dict)
                    else {}
                )
                representative_text = str(representative.get("display_text") or "").strip()
                if representative_text:
                    st.caption(f"代表证据：{representative_text}")
                if bool(representative.get("is_low_readability")):
                    st.caption("原文识别质量较弱，建议结合原始提取信息复核。")
                evidences = _remaining_dimension_evidence(dim_detail)
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
                if str(method_raw or "").lower() == "ocr":
                    st.warning("⚠ 当前 OCR 识别仍偏弱，清洗稿已尽量修复标题、时间与段落结构，但关键字段仍建议人工复核。")
                else:
                    st.warning("⚠ 提取质量较弱，建议结合原文人工复核。")
            normalized_text = str(detail.get("normalized_resume_text") or detail.get("raw_resume_text") or "").strip()
            raw_text = str(detail.get("raw_resume_text") or "").strip()
            if normalized_text:
                st.text_area(
                    "解析前清洗稿",
                    value=normalized_text,
                    height=220,
                    disabled=True,
                    key=f"normalized_text_preview_{cand_id}",
                )
            else:
                st.caption("当前批次未保存解析前清洗稿。")
            if raw_text:
                with st.expander("查看提取原文", expanded=False):
                    st.text_area(
                        "提取原文",
                        value=raw_text,
                        height=220,
                        disabled=True,
                        key=f"raw_text_preview_{cand_id}",
                    )
            else:
                st.caption("当前批次未保存提取原文。")

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
        if st.button(
            "保存人工备注",
            key=f"manual_note_save_{cand_id}",
            use_container_width=True,
            disabled=not can_edit_claimed,
        ):
            batch_ok = True
            if active_batch_id:
                batch_ok = upsert_candidate_manual_review(
                    batch_id=active_batch_id,
                    candidate_id=cand_id,
                    manual_note=note_input,
                    operator_user_id=operator["user_id"],
                    operator_name=operator["name"],
                    operator_email=operator["email"],
                    review_id=review_id,
                    jd_title=str(st.session_state.get("workspace_selected_jd_title") or ""),
                    source="workspace",
                    is_admin=bool(operator.get("is_admin")),
                    enforce_lock=True,
                )
            if not batch_ok:
                refreshed_lock_state = get_candidate_lock_state(active_batch_id, cand_id) or _empty_workspace_lock_state()
                _sync_candidate_lock_state(selected_row, detail, refreshed_lock_state, str(operator["user_id"] or ""))
                st.session_state.workspace_action_feedback = "当前候选人已被其他 HR 锁定，人工备注未保存。"
                st.session_state.workspace_action_feedback_kind = "warning"
                st.rerun()
            detail["manual_note"] = note_input
            selected_row["人工备注"] = note_input
            if review_id:
                ok = upsert_manual_review(
                    review_id=review_id,
                    manual_note=note_input,
                    reviewed_by_user_id=operator["user_id"],
                    reviewed_by_name=operator["name"],
                    reviewed_by_email=operator["email"],
                    metadata_updates=_build_workspace_review_metadata(detail, selected_row),
                )
                if not ok:
                    st.warning("未找到对应审核记录，未能写入备注。")
            else:
                st.warning("当前候选人缺少留痕 ID，无法写入备注。")
            st.session_state.v2_rows = rows
            st.session_state.v2_details = details
            st.success("人工备注已写入操作留痕。")

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
        if st.button(
            "保存处理优先级",
            key=f"manual_priority_save_{cand_id}",
            use_container_width=True,
            disabled=not can_edit_claimed,
        ):
            batch_ok = True
            if active_batch_id:
                batch_ok = upsert_candidate_manual_review(
                    batch_id=active_batch_id,
                    candidate_id=cand_id,
                    manual_priority=selected_priority,
                    operator_user_id=operator["user_id"],
                    operator_name=operator["name"],
                    operator_email=operator["email"],
                    review_id=review_id,
                    jd_title=str(st.session_state.get("workspace_selected_jd_title") or ""),
                    source="workspace",
                    is_admin=bool(operator.get("is_admin")),
                    enforce_lock=True,
                )
            if not batch_ok:
                refreshed_lock_state = get_candidate_lock_state(active_batch_id, cand_id) or _empty_workspace_lock_state()
                _sync_candidate_lock_state(selected_row, detail, refreshed_lock_state, str(operator["user_id"] or ""))
                st.session_state.workspace_action_feedback = "当前候选人已被其他 HR 锁定，处理优先级未保存。"
                st.session_state.workspace_action_feedback_kind = "warning"
                st.rerun()
            detail["manual_priority"] = selected_priority
            for row_item in rows:
                if row_item.get("candidate_id") == cand_id:
                    row_item["处理优先级"] = selected_priority
                    break
            if review_id:
                upsert_manual_review(
                    review_id=review_id,
                    reviewed_by_user_id=operator["user_id"],
                    reviewed_by_name=operator["name"],
                    reviewed_by_email=operator["email"],
                    metadata_updates=_build_workspace_review_metadata(detail, selected_row),
                )
            st.session_state.v2_rows = rows
            st.session_state.v2_details = details
            st.success("处理优先级已更新。")
            st.rerun()

        def _apply_manual_decision(manual_decision: str) -> None:
            batch_ok = True
            if active_batch_id:
                batch_ok = upsert_candidate_manual_review(
                    batch_id=active_batch_id,
                    candidate_id=cand_id,
                    manual_decision=manual_decision,
                    manual_note=note_input,
                    manual_priority=detail.get("manual_priority") or selected_row.get("处理优先级") or "普通",
                    operator_user_id=operator["user_id"],
                    operator_name=operator["name"],
                    operator_email=operator["email"],
                    review_id=review_id,
                    jd_title=str(st.session_state.get("workspace_selected_jd_title") or ""),
                    source="workspace",
                    is_admin=bool(operator.get("is_admin")),
                    enforce_lock=True,
                )
            if not batch_ok:
                refreshed_lock_state = get_candidate_lock_state(active_batch_id, cand_id) or _empty_workspace_lock_state()
                _sync_candidate_lock_state(selected_row, detail, refreshed_lock_state, str(operator["user_id"] or ""))
                st.session_state.workspace_action_feedback = "当前候选人已被其他 HR 锁定，人工结论未保存。"
                st.session_state.workspace_action_feedback_kind = "warning"
                st.rerun()

            review_status[cand_id] = manual_decision
            detail["manual_decision"] = manual_decision
            detail["manual_note"] = note_input
            for row in rows:
                if row.get("candidate_id") == cand_id:
                    row["人工最终结论"] = manual_decision
                    row["人工备注"] = note_input
                    break
            if review_id:
                upsert_manual_review(
                    review_id=review_id,
                    manual_decision=manual_decision,
                    manual_note=note_input,
                    reviewed_by_user_id=operator["user_id"],
                    reviewed_by_name=operator["name"],
                    reviewed_by_email=operator["email"],
                    metadata_updates=_build_workspace_review_metadata(detail, selected_row),
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
            if st.button("通过", key=f"manual_pass_{cand_id}", use_container_width=True, disabled=not can_edit_claimed):
                _apply_manual_decision("通过")
        with tag_cols[1]:
            if st.button("待复核", key=f"manual_pending_{cand_id}", use_container_width=True, disabled=not can_edit_claimed):
                _apply_manual_decision("待复核")
        with tag_cols[2]:
            if st.button("淘汰", key=f"manual_reject_{cand_id}", use_container_width=True, disabled=not can_edit_claimed):
                _apply_manual_decision("淘汰")

        st.session_state.v2_manual_review_status = review_status
        current_tag = st.session_state.v2_manual_review_status.get(cand_id)
        if current_tag:
            st.caption(f"当前人工最终决策：{current_tag}")

        st.markdown("**9) AI 审核建议（辅助决策区）**")
        _normalize_ai_review_state(detail)
        ai_suggestion = detail.get("ai_review_suggestion") if isinstance(detail.get("ai_review_suggestion"), dict) else {}
        ai_cfg = ((detail.get("parsed_jd") or {}).get("scoring_config") or {}).get("ai_reviewer") or {}
        ai_mode = str(ai_cfg.get("ai_reviewer_mode") or ai_suggestion.get("mode") or "off")
        ai_enabled = bool(ai_cfg.get("enable_ai_reviewer", False)) and ai_mode != "off"
        ai_meta = ai_suggestion.get("meta") if isinstance(ai_suggestion.get("meta"), dict) else {}
        active_batch_id = st.session_state.get("workspace_preferred_batch_id", "")
        ai_applied_actions = detail.get("ai_applied_actions") if isinstance(detail.get("ai_applied_actions"), list) else []
        ai_status = _refresh_ai_review_freshness(detail)
        current_input_hash = _current_ai_input_hash(detail)
        stored_input_hash = str(detail.get("ai_input_hash") or "").strip()
        display_input_hash = stored_input_hash[:12] if stored_input_hash else "-"
        source_label = detail.get("ai_source") or ai_meta.get("source") or "-"
        model_label = detail.get("ai_model") or ai_meta.get("model") or "-"
        prompt_version = detail.get("ai_prompt_version") or ai_meta.get("prompt_version") or "-"
        latency_ms = int(detail.get("ai_generated_latency_ms") or ai_meta.get("generated_latency_ms") or 0)
        latency_label = f"{latency_ms} ms" if latency_ms > 0 else "-"
        lock_key = _ai_generation_lock_key(cand_id, active_batch_id, review_id)
        generation_locks = st.session_state.setdefault("workspace_ai_generation_locks", {})
        generation_in_progress = bool(generation_locks.get(lock_key))
        ai_generate_disabled = generation_in_progress or (
            bool(active_batch_id)
            and bool(lock_state.get("is_locked_effective"))
            and not _is_candidate_self_locked(lock_state, operator)
        )

        if not ai_enabled:
            st.caption("当前批次未启用 AI reviewer。")
            return

        st.caption("AI 建议默认不生效，需人工点击应用。")
        st.caption(
            f"状态：{_ai_review_status_label(ai_status)}｜source：{source_label}｜model：{model_label}｜"
            f"prompt version：{prompt_version}｜input hash：{display_input_hash}｜latency：{latency_label}"
        )
        if detail.get("ai_generated_at"):
            generated_by = detail.get("ai_generated_by_name") or detail.get("ai_generated_by_email") or "未记录"
            refresh_reason = detail.get("ai_refresh_reason") or detail.get("ai_generation_reason") or "-"
            st.caption(f"最近生成：{detail.get('ai_generated_at')}｜生成人：{generated_by}｜生成原因：{refresh_reason}")
        if generation_in_progress:
            st.info("AI 审核建议正在生成中，请稍候。")
        if ai_status == "ready" and stored_input_hash and stored_input_hash == current_input_hash:
            st.info("当前建议基于最新规则结果，无需刷新。")
        if ai_status == "outdated":
            st.warning("检测到输入已变化，建议刷新 AI 审核建议。")
        if ai_status == "failed" and detail.get("ai_review_error"):
            st.warning(f"最近一次生成失败：{detail.get('ai_review_error')}")
        if source_label == "stub":
            st.info(
                f"当前为 fallback 结果，仅用于辅助参考："
                f"{ai_meta.get('reason') or detail.get('ai_review_error') or '未获取到真实模型结果'}"
            )

        generate_cols = st.columns(2)
        with generate_cols[0]:
            if st.button(
                "生成 AI 审核建议",
                key=f"generate_ai_review_{cand_id}",
                use_container_width=True,
                disabled=ai_generate_disabled,
            ):
                ok, message, feedback_kind = _generate_ai_review_for_candidate(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                    force_refresh=False,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = feedback_kind
                st.rerun()
        with generate_cols[1]:
            if st.button(
                "刷新 AI 审核建议",
                key=f"refresh_ai_review_{cand_id}",
                use_container_width=True,
                disabled=ai_generate_disabled,
            ):
                ok, message, feedback_kind = _generate_ai_review_for_candidate(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                    force_refresh=True,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = feedback_kind
                st.rerun()

        if not ai_suggestion:
            st.caption("当前尚未生成 AI 审核建议。")
            return

        preview = _build_ai_change_preview(detail, selected_row, ai_cfg, ai_suggestion)
        evidence_updates = ai_suggestion.get("evidence_updates") or []
        timeline_updates = ai_suggestion.get("timeline_updates") or []
        risk_adjustment = ai_suggestion.get("risk_adjustment") or {}
        score_adjustments = ai_suggestion.get("score_adjustments") or []

        st.write(f"AI 审核摘要：{ai_suggestion.get('review_summary') or '暂无'}")

        st.caption("建议变更预览")
        if preview["score_rows"]:
            for item in preview["score_rows"]:
                st.markdown(
                    f"- {item['dimension']}：当前 {item['current_score']}，建议 {item['suggested_delta']:+}，"
                    f"应用后 {item['next_score']}｜原因：{item['reason'] or '-'}"
                )
        else:
            st.caption("本次无改分预览。")

        st.markdown(
            f"- 风险等级：当前 {_risk_level_label(preview['current_risk'])}，"
            f"建议 {_risk_level_label(preview['suggested_risk'])}，"
            f"{'会变化' if preview['risk_changed'] else '无变化'}"
        )
        if preview["allow_direct_change"]:
            st.markdown(
                f"- 自动结论：当前 {preview['current_decision'] or '-'}，"
                f"预估应用后 {preview['estimated_decision'] or '-'}"
            )
        else:
            st.markdown(
                f"- 自动结论：当前 {preview['current_decision'] or '-'}。"
                "本次不会自动改变候选池/自动结论。"
            )
        st.markdown(f"- 证据建议：预计新增 {preview['evidence_add_count']} 条")
        st.markdown(f"- 时间线建议：预计新增 {preview['timeline_add_count']} 条")

        st.caption("AI 建议内容")
        st.caption("AI 补充证据建议")
        if evidence_updates:
            for item in evidence_updates:
                st.markdown(f"- [{item.get('source', 'AI')}] {item.get('text', '')}")
        else:
            st.caption("暂无建议。")

        st.caption("AI 关键时间线补充")
        if timeline_updates:
            for item in timeline_updates:
                st.markdown(f"- {item.get('label', '时间点')}：{item.get('value', '')}")
        else:
            st.caption("暂无建议。")

        st.caption("AI 风险调整建议")
        if risk_adjustment:
            st.markdown(
                f"- 建议风险等级：{_risk_level_label(str(risk_adjustment.get('suggested_risk_level') or 'unknown'))}"
            )
            if risk_adjustment.get("reason"):
                st.caption(f"说明：{risk_adjustment.get('reason')}")
        else:
            st.caption("暂无建议。")

        st.caption("AI 改分建议")
        if score_adjustments:
            for item in score_adjustments:
                st.markdown(
                    f"- {item.get('dimension', '-')}：{int(item.get('suggested_delta', 0) or 0):+d}"
                    f" (max {item.get('max_delta', 1)})"
                )
                if item.get("reason"):
                    st.caption(f"说明：{item.get('reason')}")
        else:
            st.caption("暂无建议。")

        st.caption(f"AI 推荐动作建议：{ai_suggestion.get('recommended_action') or 'no_action'}")

        if detail.get("ai_applied"):
            action_labels = {
                "evidence": "证据建议",
                "timeline": "时间线建议",
                "risk": "风险建议",
                "scores": "改分建议",
            }
            applied_label = "、".join(action_labels.get(action, action) for action in ai_applied_actions) or "AI 建议"
            applied_by = detail.get("ai_applied_by_name") or detail.get("ai_applied_by_email") or "未记录"
            st.success(f"已应用：{applied_label}")
            st.caption(
                f"应用人：{applied_by}｜应用时间：{detail.get('ai_applied_at') or '-'}｜"
                f"来源：{detail.get('ai_source') or '-'}｜模式：{detail.get('ai_mode') or ai_mode}｜"
                f"模型：{detail.get('ai_model') or ai_meta.get('model') or '-'}"
            )
        if detail.get("ai_reverted"):
            reverted_actions = detail.get("ai_reverted_actions") if isinstance(detail.get("ai_reverted_actions"), list) else []
            reverted_by = detail.get("ai_reverted_by_name") or detail.get("ai_reverted_by_email") or "未记录"
            st.caption(
                f"最近撤回：{'、'.join(reverted_actions) or 'AI 建议'}｜撤回人：{reverted_by}｜"
                f"撤回时间：{detail.get('ai_reverted_at') or '-'}"
            )

        has_evidence = bool(evidence_updates)
        has_timeline = bool(timeline_updates)
        has_risk = bool(risk_adjustment and risk_adjustment.get("suggested_risk_level"))
        has_scores = bool(score_adjustments)
        has_any_action = has_evidence or has_timeline or has_risk or has_scores
        has_baseline = bool(detail.get("ai_baseline_saved"))
        has_applied_scores = "scores" in ai_applied_actions

        action_cols = st.columns(4)
        with action_cols[0]:
            if st.button(
                "应用 AI 证据建议",
                key=f"apply_ai_evidence_{cand_id}",
                use_container_width=True,
                disabled=(not has_evidence) or (not can_edit_claimed),
            ):
                ok, message = _apply_ai_suggestions_to_candidate(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                    ai_cfg=ai_cfg,
                    ai_suggestion=ai_suggestion,
                    apply_evidence=True,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                st.rerun()

        with action_cols[1]:
            if st.button(
                "应用 AI 风险建议",
                key=f"apply_ai_risk_{cand_id}",
                use_container_width=True,
                disabled=(not has_risk) or (not can_edit_claimed),
            ):
                ok, message = _apply_ai_suggestions_to_candidate(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                    ai_cfg=ai_cfg,
                    ai_suggestion=ai_suggestion,
                    apply_risk=True,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                st.rerun()

        with action_cols[2]:
            if st.button(
                "应用 AI 改分建议",
                key=f"apply_ai_scores_{cand_id}",
                use_container_width=True,
                disabled=(not has_scores) or (not can_edit_claimed),
            ):
                ok, message = _apply_ai_suggestions_to_candidate(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                    ai_cfg=ai_cfg,
                    ai_suggestion=ai_suggestion,
                    apply_scores=True,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                st.rerun()

        with action_cols[3]:
            if st.button(
                "应用全部建议",
                key=f"apply_ai_all_{cand_id}",
                use_container_width=True,
                disabled=(not has_any_action) or (not can_edit_claimed),
            ):
                ok, message = _apply_ai_suggestions_to_candidate(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                    ai_cfg=ai_cfg,
                    ai_suggestion=ai_suggestion,
                    apply_evidence=has_evidence,
                    apply_timeline=has_timeline,
                    apply_risk=has_risk,
                    apply_scores=has_scores,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                st.rerun()

        revert_cols = st.columns(3)
        with revert_cols[0]:
            if st.button(
                "撤回 AI 改分建议",
                key=f"revert_ai_scores_{cand_id}",
                use_container_width=True,
                disabled=(not (has_baseline and has_applied_scores)) or (not can_edit_claimed),
            ):
                ok, message = _revert_ai_application_from_baseline(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                    full_restore=False,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                st.rerun()
        with revert_cols[1]:
            if st.button(
                "恢复原始规则结果",
                key=f"restore_ai_baseline_{cand_id}",
                use_container_width=True,
                disabled=(not has_baseline) or (not can_edit_claimed),
            ):
                ok, message = _revert_ai_application_from_baseline(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                    full_restore=True,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                st.rerun()
        with revert_cols[2]:
            if st.button(
                "清除 AI 应用状态",
                key=f"clear_ai_state_{cand_id}",
                use_container_width=True,
                disabled=(not (detail.get("ai_applied") or detail.get("ai_baseline_saved") or detail.get("ai_reverted"))) or (not can_edit_claimed),
            ):
                ok, message = _clear_ai_application_state_for_candidate(
                    rows=rows,
                    details=details,
                    selected_row=selected_row,
                    detail=detail,
                    candidate_id=cand_id,
                    batch_id=active_batch_id,
                    review_id=review_id,
                )
                st.session_state.workspace_action_feedback = message
                st.session_state.workspace_action_feedback_kind = "success" if ok else "warning"
                st.rerun()
        return


def _render_batch_screening() -> None:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.subheader("批量初筛")
    st.caption("当前岗位下执行批量初筛：上传简历、检查提取质量、自动分流。")

    jd_titles = list_jds()
    if "batch_selected_jd_prev" not in st.session_state:
        st.session_state.batch_selected_jd_prev = ""
    if "batch_jd_text_area" not in st.session_state:
        st.session_state.batch_jd_text_area = st.session_state.get("v2_jd_text_area") or st.session_state.get("jd_text", "")

    current_jd = _sync_batch_screening_jd_context(jd_titles)

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
            _apply_jd_to_workspace(selected_jd)
            st.rerun()

    _apply_pending_batch_jd_text_area()

    _sync_batch_ai_reviewer_widget_state(current_jd)
    _apply_pending_batch_ai_reviewer_widget_state()
    st.markdown("<div class='module-box'>", unsafe_allow_html=True)
    st.markdown("**本批次 AI reviewer 设置**")
    st.caption("AI reviewer 仍然只作为建议层，不会直接替代人工“通过 / 待复核 / 淘汰”操作。当前批次创建后，候选人工作台会继承这里的运行配置。")

    batch_ai_enable_cols = st.columns(2)
    batch_ai_enable = batch_ai_enable_cols[0].toggle(
        "启用 AI reviewer",
        value=bool(st.session_state.get("batch_ai_reviewer_enable", False)),
        key="batch_ai_reviewer_enable",
    )
    batch_ai_auto_generate = batch_ai_enable_cols[1].checkbox(
        "对新批次自动生成 AI 建议",
        value=bool(st.session_state.get("batch_ai_reviewer_auto_generate", False)),
        key="batch_ai_reviewer_auto_generate",
    )

    batch_ai_provider_options = get_ai_provider_options()
    current_batch_ai_provider = str(st.session_state.get("batch_ai_reviewer_provider") or "openai")
    batch_ai_cols = st.columns(4)
    batch_ai_provider = batch_ai_cols[0].selectbox(
        "provider（本批次）",
        options=batch_ai_provider_options,
        index=batch_ai_provider_options.index(current_batch_ai_provider) if current_batch_ai_provider in batch_ai_provider_options else 0,
        key="batch_ai_reviewer_provider",
    )
    batch_ai_runtime_prefix = "batch_ai_reviewer_runtime"
    _sync_ai_config_defaults(
        batch_ai_runtime_prefix,
        batch_ai_provider,
        model_fallback=str(st.session_state.get(f"{batch_ai_runtime_prefix}_model") or get_default_ai_model(batch_ai_provider)),
    )
    with batch_ai_cols[1]:
        _render_ai_model_selector(
            batch_ai_runtime_prefix,
            batch_ai_provider,
            str(st.session_state.get(f"{batch_ai_runtime_prefix}_model") or get_default_ai_model(batch_ai_provider)),
            label="model（本批次）",
        )
    with batch_ai_cols[2]:
        st.text_input(
            "api_base（可选）",
            value=st.session_state.get(
                f"{batch_ai_runtime_prefix}_api_base",
                get_default_ai_api_base(batch_ai_provider),
            ),
            key=f"{batch_ai_runtime_prefix}_api_base",
        )
    with batch_ai_cols[3]:
        st.caption(
            "DeepSeek 默认 `deepseek-chat` + `https://api.deepseek.com/v1`。"
            if batch_ai_provider == "deepseek"
            else "API Key 可保存到账号，下次直接复用。"
        )

    _render_ai_api_key_config_inputs(
        batch_ai_runtime_prefix,
        batch_ai_provider,
        _current_batch_ai_reviewer_runtime(current_jd),
    )
    batch_ai_runtime_cfg = _current_batch_ai_reviewer_runtime(current_jd)
    _render_ai_runtime_hint(
        str(batch_ai_runtime_cfg.get("provider") or ""),
        str(batch_ai_runtime_cfg.get("api_base") or ""),
        str(batch_ai_runtime_cfg.get("api_key_env_name") or ""),
        api_key_mode=str(batch_ai_runtime_cfg.get("api_key_mode") or "direct_input"),
        api_key_value=str(batch_ai_runtime_cfg.get("api_key_value") or ""),
    )
    _render_ai_runtime_warning(
        str(batch_ai_runtime_cfg.get("provider") or ""),
        str(batch_ai_runtime_cfg.get("api_base") or ""),
        str(batch_ai_runtime_cfg.get("api_key_env_name") or ""),
        api_key_mode=str(batch_ai_runtime_cfg.get("api_key_mode") or "direct_input"),
        api_key_value=str(batch_ai_runtime_cfg.get("api_key_value") or ""),
        enabled=bool(batch_ai_runtime_cfg.get("enable_ai_reviewer", False)),
        feature_label="本批次 AI reviewer",
    )
    batch_connection_key = "batch_ai_reviewer_connection_test_result"
    batch_default_save_feedback = st.session_state.pop("batch_ai_reviewer_default_save_feedback", None)
    if isinstance(batch_default_save_feedback, dict):
        feedback_kind = str(batch_default_save_feedback.get("kind") or "success")
        feedback_message = str(batch_default_save_feedback.get("message") or "").strip()
        if feedback_message:
            if feedback_kind == "warning":
                st.warning(feedback_message)
            else:
                st.success(feedback_message)

    batch_action_cols = st.columns(2)
    with batch_action_cols[0]:
        if st.button("测试 AI 连接", key="batch_ai_reviewer_test_btn", use_container_width=True):
            st.session_state[batch_connection_key] = test_ai_connection(batch_ai_runtime_cfg, purpose="ai_reviewer")
    with batch_action_cols[1]:
        if st.button("保存为当前岗位默认设置", key="batch_ai_reviewer_save_defaults_btn", use_container_width=True):
            try:
                ok, message = _save_batch_ai_reviewer_defaults_for_jd(current_jd, batch_ai_runtime_cfg)
                st.session_state["batch_ai_reviewer_default_save_feedback"] = {
                    "kind": "success" if ok else "warning",
                    "message": message,
                }
                st.rerun()
            except ValueError as err:
                st.session_state["batch_ai_reviewer_default_save_feedback"] = {
                    "kind": "warning",
                    "message": str(err),
                }
                st.rerun()
    _render_ai_connection_result(st.session_state.get(batch_connection_key))
    if batch_ai_provider == "deepseek":
        st.info("当前 provider=deepseek 时，请直接输入 API Key 联调。")
    st.caption("点击“保存为当前岗位默认设置”后，下次切到这个岗位会自动带出当前 reviewer 配置；直接输入的 API Key 不会被保存。")
    st.markdown("</div>", unsafe_allow_html=True)

    ocr_caps = check_ocr_capabilities()
    _render_batch_ocr_health_panel(ocr_caps)

    batch_jd_text = st.text_area("岗位 JD", height=180, key="batch_jd_text_area")
    uploaded_files = st.file_uploader(
        "批量上传简历（txt / pdf / docx / png / jpg / jpeg，可多选）",
        type=["txt", "pdf", "docx", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="batch_uploader",
    )

    if uploaded_files:
        has_image = any(str(getattr(f, "name", "")).lower().endswith((".png", ".jpg", ".jpeg")) for f in uploaded_files)
        has_pdf = any(str(getattr(f, "name", "")).lower().endswith(".pdf") for f in uploaded_files)
        if has_image and not ocr_caps.get("image_ocr_available", False):
            missing = ", ".join((ocr_caps.get("missing_deps") or []) + (ocr_caps.get("missing_runtime") or []))
            suffix = f"（缺失：{missing}）" if missing else ""
            st.warning(f"当前环境未启用图片 OCR{suffix}，图片简历可能不可稳定识别。txt/docx 可继续，建议对图片文件先补齐 OCR 或人工处理。")
        if has_pdf and not ocr_caps.get("pdf_ocr_available", False):
            missing = ", ".join((ocr_caps.get("missing_deps") or []) + (ocr_caps.get("missing_runtime") or []))
            suffix = f"（缺失：{missing}）" if missing else ""
            st.warning(f"当前环境未启用 PDF OCR fallback{suffix}，可提文本的 PDF 仍可继续；扫描版 PDF 可能不可稳定识别。")

    st.markdown("**提取质量预检查**")
    st.caption("先检查每份简历的提取方式与提取质量，再执行初筛。")
    if st.button("检查提取方式 / 提取质量", key="batch_preview_btn"):
        if not uploaded_files:
            st.warning("请先上传简历文件。")
        else:
            preview_rows: list[dict] = []
            with st.spinner(f"正在检查 {len(uploaded_files)} 份简历的提取方式与提取质量..."):
                for file_obj in uploaded_files:
                    try:
                        extract_result = load_resume_file(file_obj)
                        preview_rows.append(_build_batch_preview_row(file_obj, extract_result))
                    except Exception as err:  # noqa: BLE001
                        preview_rows.append(
                            {
                                "文件名": file_obj.name,
                                "提取方式": "-",
                                "提取质量": "较弱",
                                "提取说明": _friendly_upload_error(err),
                                "解析状态": "读取失败",
                                "是否可进入批量初筛": "否（读取失败）",
                            }
                        )
            st.session_state.batch_extract_preview = preview_rows

    preview_rows = st.session_state.get("batch_extract_preview", [])
    if preview_rows:
        preview_success = sum(1 for row in preview_rows if str(row.get("是否可进入批量初筛") or "").startswith("是"))
        preview_blocked = sum(1 for row in preview_rows if str(row.get("是否可进入批量初筛") or "").startswith("否"))
        preview_weak = sum(1 for row in preview_rows if row.get("提取质量") == "较弱")
        preview_ocr_missing = sum(1 for row in preview_rows if row.get("解析状态") == "OCR能力缺失")
        preview_cols = st.columns(4)
        preview_cols[0].metric("可进入初筛", preview_success)
        preview_cols[1].metric("不建议进入", preview_blocked)
        preview_cols[2].metric("弱质量识别", preview_weak)
        preview_cols[3].metric("OCR 能力缺失", preview_ocr_missing)
        if preview_blocked == len(preview_rows):
            st.warning("当前上传文件均未通过稳定评估预检查。请优先改用 txt/docx、补齐 OCR 环境，或人工处理后再重试。")
        st.dataframe(preview_rows, use_container_width=True, hide_index=True)

    force_allow_weak = st.checkbox(
        "允许弱文本/空文本进入初筛（仅在 OCR 无法识别时使用）",
        value=False,
        key="batch_force_allow_weak",
    )
    if st.button("开始批量初筛", type="primary", key="batch_run_btn"):
        if not batch_jd_text.strip():
            st.warning("请先填写 JD。")
        elif not uploaded_files:
            st.warning("请至少上传一份简历文件。")
        else:
            effective_jd_title = (st.session_state.get("batch_selected_jd_prev") or "").strip() or "未命名岗位"
            with st.spinner(f"正在执行批量初筛，共 {len(uploaded_files)} 份文件..."):
                _run_batch_screening(
                    jd_title=effective_jd_title,
                    jd_text=batch_jd_text,
                    uploaded_files=uploaded_files,
                    batch_ai_runtime_cfg=batch_ai_runtime_cfg,
                    force_allow_weak=bool(force_allow_weak),
                )

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


def _render_job_library() -> None:
    _cleanup_legacy_joblib_widget_state()

    pending_selection = str(st.session_state.pop("joblib_selected_title_pending", "") or "").strip()
    if pending_selection:
        st.session_state["joblib_selected_title"] = pending_selection
        _sync_job_management_drafts(pending_selection)

    flash_msg = str(st.session_state.pop("joblib_flash_success", "") or "").strip()
    records = list_jd_records()
    in_use_titles = {
        str(st.session_state.get("selected_jd_title") or "").strip(),
        str(st.session_state.get("v2_selected_jd_prev") or "").strip(),
        str(st.session_state.get("batch_selected_jd_prev") or "").strip(),
        str(st.session_state.get("workspace_selected_jd_title") or "").strip(),
    }
    in_use_titles.discard("")

    if not str(st.session_state.get("joblib_selected_title") or "").strip() and records:
        fallback_title = next((title for title in in_use_titles if title), "") or str(records[0].get("title") or "").strip()
        if fallback_title:
            st.session_state["joblib_selected_title"] = fallback_title
            _sync_job_management_drafts(fallback_title)

    total_openings = sum(int(item.get("openings", 0) or 0) for item in records)
    pass_total = sum(int(_latest_batch_snapshot(str(item.get("title") or "")).get("pass_count", 0) or 0) for item in records)
    review_total = sum(int(_latest_batch_snapshot(str(item.get("title") or "")).get("review_count", 0) or 0) for item in records)

    _render_app_topbar("Job Config")
    _render_page_intro(
        "Active Configurations",
        "Keep requisitions, scoring thresholds, AI defaults, and downstream batch behavior aligned from one configuration cockpit.",
        eyebrow="Job Config",
        chips=[
            f"{len(records)} Active Roles",
            f"{total_openings} Open Seats",
            f"{pass_total} Pass Candidates",
            f"{review_total} Needs Review",
        ],
    )
    _render_metric_strip(
        [
            {"label": "Requisitions", "value": len(records), "meta": "Current job library"},
            {"label": "Open Seats", "value": total_openings, "meta": "Openings across all roles"},
            {"label": "Pass Pool", "value": pass_total, "meta": "Latest batch summary"},
            {"label": "Review Pool", "value": review_total, "meta": "Candidates awaiting review"},
        ]
    )
    if flash_msg:
        st.success(flash_msg)

    library_col, editor_col = st.columns([0.4, 0.6], gap="large")

    with library_col:
        st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
        _render_surface_head(
            "Job Library",
            "Browse active requisitions, open the right-side editor, or jump directly into batch screening and the workbench.",
            eyebrow="Left Rail",
            chips=["Card-based overview", "Fast entry actions", "Current-context highlighting"],
        )
        if records:
            active_title = str(st.session_state.get("joblib_selected_title") or "").strip()
            for rec in records:
                title = str(rec.get("title") or "").strip()
                snapshot = _latest_batch_snapshot(title)
                openings = int(rec.get("openings", 0) or 0)
                creator_name = str(rec.get("created_by_name") or "").strip()
                creator_email = str(rec.get("created_by_email") or "").strip()
                creator_display = creator_name or creator_email or "Unknown"
                is_active = title == active_title
                chip_items = [
                    f"Last Batch {snapshot.get('latest_time', '-')}",
                    f"Openings {openings}",
                    "In Context" if title in in_use_titles else "Available",
                ]
                st.markdown(
                    "<div class='job-tile{active}'>"
                    "<div class='job-tile__eyebrow'>Requisition</div>"
                    f"<div class='job-tile__title'>{html.escape(title or 'Untitled Role')}</div>"
                    f"<div class='job-tile__summary'>{html.escape(_jd_summary(str(rec.get('text', '') or ''), max_len=120))}</div>"
                    "<div class='job-tile__meta'>"
                    + "".join(f"<span class='ui-chip'>{html.escape(item)}</span>" for item in chip_items)
                    + "</div>"
                    "<div class='job-tile__stat-grid'>"
                    f"<div class='ui-mini-stat'><span class='ui-mini-stat__label'>Pass</span><span class='ui-mini-stat__value'>{int(snapshot.get('pass_count', 0) or 0)}</span></div>"
                    f"<div class='ui-mini-stat'><span class='ui-mini-stat__label'>Review</span><span class='ui-mini-stat__value'>{int(snapshot.get('review_count', 0) or 0)}</span></div>"
                    f"<div class='ui-mini-stat'><span class='ui-mini-stat__label'>Reject</span><span class='ui-mini-stat__value'>{int(snapshot.get('reject_count', 0) or 0)}</span></div>"
                    f"<div class='ui-mini-stat'><span class='ui-mini-stat__label'>Owner</span><span class='ui-mini-stat__value' style='font-size:.92rem'>{html.escape(_ui_initials(creator_display))}</span><span class='ui-mini-stat__meta'>{html.escape(_short_text(creator_display, max_len=18))}</span></div>"
                    "</div>"
                    "</div>".format(active=" is-active" if is_active else ""),
                    unsafe_allow_html=True,
                )
                action_cols = st.columns([1.1, 1, 1])
                with action_cols[0]:
                    if st.button("编辑配置", key=f"joblib_pick_{title}", use_container_width=True):
                        _queue_joblib_selection(title)
                        st.rerun()
                with action_cols[1]:
                    if st.button("批量初筛", key=f"job_entry_batch_{title}", use_container_width=True):
                        _apply_jd_to_workspace(title)
                        _request_page_navigation("批量初筛")
                        st.rerun()
                with action_cols[2]:
                    if st.button("候选人工作台", key=f"job_entry_workspace_{title}", use_container_width=True):
                        _apply_jd_to_workspace(title)
                        _request_page_navigation("候选人工作台")
                        st.rerun()
        else:
            st.info("当前岗位库为空。先在下方创建岗位，再进入右侧详情面板继续配置。")
        st.markdown("</div>", unsafe_allow_html=True)

    with editor_col:
        st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
        _render_surface_head(
            "Configuration Detail",
            "Open one role and edit its JD, scoring profile, AI defaults, and batch history without leaving the page.",
            eyebrow="Right Pane",
        )
        selected_job = st.selectbox(
            "选择岗位进行管理",
            options=[""] + [str(r.get("title") or "") for r in records],
            format_func=lambda value: value if value else "请选择岗位",
            key="joblib_selected_title",
            on_change=_on_joblib_selected_job_change,
            label_visibility="collapsed",
        )

        if selected_job and st.session_state.get("joblib_selected_title_prev", "") != selected_job:
            _sync_job_management_drafts(selected_job)

        if selected_job:
            selected_snapshot = _latest_batch_snapshot(selected_job)
            st.markdown("<div class='ui-surface ui-surface--accent'>", unsafe_allow_html=True)
            _render_surface_head(
                selected_job,
                "This configuration becomes the single source for batch screening defaults, scoring weights, and workbench inheritance.",
                eyebrow="Active Config",
                chips=[
                    f"Last Batch {selected_snapshot.get('latest_time', '-')}",
                    f"Pass {int(selected_snapshot.get('pass_count', 0) or 0)}",
                    f"Review {int(selected_snapshot.get('review_count', 0) or 0)}",
                    f"Reject {int(selected_snapshot.get('reject_count', 0) or 0)}",
                ],
            )
            accent_stats = st.columns(4)
            accent_stats[0].metric("Openings", int(st.session_state.get("joblib_draft_openings", 0) or 0))
            accent_stats[1].metric("Pass", int(selected_snapshot.get("pass_count", 0) or 0))
            accent_stats[2].metric("Review", int(selected_snapshot.get("review_count", 0) or 0))
            accent_stats[3].metric("Reject", int(selected_snapshot.get("reject_count", 0) or 0))
            st.markdown("</div>", unsafe_allow_html=True)

            edit_upload = st.file_uploader(
                "重新导入 JD 文档（txt / pdf / docx）",
                type=["txt", "pdf", "docx"],
                key=f"joblib_edit_jd_upload_{selected_job}",
                help="上传后只会覆盖当前编辑草稿，不会自动落库。",
            )
            _handle_edit_jd_upload(selected_job, edit_upload)
            edit_meta_map = st.session_state.get("joblib_edit_jd_upload_meta_by_job", {})
            _render_jd_upload_feedback(
                edit_meta_map.get(selected_job) if isinstance(edit_meta_map, dict) else None,
                context_label="编辑区 JD ",
            )

            scoring_cfg = _normalize_scoring_config(
                st.session_state.get("joblib_draft_scoring_config")
                or build_default_scoring_config("AI产品经理 / 大模型产品经理")
            )

            tabs = st.tabs(["Core Setup", "AI Defaults", "Batch History"])

            with tabs[0]:
                basics_col, jd_col = st.columns([0.34, 0.66], gap="large")
                with basics_col:
                    st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
                    _render_surface_head(
                        "Role Basics",
                        "Maintain the hiring seat count and scoring profile before touching detailed thresholds.",
                        eyebrow="Basics",
                    )
                    edited_openings = st.number_input(
                        "当前空缺人数",
                        min_value=0,
                        step=1,
                        value=int(st.session_state.get("joblib_draft_openings", 0) or 0),
                        key=f"joblib_edit_openings_input_{selected_job}",
                        help="用于岗位总览卡片展示，可按招聘进度手动维护。",
                    )
                    profile_options = get_profile_options()
                    current_profile = str(scoring_cfg.get("profile_name") or profile_options[0])
                    if current_profile not in profile_options:
                        current_profile = profile_options[0]
                    selected_profile = st.selectbox(
                        "岗位评分模板",
                        options=profile_options,
                        index=profile_options.index(current_profile),
                        key=f"joblib_scoring_profile_{selected_job}",
                    )
                    if selected_profile != current_profile:
                        scoring_cfg = _normalize_scoring_config(build_default_scoring_config(selected_profile))
                        st.session_state.joblib_draft_scoring_config = scoring_cfg
                        _apply_scoring_widget_state(selected_job, scoring_cfg)
                    st.caption("若不确定模板选择，建议先用最接近的岗位族默认值，再微调权重和门槛。")
                    st.markdown("</div>", unsafe_allow_html=True)

                with jd_col:
                    st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
                    _render_surface_head(
                        "JD Draft",
                        "This text feeds parsing, downstream batch defaults, and AI helper prompts. Keep it readable and current.",
                        eyebrow="JD",
                    )
                    edited_text = st.text_area(
                        "岗位 JD 内容（可查看/编辑）",
                        value=st.session_state.get("joblib_draft_text", load_jd(selected_job)),
                        height=280,
                        key=f"joblib_edit_text_input_{selected_job}",
                        label_visibility="collapsed",
                    )
                    st.markdown("</div>", unsafe_allow_html=True)

                st.session_state.joblib_draft_openings = int(edited_openings)
                st.session_state.joblib_draft_text = edited_text

                st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
                _render_surface_head(
                    "Scoring Weights",
                    "Keep the four foundational dimensions balanced. The total must remain exactly 1.00 before changes can be saved.",
                    eyebrow="Scoring",
                    chips=["Rule scorer remains primary", "Template defaults supported", "Real-time validation"],
                )
                weight_cfg = scoring_cfg.get("weights") or {}
                weight_action_cols = st.columns([1, 1, 1.5])
                with weight_action_cols[0]:
                    if st.button("恢复模板默认值", key=f"joblib_restore_profile_defaults_{selected_job}", use_container_width=True):
                        scoring_cfg = _normalize_scoring_config(build_default_scoring_config(selected_profile))
                        st.session_state.joblib_draft_scoring_config = scoring_cfg
                        _apply_scoring_widget_state(selected_job, scoring_cfg)
                        st.rerun()
                with weight_action_cols[1]:
                    if st.button("自动归一化", key=f"joblib_normalize_weights_{selected_job}", use_container_width=True):
                        normalized_weights = normalize_weights(
                            _read_weight_widget_values(selected_job, weight_cfg),
                            fallback=weight_cfg,
                        )
                        _apply_weight_widget_state(selected_job, normalized_weights)
                        st.rerun()
                with weight_action_cols[2]:
                    with st.expander("权重说明", expanded=False):
                        for wk in BASE_WEIGHT_KEYS:
                            field_help = WEIGHT_FIELD_HELP.get(wk, {})
                            st.markdown(
                                f"**{wk}**\n\n"
                                f"代表什么：{field_help.get('summary', '-')}\n\n"
                                f"建议如何调：{field_help.get('guidance', '-')}\n\n"
                                f"极端设置会带来什么偏差：{field_help.get('bias', '-')}"
                            )

                weight_cols = st.columns(2)
                weight_values: dict[str, float] = {}
                for idx, wk in enumerate(BASE_WEIGHT_KEYS):
                    weight_values[wk] = weight_cols[idx % 2].slider(
                        wk,
                        min_value=0.0,
                        max_value=1.0,
                        step=0.01,
                        value=float(weight_cfg.get(wk, 0.25) or 0.25),
                        key=_weight_widget_key(selected_job, wk),
                        help=WEIGHT_FIELD_HELP.get(wk, {}).get("summary"),
                    )
                current_weight_total = weight_total(weight_values)
                weights_valid = is_weight_total_valid(weight_values, tolerance=WEIGHT_SUM_TOLERANCE)
                if weights_valid:
                    st.success(f"当前总和：{current_weight_total:.2f} / 1.00，已经满足保存条件。")
                else:
                    st.warning(f"当前总和：{current_weight_total:.2f} / 1.00。请手动调整，或点击“自动归一化”。")
                st.markdown("</div>", unsafe_allow_html=True)

                st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
                _render_surface_head(
                    "Thresholds & Hard Flags",
                    "Use soft thresholds to control routing, and hard flags only for very explicit must-have requirements.",
                    eyebrow="Routing",
                )
                thr_cfg = scoring_cfg.get("screening_thresholds") or scoring_cfg.get("thresholds") or {}
                thr_cols = st.columns(5)
                pass_line = thr_cols[0].number_input("通过线", min_value=1, max_value=5, value=int(thr_cfg.get("pass_line", 4) or 4), key=f"joblib_thr_pass_{selected_job}")
                review_line = thr_cols[1].number_input("复核线", min_value=1, max_value=5, value=int(thr_cfg.get("review_line", 3) or 3), key=f"joblib_thr_review_{selected_job}")
                min_exp = thr_cols[2].number_input("经历最低分", min_value=1, max_value=5, value=int(thr_cfg.get("min_experience", 2) or 2), key=f"joblib_thr_exp_{selected_job}")
                min_skill = thr_cols[3].number_input("技能最低分", min_value=1, max_value=5, value=int(thr_cfg.get("min_skill", 2) or 2), key=f"joblib_thr_skill_{selected_job}")
                min_expr = thr_cols[4].number_input("表达最低分", min_value=1, max_value=5, value=int(thr_cfg.get("min_expression", 2) or 2), key=f"joblib_thr_expr_{selected_job}")
                hard_cfg = dict(scoring_cfg.get("hard_thresholds") or scoring_cfg.get("hard_flags") or {})
                hard_opts = _profile_hard_flag_options(selected_profile)
                if hard_opts:
                    hard_cols = st.columns(2)
                    for idx, (hard_key, hard_label) in enumerate(hard_opts):
                        hard_cfg[hard_key] = hard_cols[idx % 2].checkbox(
                            hard_label,
                            value=bool(hard_cfg.get(hard_key, False)),
                            key=f"joblib_hard_{selected_job}_{hard_key}",
                        )
                else:
                    st.caption("当前模板没有额外硬门槛。")
                st.markdown("</div>", unsafe_allow_html=True)

            with tabs[1]:
                ai_rule_cfg = {**_default_ai_rule_suggester_config(), **(scoring_cfg.get("ai_rule_suggester") or {})}
                st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
                _render_surface_head(
                    "AI Rule Suggestion",
                    "Use AI only as a configuration assistant. The rule scorer remains primary and AI suggestions must be manually applied.",
                    eyebrow="AI Assist",
                )
                ai_cols = st.columns(2)
                enable_ai_rule_suggester = ai_cols[0].toggle(
                    "启用 AI 评分细则建议",
                    value=bool(ai_rule_cfg.get("enable_ai_rule_suggester", False)),
                    key=f"joblib_ai_enable_{selected_job}",
                )
                provider_options = get_ai_provider_options()
                current_rule_provider = str(ai_rule_cfg.get("provider", "openai") or "openai")
                provider = ai_cols[1].selectbox(
                    "provider",
                    options=provider_options,
                    index=provider_options.index(current_rule_provider) if current_rule_provider in provider_options else 0,
                    key=f"joblib_ai_rule_provider_{selected_job}",
                )
                rule_runtime_prefix = f"joblib_ai_rule_runtime_{selected_job}"
                _sync_ai_config_defaults(
                    rule_runtime_prefix,
                    provider,
                    model_fallback=str(ai_rule_cfg.get("model") or get_default_ai_model(provider)),
                )
                ai_cols2 = st.columns(2)
                with ai_cols2[0]:
                    model_name = _render_ai_model_selector(rule_runtime_prefix, provider, str(ai_rule_cfg.get("model") or get_default_ai_model(provider)), label="model")
                with ai_cols2[1]:
                    api_base = st.text_input(
                        "api_base（可选）",
                        value=st.session_state.get(f"{rule_runtime_prefix}_api_base", str(ai_rule_cfg.get("api_base") or get_default_ai_api_base(provider))),
                        key=f"{rule_runtime_prefix}_api_base",
                    ).strip()
                key_cfg = _render_ai_api_key_config_inputs(rule_runtime_prefix, provider, ai_rule_cfg)
                _render_ai_runtime_hint(provider, api_base, str(key_cfg.get("api_key_env_name") or ""), api_key_mode=str(key_cfg.get("api_key_mode") or "direct_input"), api_key_value=str(key_cfg.get("api_key_value") or ""))
                _render_ai_runtime_warning(provider, api_base, str(key_cfg.get("api_key_env_name") or ""), api_key_mode=str(key_cfg.get("api_key_mode") or "direct_input"), api_key_value=str(key_cfg.get("api_key_value") or ""), enabled=bool(enable_ai_rule_suggester), feature_label="AI 评分细则建议")
                rule_runtime_cfg = {"enable_ai_rule_suggester": bool(enable_ai_rule_suggester), "provider": provider, "model": model_name, "api_base": api_base, **key_cfg}
                rule_connection_key = f"joblib_ai_rule_connection_test_{selected_job}"
                rule_action_cols = st.columns(2)
                with rule_action_cols[0]:
                    if st.button("测试 AI 连接", key=f"joblib_ai_rule_test_btn_{selected_job}", use_container_width=True):
                        st.session_state[rule_connection_key] = test_ai_connection(rule_runtime_cfg, purpose="ai_rule_suggester")
                with rule_action_cols[1]:
                    if st.button("生成评分细则建议", key=f"joblib_ai_suggest_btn_{selected_job}", use_container_width=True):
                        suggestion = run_ai_rule_suggester(selected_profile, scoring_cfg, edited_text, rule_runtime_cfg)
                        st.session_state[f"joblib_ai_suggestion_{selected_job}"] = suggestion
                        st.session_state[f"joblib_ai_suggestion_text_{selected_job}"] = json.dumps(suggestion, ensure_ascii=False, indent=2)
                        suggestion_meta = suggestion.get("meta") if isinstance(suggestion.get("meta"), dict) else {}
                        if str(suggestion_meta.get("source") or "") == "stub":
                            st.warning(f"当前返回为 stub fallback：{suggestion_meta.get('reason') or '未获取到真实模型结果'}")
                        else:
                            st.success("AI 评分细则建议生成成功。")
                _render_ai_connection_result(st.session_state.get(rule_connection_key))

                ai_suggestion = st.session_state.get(f"joblib_ai_suggestion_{selected_job}")
                if ai_suggestion:
                    ai_suggestion_meta = ai_suggestion.get("meta") if isinstance(ai_suggestion.get("meta"), dict) else {}
                    st.caption(
                        f"source：{ai_suggestion_meta.get('source') or '-'}  |  "
                        f"provider：{ai_suggestion_meta.get('provider') or '-'}  |  "
                        f"model：{ai_suggestion_meta.get('model') or '-'}"
                    )
                    if ai_suggestion_meta.get("reason"):
                        st.caption(f"说明：{ai_suggestion_meta.get('reason')}")
                    suggestion_text_default = json.dumps(ai_suggestion, ensure_ascii=False, indent=2)
                    suggestion_text = st.text_area("可手动编辑建议后再应用", value=st.session_state.get(f"joblib_ai_suggestion_text_{selected_job}", suggestion_text_default), height=220, key=f"joblib_ai_suggestion_text_{selected_job}")
                    st.json(ai_suggestion)
                    if st.button("应用建议到当前草稿", key=f"joblib_apply_ai_suggestion_{selected_job}", use_container_width=True):
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
                            st.success("AI 建议已应用到当前草稿，请继续保存修改。")
                        except (json.JSONDecodeError, TypeError, ValueError):
                            st.warning("AI 建议 JSON 解析失败，请检查格式后重试。")
                st.markdown("</div>", unsafe_allow_html=True)

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
                st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
                _render_surface_head("AI Reviewer Defaults", "JD 页面只保留默认 provider、model 和能力边界。运行期开关与 API 调试仍放在批量初筛页。", eyebrow="Reviewer Defaults")
                st.info("AI reviewer 仍是建议层。候选人工作台中的人工最终决策与留痕逻辑保持不变。")
                reviewer_default_cols = st.columns(2)
                reviewer_provider_options = get_ai_provider_options()
                current_reviewer_provider = str(reviewer_cfg.get("provider", "openai") or "openai")
                reviewer_provider = reviewer_default_cols[0].selectbox(
                    "默认 provider（审核员）",
                    options=reviewer_provider_options,
                    index=reviewer_provider_options.index(current_reviewer_provider) if current_reviewer_provider in reviewer_provider_options else 0,
                    key=f"joblib_ai_reviewer_provider_{selected_job}",
                )
                reviewer_default_prefix = f"joblib_ai_reviewer_default_{selected_job}"
                _sync_ai_config_defaults(reviewer_default_prefix, reviewer_provider, model_fallback=str(reviewer_cfg.get("model") or get_default_ai_model(reviewer_provider)))
                with reviewer_default_cols[1]:
                    reviewer_model = _render_ai_model_selector(reviewer_default_prefix, reviewer_provider, str(reviewer_cfg.get("model") or get_default_ai_model(reviewer_provider)), label="默认 model（审核员）")

                cap_cfg = reviewer_cfg.get("capabilities") or {}
                cap_cols = st.columns(3)
                add_evidence_snippets = cap_cols[0].checkbox("可补充关键证据片段", value=bool(cap_cfg.get("add_evidence_snippets", True)), key=f"joblib_ai_reviewer_cap_evidence_{selected_job}")
                organize_timeline = cap_cols[1].checkbox("可整理关键时间线", value=bool(cap_cfg.get("organize_timeline", True)), key=f"joblib_ai_reviewer_cap_timeline_{selected_job}")
                suggest_risk_adjustment = cap_cols[2].checkbox("可建议调整风险等级", value=bool(cap_cfg.get("suggest_risk_adjustment", False)), key=f"joblib_ai_reviewer_cap_risk_{selected_job}")
                cap_cols2 = st.columns(2)
                suggest_score_adjustment = cap_cols2[0].checkbox("可建议调整分数", value=bool(cap_cfg.get("suggest_score_adjustment", False)), key=f"joblib_ai_reviewer_cap_score_{selected_job}")
                generate_review_summary = cap_cols2[1].checkbox("可生成审核摘要", value=bool(cap_cfg.get("generate_review_summary", True)), key=f"joblib_ai_reviewer_cap_summary_{selected_job}")
                limit_cfg = reviewer_cfg.get("score_adjustment_limit") or {}
                limit_cols = st.columns(3)
                max_delta_per_dimension = limit_cols[0].number_input("单维最大调整幅度", min_value=0, max_value=2, step=1, value=int(limit_cfg.get("max_delta_per_dimension", 1) or 1), key=f"joblib_ai_reviewer_max_delta_{selected_job}")
                allow_break_hard_thresholds = limit_cols[1].checkbox("允许突破硬门槛", value=bool(limit_cfg.get("allow_break_hard_thresholds", False)), key=f"joblib_ai_reviewer_allow_break_hard_{selected_job}")
                allow_direct_recommendation_change = limit_cols[2].checkbox("允许直接改变推荐结论", value=bool(limit_cfg.get("allow_direct_recommendation_change", False)), key=f"joblib_ai_reviewer_allow_change_decision_{selected_job}")
                reviewer_api_base = str(reviewer_cfg.get("api_base") or get_default_ai_api_base(reviewer_provider)).strip()
                reviewer_api_key_env_name = str(reviewer_cfg.get("api_key_env_name") or get_default_ai_api_key_env_name(reviewer_provider)).strip() or get_default_ai_api_key_env_name(reviewer_provider)
                enable_ai_reviewer = bool(reviewer_cfg.get("enable_ai_reviewer", False))
                ai_reviewer_mode = "suggest_only"
                st.markdown("</div>", unsafe_allow_html=True)

            with tabs[2]:
                st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
                _render_surface_head("Batch History", "Open historical candidate pools for this role, or clean batches when you need to reset stale screening results.", eyebrow="History")
                batch_history = list_candidate_batches_by_jd(selected_job)
                is_admin_user = _current_user_is_admin()
                if batch_history:
                    st.warning("删除批次后不可恢复，请确认后再操作。")
                    st.dataframe(
                        [
                            {
                                "批次ID": str(item.get("batch_id", "") or "")[:12],
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
                    batch_choice = st.selectbox("选择批次进入候选人工作台", options=[item.get("batch_id", "") for item in batch_history], format_func=lambda bid: f"{str(bid or '')[:12]}...", key="joblib_batch_choice")
                    history_action_cols = st.columns(3)
                    with history_action_cols[0]:
                        if st.button("打开该批次到工作台", key="joblib_open_batch_btn", use_container_width=True):
                            _apply_batch_to_workspace(selected_job, batch_choice)
                            _request_page_navigation("候选人工作台")
                            st.rerun()
                    with history_action_cols[1]:
                        if st.button("删除所选批次", key="joblib_delete_batch_btn", use_container_width=True, disabled=not is_admin_user):
                            if delete_candidate_batch(batch_choice):
                                _after_batch_deleted(selected_job, batch_choice)
                                st.session_state.joblib_flash_success = f"已删除批次：{str(batch_choice or '')[:12]}..."
                                st.rerun()
                            else:
                                st.warning("未找到可删除的批次，可能已被删除。")
                    with history_action_cols[2]:
                        if st.button("清空该岗位全部批次", key="joblib_delete_all_batches_btn", use_container_width=True, disabled=not is_admin_user):
                            deleted_count = delete_batches_by_jd(selected_job)
                            if deleted_count > 0:
                                _after_batch_deleted(selected_job, batch_choice)
                                st.session_state.joblib_flash_success = f"已清空岗位“{selected_job}”的 {deleted_count} 个批次。"
                                st.rerun()
                            else:
                                st.warning("该岗位当前没有可清空的批次。")
                else:
                    st.caption("该岗位暂时没有批次记录。先到“批量初筛”页跑一次，再回到这里管理。")
                st.markdown("</div>", unsafe_allow_html=True)

            weights_valid = is_weight_total_valid(weight_values, tolerance=WEIGHT_SUM_TOLERANCE)
            st.session_state.joblib_draft_scoring_config = {
                "profile_name": selected_profile,
                "role_template": selected_profile,
                "weights": weight_values,
                "thresholds": {"pass_line": int(pass_line), "review_line": int(review_line), "min_experience": int(min_exp), "min_skill": int(min_skill), "min_expression": int(min_expr)},
                "screening_thresholds": {"pass_line": int(pass_line), "review_line": int(review_line), "min_experience": int(min_exp), "min_skill": int(min_skill), "min_expression": int(min_expr)},
                "hard_flags": hard_cfg,
                "hard_thresholds": hard_cfg,
                "risk_focus": scoring_cfg.get("risk_focus") if isinstance(scoring_cfg.get("risk_focus"), list) else [],
                "ai_rule_suggester": {**_default_ai_rule_suggester_config(), **_sanitize_ai_runtime_cfg_for_storage(rule_runtime_cfg)},
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

            is_admin_user = _current_user_is_admin()
            st.markdown("<div class='ui-divider'></div>", unsafe_allow_html=True)
            st.markdown("**Action Dock**")
            if not is_admin_user:
                st.caption("删除类高风险操作仅管理员可执行。")
            action_cols = st.columns(5)
            with action_cols[0]:
                if st.button("保存修改", use_container_width=True, key="joblib_update_btn", disabled=not weights_valid):
                    try:
                        operator = _current_operator()
                        update_jd(
                            selected_job,
                            edited_text,
                            openings=int(edited_openings),
                            created_by_user_id=operator["user_id"],
                            created_by_name=operator["name"],
                            created_by_email=operator["email"],
                            updated_by_user_id=operator["user_id"],
                            updated_by_name=operator["name"],
                            updated_by_email=operator["email"],
                        )
                        upsert_jd_scoring_config(selected_job, st.session_state.get("joblib_draft_scoring_config", {}))
                        _apply_jd_to_workspace(selected_job)
                        _sync_job_management_drafts(selected_job)
                        st.session_state.joblib_flash_success = "岗位已更新。"
                        st.rerun()
                    except ValueError as err:
                        st.warning(str(err))
            with action_cols[1]:
                if st.button("只更新空缺人数", use_container_width=True, key="joblib_update_openings_btn", disabled=not weights_valid):
                    try:
                        operator = _current_operator()
                        upsert_jd_openings(
                            selected_job,
                            int(edited_openings),
                            updated_by_user_id=operator["user_id"],
                            updated_by_name=operator["name"],
                            updated_by_email=operator["email"],
                        )
                        upsert_jd_scoring_config(selected_job, st.session_state.get("joblib_draft_scoring_config", {}))
                        _sync_job_management_drafts(selected_job)
                        st.session_state.joblib_flash_success = "空缺人数与评分设置已更新。"
                        st.rerun()
                    except ValueError as err:
                        st.warning(str(err))
            with action_cols[2]:
                if st.button("删除岗位", use_container_width=True, key="joblib_delete_btn", disabled=not is_admin_user):
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
                            st.session_state.batch_jd_text_area_pending = ""
                        _sync_job_management_drafts("")
                        st.session_state.joblib_flash_success = "岗位已删除。"
                        st.rerun()
                    except ValueError as err:
                        st.warning(str(err))
            with action_cols[3]:
                if st.button("进入批量初筛", use_container_width=True, key="joblib_use_v1_btn"):
                    _apply_jd_to_workspace(selected_job)
                    _request_page_navigation("批量初筛")
                    st.rerun()
            with action_cols[4]:
                if st.button("进入候选人工作台", use_container_width=True, key="joblib_use_v2_btn"):
                    _apply_jd_to_workspace(selected_job)
                    _request_page_navigation("候选人工作台")
                    st.rerun()
        else:
            st.info("先从左侧选择一个岗位，右侧才会展开完整配置面板。")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
    _render_surface_head("Create New Requisition", "Start a new role from scratch or import a JD file, then the new configuration will immediately sync to batch screening.", eyebrow="New Role")
    new_title = st.text_input("岗位名称", placeholder="例如：AI 产品经理实习生（2026 校招）", key="joblib_new_title")
    new_role_cols = st.columns([0.22, 0.78], gap="large")
    with new_role_cols[0]:
        new_openings = st.number_input("初始空缺人数", min_value=0, step=1, value=1, key="joblib_new_openings")
        new_upload = st.file_uploader("上传 JD 文档（txt / pdf / docx）", type=["txt", "pdf", "docx"], key="joblib_new_jd_upload", help="上传后会自动提取文本并填入下方 JD 草稿。")
        _handle_new_jd_upload(new_upload)
        _render_jd_upload_feedback(st.session_state.get("joblib_new_jd_upload_meta"), context_label="新建岗位 JD ")
    with new_role_cols[1]:
        new_text = st.text_area("JD 内容", height=220, key="joblib_new_text")
    if st.button("创建岗位", type="primary", key="joblib_create_btn"):
        try:
            operator = _current_operator()
            save_jd(
                new_title,
                new_text,
                openings=int(new_openings),
                created_by_user_id=operator["user_id"],
                created_by_name=operator["name"],
                created_by_email=operator["email"],
                updated_by_user_id=operator["user_id"],
                updated_by_name=operator["name"],
                updated_by_email=operator["email"],
            )
            _apply_jd_to_workspace((new_title or "").strip())
            _sync_job_management_drafts((new_title or "").strip())
            st.session_state.joblib_flash_success = "岗位创建成功，已同步到批量初筛。"
            st.rerun()
        except ValueError as err:
            st.warning(str(err))
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("Admin Utilities", expanded=False):
        _render_admin_account_management()
        _render_environment_health_panel()
        _render_system_health_panel()


def _render_batch_screening() -> None:
    jd_titles = list_jds()
    if "batch_selected_jd_prev" not in st.session_state:
        st.session_state.batch_selected_jd_prev = ""
    if "batch_jd_text_area" not in st.session_state:
        st.session_state.batch_jd_text_area = st.session_state.get("v2_jd_text_area") or st.session_state.get("jd_text", "")

    current_jd = _sync_batch_screening_jd_context(jd_titles)
    latest = _latest_batch_snapshot(current_jd) if current_jd else {"latest_time": "-", "pass_count": 0, "review_count": 0, "reject_count": 0}
    recent_batches = list_candidate_batches_by_jd(current_jd)[:4] if current_jd else []

    _render_app_topbar("Batch Screening")
    _render_page_intro(
        "New Batch Screen",
        "Configure reviewer runtime settings, verify OCR readiness, preview extraction quality, and launch a new batch from one operational canvas.",
        eyebrow="Batch Screening",
        chips=[
            f"Current JD: {current_jd or 'Not Selected'}",
            f"Last Batch: {latest.get('latest_time', '-')}",
            f"Needs Review: {int(latest.get('review_count', 0) or 0)}",
        ],
    )
    _render_metric_strip(
        [
            {"label": "Pass", "value": int(latest.get("pass_count", 0) or 0), "meta": "Latest batch"},
            {"label": "Review", "value": int(latest.get("review_count", 0) or 0), "meta": "Latest batch"},
            {"label": "Reject", "value": int(latest.get("reject_count", 0) or 0), "meta": "Latest batch"},
            {"label": "History", "value": len(recent_batches), "meta": "Recent batches in rail"},
        ]
    )

    _apply_pending_batch_jd_text_area()
    _sync_batch_ai_reviewer_widget_state(current_jd)
    _apply_pending_batch_ai_reviewer_widget_state()

    main_col, rail_col = st.columns([0.72, 0.28], gap="large")

    with main_col:
        if current_jd:
            st.markdown("<div class='ui-surface ui-surface--accent'>", unsafe_allow_html=True)
            _render_surface_head(
                current_jd,
                "The current batch will inherit this JD context. Candidate workbench state will follow the batch after creation.",
                eyebrow="Target Requisition",
                chips=[
                    f"Latest Batch {latest.get('latest_time', '-')}",
                    f"Pass {int(latest.get('pass_count', 0) or 0)}",
                    f"Review {int(latest.get('review_count', 0) or 0)}",
                    f"Reject {int(latest.get('reject_count', 0) or 0)}",
                ],
            )
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.warning("当前尚未选择岗位。请先在岗位配置页创建或选择岗位，再回来进行批量初筛。")

        st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
        _render_surface_head("Batch Context", "Switch the active JD here. The page will sync the pending JD text before the editor widgets instantiate.", eyebrow="Context")
        selected_jd = st.selectbox(
            "选择岗位（JD）",
            options=[""] + jd_titles,
            index=([""] + jd_titles).index(current_jd) if current_jd in ([""] + jd_titles) else 0,
            format_func=lambda value: value if value else "请选择岗位",
            key="batch_saved_jd_select",
        )
        if selected_jd and selected_jd != st.session_state.batch_selected_jd_prev:
            _apply_jd_to_workspace(selected_jd)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
        _render_surface_head("Reviewer Configuration", "These settings belong to the current batch. The AI reviewer stays in suggestion mode and never replaces the manual final decision.", eyebrow="Runtime AI")
        batch_ai_enable_cols = st.columns(2)
        batch_ai_enable_cols[0].toggle("启用 AI reviewer", value=bool(st.session_state.get("batch_ai_reviewer_enable", False)), key="batch_ai_reviewer_enable")
        batch_ai_enable_cols[1].checkbox("对新批次自动生成 AI 建议", value=bool(st.session_state.get("batch_ai_reviewer_auto_generate", False)), key="batch_ai_reviewer_auto_generate")

        batch_ai_provider_options = get_ai_provider_options()
        current_batch_ai_provider = str(st.session_state.get("batch_ai_reviewer_provider") or "openai")
        batch_ai_cols = st.columns(4)
        batch_ai_provider = batch_ai_cols[0].selectbox(
            "provider（本批次）",
            options=batch_ai_provider_options,
            index=batch_ai_provider_options.index(current_batch_ai_provider) if current_batch_ai_provider in batch_ai_provider_options else 0,
            key="batch_ai_reviewer_provider",
        )
        batch_ai_runtime_prefix = "batch_ai_reviewer_runtime"
        _sync_ai_config_defaults(
            batch_ai_runtime_prefix,
            batch_ai_provider,
            model_fallback=str(st.session_state.get(f"{batch_ai_runtime_prefix}_model") or get_default_ai_model(batch_ai_provider)),
        )
        with batch_ai_cols[1]:
            _render_ai_model_selector(batch_ai_runtime_prefix, batch_ai_provider, str(st.session_state.get(f"{batch_ai_runtime_prefix}_model") or get_default_ai_model(batch_ai_provider)), label="model（本批次）")
        with batch_ai_cols[2]:
            st.text_input("api_base（可选）", value=st.session_state.get(f"{batch_ai_runtime_prefix}_api_base", get_default_ai_api_base(batch_ai_provider)), key=f"{batch_ai_runtime_prefix}_api_base")
        with batch_ai_cols[3]:
            st.markdown("<div class='ui-rail-item'><strong>DeepSeek 默认</strong><br><span class='subtle'>Provider=deepseek 时，优先使用 deepseek-chat / deepseek-reasoner 与官方 base。</span></div>", unsafe_allow_html=True)

        _render_ai_api_key_config_inputs(batch_ai_runtime_prefix, batch_ai_provider, _current_batch_ai_reviewer_runtime(current_jd))
        batch_ai_runtime_cfg = _current_batch_ai_reviewer_runtime(current_jd)
        _render_ai_runtime_hint(str(batch_ai_runtime_cfg.get("provider") or ""), str(batch_ai_runtime_cfg.get("api_base") or ""), str(batch_ai_runtime_cfg.get("api_key_env_name") or ""), api_key_mode=str(batch_ai_runtime_cfg.get("api_key_mode") or "direct_input"), api_key_value=str(batch_ai_runtime_cfg.get("api_key_value") or ""))
        _render_ai_runtime_warning(str(batch_ai_runtime_cfg.get("provider") or ""), str(batch_ai_runtime_cfg.get("api_base") or ""), str(batch_ai_runtime_cfg.get("api_key_env_name") or ""), api_key_mode=str(batch_ai_runtime_cfg.get("api_key_mode") or "direct_input"), api_key_value=str(batch_ai_runtime_cfg.get("api_key_value") or ""), enabled=bool(batch_ai_runtime_cfg.get("enable_ai_reviewer", False)), feature_label="本批次 AI reviewer")
        batch_connection_key = "batch_ai_reviewer_connection_test_result"
        batch_default_save_feedback = st.session_state.pop("batch_ai_reviewer_default_save_feedback", None)
        if isinstance(batch_default_save_feedback, dict):
            feedback_kind = str(batch_default_save_feedback.get("kind") or "success")
            feedback_message = str(batch_default_save_feedback.get("message") or "").strip()
            if feedback_message:
                if feedback_kind == "warning":
                    st.warning(feedback_message)
                else:
                    st.success(feedback_message)
        batch_action_cols = st.columns(2)
        with batch_action_cols[0]:
            if st.button("测试 AI 连接", key="batch_ai_reviewer_test_btn", use_container_width=True):
                st.session_state[batch_connection_key] = test_ai_connection(batch_ai_runtime_cfg, purpose="ai_reviewer")
        with batch_action_cols[1]:
            if st.button("保存为当前岗位默认设置", key="batch_ai_reviewer_save_defaults_btn", use_container_width=True):
                try:
                    ok, message = _save_batch_ai_reviewer_defaults_for_jd(current_jd, batch_ai_runtime_cfg)
                    st.session_state["batch_ai_reviewer_default_save_feedback"] = {"kind": "success" if ok else "warning", "message": message}
                    st.rerun()
                except ValueError as err:
                    st.session_state["batch_ai_reviewer_default_save_feedback"] = {"kind": "warning", "message": str(err)}
                    st.rerun()
        _render_ai_connection_result(st.session_state.get(batch_connection_key))
        st.caption("保存默认设置后，下次切换到该岗位会自动带出当前 reviewer 配置；直接输入的 API Key 仍不会明文展示。")
        st.markdown("</div>", unsafe_allow_html=True)

        ocr_caps = check_ocr_capabilities()
        st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
        _render_surface_head("OCR & Upload", "Check OCR readiness, edit the batch JD, and upload a new set of resumes before screening.", eyebrow="Input")
        _render_batch_ocr_health_panel(ocr_caps)
        batch_input_cols = st.columns([0.58, 0.42], gap="large")
        with batch_input_cols[0]:
            batch_jd_text = st.text_area("岗位 JD", height=220, key="batch_jd_text_area")
        with batch_input_cols[1]:
            uploaded_files = st.file_uploader("批量上传简历（txt / pdf / docx / png / jpg / jpeg，可多选）", type=["txt", "pdf", "docx", "png", "jpg", "jpeg"], accept_multiple_files=True, key="batch_uploader")
            st.caption("建议先做提取质量预检查，再执行真正的批量初筛。")
        st.markdown("</div>", unsafe_allow_html=True)

        if uploaded_files:
            has_image = any(str(getattr(f, "name", "")).lower().endswith((".png", ".jpg", ".jpeg")) for f in uploaded_files)
            has_pdf = any(str(getattr(f, "name", "")).lower().endswith(".pdf") for f in uploaded_files)
            if has_image and not ocr_caps.get("image_ocr_available", False):
                missing = ", ".join((ocr_caps.get("missing_deps") or []) + (ocr_caps.get("missing_runtime") or []))
                suffix = f"（缺失：{missing}）" if missing else ""
                st.warning(f"当前环境未启用图片 OCR{suffix}，图片简历可能无法稳定识别。")
            if has_pdf and not ocr_caps.get("pdf_ocr_available", False):
                missing = ", ".join((ocr_caps.get("missing_deps") or []) + (ocr_caps.get("missing_runtime") or []))
                suffix = f"（缺失：{missing}）" if missing else ""
                st.warning(f"当前环境未启用 PDF OCR fallback{suffix}，扫描版 PDF 可能无法稳定识别。")

        st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
        _render_surface_head("Extraction Preview", "Run a lightweight preflight check first so we know which resumes can safely enter stable screening.", eyebrow="Preflight")
        preview_action_cols = st.columns([0.45, 0.55])
        with preview_action_cols[0]:
            if st.button("检查提取方式 / 提取质量", key="batch_preview_btn", use_container_width=True):
                if not uploaded_files:
                    st.warning("请先上传简历文件。")
                else:
                    preview_rows: list[dict] = []
                    with st.spinner(f"正在检查 {len(uploaded_files)} 份简历的提取方式与提取质量..."):
                        for file_obj in uploaded_files:
                            try:
                                extract_result = load_resume_file(file_obj)
                                preview_rows.append(_build_batch_preview_row(file_obj, extract_result))
                            except Exception as err:  # noqa: BLE001
                                preview_rows.append({"文件名": file_obj.name, "提取方式": "-", "提取质量": "较弱", "提取说明": _friendly_upload_error(err), "解析状态": "读取失败", "是否可进入批量初筛": "否（读取失败）"})
                    st.session_state.batch_extract_preview = preview_rows
        with preview_action_cols[1]:
            force_allow_weak = st.checkbox("允许弱文本 / 空文本进入初筛（仅在 OCR 无法识别时使用）", value=False, key="batch_force_allow_weak")

        preview_rows = st.session_state.get("batch_extract_preview", [])
        if preview_rows:
            preview_success = sum(1 for row in preview_rows if str(row.get("是否可进入批量初筛") or "").startswith("是"))
            preview_blocked = sum(1 for row in preview_rows if str(row.get("是否可进入批量初筛") or "").startswith("否"))
            preview_weak = sum(1 for row in preview_rows if row.get("提取质量") == "较弱")
            preview_ocr_missing = sum(1 for row in preview_rows if row.get("解析状态") == "OCR能力缺失")
            preview_cols = st.columns(4)
            preview_cols[0].metric("可进入初筛", preview_success)
            preview_cols[1].metric("建议拦截", preview_blocked)
            preview_cols[2].metric("弱质量识别", preview_weak)
            preview_cols[3].metric("OCR 缺失", preview_ocr_missing)
            st.dataframe(preview_rows, use_container_width=True, hide_index=True)
        else:
            force_allow_weak = bool(st.session_state.get("batch_force_allow_weak", False))
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
        _render_surface_head("Run Screening", "When the JD and files are ready, start the batch. The resulting batch can then be opened directly in the candidate workbench.", eyebrow="Execute")
        if st.button("开始批量初筛", type="primary", key="batch_run_btn", use_container_width=True):
            if not batch_jd_text.strip():
                st.warning("请先填写 JD。")
            elif not uploaded_files:
                st.warning("请至少上传一份简历文件。")
            else:
                effective_jd_title = (st.session_state.get("batch_selected_jd_prev") or "").strip() or "未命名岗位"
                with st.spinner(f"正在执行批量初筛，共 {len(uploaded_files)} 份文件..."):
                    _run_batch_screening(jd_title=effective_jd_title, jd_text=batch_jd_text, uploaded_files=uploaded_files, batch_ai_runtime_cfg=batch_ai_runtime_cfg, force_allow_weak=bool(force_allow_weak))
        st.markdown("</div>", unsafe_allow_html=True)

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

            st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
            _render_surface_head("Batch Result Summary", "The batch is ready. Open it in the workbench for manual review and final decisions.", eyebrow="Result", chips=[f"Batch {str(active_batch_id or '')[:12] + '...' if active_batch_id else '-'}", f"Created {batch_created_at}", f"Total {batch_total_resumes}"])
            result_cols = st.columns(3)
            result_cols[0].metric("通过", pass_count)
            result_cols[1].metric("待复核", review_count)
            result_cols[2].metric("淘汰", reject_count)
            if st.button("进入该批次候选池", type="primary", key="go_workspace_from_batch", use_container_width=True):
                preferred_pool = "待复核候选人" if review_count > 0 else "通过候选人"
                _apply_batch_to_workspace(active_jd, active_batch_id, preferred_pool=preferred_pool)
                _request_page_navigation("候选人工作台")
                st.rerun()
            pool_display_order = ["通过候选人", "待复核候选人", "淘汰候选人"]
            for pool_name in pool_display_order:
                pool_rows = [row for row in rows if row.get("候选池") == pool_name]
                st.markdown(f"**{pool_name}（{len(pool_rows)}）**")
                if not pool_rows:
                    st.caption("暂无候选人。")
                    continue
                st.dataframe(
                    [
                        {"姓名": row.get("姓名", ""), "初筛结论": row.get("初筛结论", ""), "风险等级": _risk_level_label(row.get("风险等级", "unknown")), "审核摘要": row.get("审核摘要", ""), "提取质量": _extract_quality_label(row.get("提取质量", "weak"))}
                        for row in pool_rows
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

        if st.session_state.get("dev_debug_mode", False):
            st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
            _render_surface_head("开发辅助", "保留单份审核调试入口，方便在不影响主流程的前提下观察单份简历行为。", eyebrow="Debug")
            _render_v1()
            st.markdown("</div>", unsafe_allow_html=True)

    with rail_col:
        st.markdown("<div class='ui-surface ui-surface--accent'>", unsafe_allow_html=True)
        _render_surface_head("Current Batch Status", "A quick rail for the current target role, latest batch outcome, and what should happen next.", eyebrow="Status Rail")
        rail_stats = st.columns(3)
        rail_stats[0].metric("Pass", int(latest.get("pass_count", 0) or 0))
        rail_stats[1].metric("Review", int(latest.get("review_count", 0) or 0))
        rail_stats[2].metric("Reject", int(latest.get("reject_count", 0) or 0))
        st.markdown("<div class='ui-note'>" f"当前岗位：{html.escape(current_jd or '未选择')}<br>" f"最近批次：{html.escape(str(latest.get('latest_time', '-')))}" "</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='ui-surface ui-surface--soft'>", unsafe_allow_html=True)
        _render_surface_head("Recent Batches", "A compact batch rail so you can quickly see whether this role already has recent activity.", eyebrow="History Rail")
        if recent_batches:
            for item in recent_batches:
                st.markdown(
                    "<div class='ui-rail-item'>"
                    f"<strong>{html.escape(current_jd or '岗位')}</strong><br>"
                    f"<span class='subtle'>{html.escape(str(item.get('created_at', '-')))}</span><br>"
                    f"<span class='subtle'>总数 {int(item.get('total_resumes', item.get('candidate_count', 0)) or 0)}  |  "
                    f"通过 {int(item.get('pass_count', 0) or 0)}  |  "
                    f"复核 {int(item.get('review_count', 0) or 0)}  |  "
                    f"淘汰 {int(item.get('reject_count', 0) or 0)}</span>"
                    "</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("当前岗位暂时没有历史批次。")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='ui-surface'>", unsafe_allow_html=True)
        _render_surface_head("Operator Notes", "Suggested flow: 1) switch role, 2) verify OCR readiness, 3) preview extraction, 4) run batch, 5) open workbench.", eyebrow="Guide")
        st.caption("如果 OCR / 提取质量偏弱，先做人工复核或补齐 OCR 环境，再决定是否强制进入初筛。")
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
        _render_workspace_batch_overview(rows, details)
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

    payload = load_candidate_batch(selected_batch_id) if selected_batch_id else None
    if payload is None:
        payload = load_latest_batch_by_jd(selected_jd)
    if payload is None:
        st.info("未读取到可用候选池批次，请先在“批量初筛”生成结果。")
        return

    selected_batch_id = str(payload.get("batch_id") or selected_batch_id or "").strip()
    st.session_state.workspace_preferred_batch_id = selected_batch_id
    st.session_state.v2_current_batch_id = selected_batch_id
    batch_ai_runtime = _hydrate_batch_ai_reviewer_runtime(payload, selected_jd)

    rows = payload.get("rows", [])
    details = payload.get("details", {})
    st.session_state.v2_rows = rows
    st.session_state.v2_details = details
    st.session_state.v2_batch_ai_reviewer_runtime = dict(batch_ai_runtime)
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
    st.caption(
        "AI reviewer："
        + (
            f"已启用｜{batch_ai_runtime.get('provider') or '-'}｜{batch_ai_runtime.get('model') or '-'}"
            if batch_ai_runtime.get("enable_ai_reviewer")
            else "当前批次未启用"
        )
    )

    if rows:
        _render_workspace_batch_overview(rows, details)
        _render_workspace_admin_lock_panel(selected_batch_id)

    st.session_state.workspace_jd_switch = selected_jd
    if selected_batch_label in batch_labels:
        st.session_state.workspace_batch_switch = selected_batch_label

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

        is_admin_user = _current_user_is_admin()
        if not is_admin_user:
            st.caption("删除当前批次仅管理员可执行。")
        st.caption("删除提示：删除当前批次后会自动切换到该岗位最新剩余批次。")
        if st.button(
            "删除当前批次（不可恢复）",
            key="workspace_delete_current_batch",
            use_container_width=True,
            disabled=not is_admin_user,
        ):
            current_bid = st.session_state.get("workspace_preferred_batch_id", "")
            if not current_bid:
                st.warning("当前没有可删除的批次。")
            elif delete_candidate_batch(current_bid):
                _after_batch_deleted(selected_jd, current_bid)
                st.session_state.workspace_action_feedback = f"已删除当前批次：{current_bid[:12]}…"
                st.rerun()
            else:
                st.warning("当前批次删除失败，可能已被删除。")

    if not rows:
        st.info("当前批次暂无候选人，请返回“批量初筛”上传简历，或在上方切换其他批次继续审核。")
        return

    _render_candidate_workspace_panel(rows, details)


_inject_page_style_base = _inject_page_style
_render_hero_base = _render_hero
_render_login_page_base = _render_login_page
_render_sidebar_user_panel_base = _render_sidebar_user_panel
_render_job_library_base = _render_job_library
_render_batch_screening_base = _render_batch_screening
_render_candidate_workspace_base = _render_candidate_workspace


def _inject_page_style() -> None:
    _inject_page_style_base()
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600;700&display=swap');

        :root {
            --hm-primary: #002046;
            --hm-primary-soft: #1b365d;
            --hm-surface-low: #f0f4f8;
            --hm-ink: #171c1f;
            --hm-muted: #54647a;
            --hm-outline-soft: rgba(196, 198, 207, 0.18);
            --hm-shadow: 0 12px 40px rgba(23, 28, 31, 0.06);
        }

        html, body, [class*="css"] {
            font-family: "Inter", sans-serif;
            color: var(--hm-ink);
        }

        h1, h2, h3, h4, h5, h6 {
            font-family: "Manrope", sans-serif;
            letter-spacing: -0.02em;
        }

        .stApp {
            background:
                radial-gradient(circle at top left, rgba(214, 227, 255, 0.65), transparent 28%),
                linear-gradient(180deg, #f8fbff 0%, #eef4fa 100%);
            color: var(--hm-ink);
        }

        .block-container {
            max-width: 1480px;
            padding-top: 1rem;
            padding-bottom: 3rem;
        }

        div[data-testid="stSidebar"] {
            background: var(--hm-surface-low);
            border-right: none;
        }

        div[data-testid="stSidebar"] > div:first-child {
            background: var(--hm-surface-low);
        }

        div[data-testid="stSidebar"] .block-container {
            padding-top: 1rem;
            padding-bottom: 1.25rem;
        }

        div[data-testid="stSidebar"] div[role="radiogroup"] label {
            background: transparent;
            border-radius: 16px;
            padding: .45rem .65rem;
            margin-bottom: .35rem;
            transition: all .18s ease;
        }

        div[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
            background: rgba(255, 255, 255, 0.55);
        }

        div[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
            background: rgba(255, 255, 255, 0.72);
            box-shadow: inset -4px 0 0 var(--hm-primary);
        }

        .panel {
            border: none;
            background: transparent;
            padding: 0;
            border-radius: 0;
            box-shadow: none;
        }

        .workspace-list,
        .workspace-detail {
            background: rgba(255, 255, 255, 0.86);
            border-radius: 22px;
            padding: 1rem 1.05rem;
            min-height: 0;
            border: 1px solid var(--hm-outline-soft);
            box-shadow: var(--hm-shadow);
            backdrop-filter: blur(8px);
        }

        .workspace-list {
            background: rgba(240, 244, 248, 0.9);
        }

        .section-title {
            font-family: "Manrope", sans-serif;
            font-size: 1.05rem;
            font-weight: 800;
            margin: .15rem 0 .65rem;
            color: var(--hm-primary);
            letter-spacing: -0.02em;
        }

        .subtle {
            color: var(--hm-muted);
            font-size: .84rem;
            line-height: 1.5;
        }

        .hero { display: none; }

        .app-topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            margin: 0 0 1.2rem;
            padding: 1rem 1.35rem;
            background: rgba(255, 255, 255, 0.75);
            border: 1px solid var(--hm-outline-soft);
            border-radius: 20px;
            box-shadow: var(--hm-shadow);
            backdrop-filter: blur(10px);
        }

        .app-topbar__nav,
        .app-topbar__actions {
            display: flex;
            align-items: center;
            gap: 1rem;
            flex-wrap: wrap;
        }

        .app-topbar__link {
            color: var(--hm-muted);
            font-family: "Manrope", sans-serif;
            font-weight: 700;
            font-size: 1rem;
        }

        .app-topbar__link.is-active {
            color: var(--hm-primary);
        }

        .app-topbar__invite {
            padding: .7rem 1rem;
            border-radius: 14px;
            background: rgba(240, 244, 248, 0.9);
            color: var(--hm-primary);
            font-family: "Manrope", sans-serif;
            font-weight: 700;
        }

        .app-topbar__avatar {
            width: 2.25rem;
            height: 2.25rem;
            border-radius: 999px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, var(--hm-primary) 0%, var(--hm-primary-soft) 100%);
            color: #fff;
            font-weight: 700;
            font-size: .85rem;
        }

        .page-intro {
            margin: 0 0 1.4rem;
        }

        .page-intro__eyebrow {
            display: inline-flex;
            align-items: center;
            gap: .45rem;
            border-radius: 999px;
            background: rgba(214, 227, 255, 0.85);
            color: var(--hm-primary);
            padding: .3rem .7rem;
            font-size: .72rem;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
            margin-bottom: .65rem;
        }

        .page-intro__title {
            margin: 0;
            font-size: clamp(2rem, 4vw, 3rem);
            line-height: 1.02;
            color: var(--hm-ink);
        }

        .page-intro__subtitle {
            margin: .55rem 0 0;
            font-size: 1rem;
            line-height: 1.65;
            color: var(--hm-muted);
            max-width: 68rem;
        }

        .page-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: .65rem;
            margin-top: .9rem;
        }

        .page-chip {
            display: inline-flex;
            align-items: center;
            gap: .4rem;
            padding: .42rem .8rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid var(--hm-outline-soft);
            color: var(--hm-muted);
            font-size: .82rem;
            font-weight: 600;
        }

        .metric-strip {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: .85rem;
            margin: 1rem 0 1.2rem;
        }

        .metric-card {
            background: rgba(255, 255, 255, 0.78);
            border: 1px solid var(--hm-outline-soft);
            border-radius: 18px;
            padding: .9rem 1rem;
            box-shadow: var(--hm-shadow);
        }

        .metric-card__label {
            font-size: .72rem;
            text-transform: uppercase;
            letter-spacing: .08em;
            color: var(--hm-muted);
            font-weight: 700;
        }

        .metric-card__value {
            display: block;
            margin-top: .35rem;
            font-family: "Manrope", sans-serif;
            font-size: 1.6rem;
            font-weight: 800;
            color: var(--hm-primary);
        }

        .metric-card__meta {
            margin-top: .25rem;
            color: var(--hm-muted);
            font-size: .8rem;
        }

        .login-brand-panel {
            min-height: 640px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            padding: 2rem 2rem 1.5rem;
            border-radius: 28px;
            background:
                radial-gradient(circle at 52% 45%, rgba(255, 255, 255, 0.78), rgba(255, 255, 255, 0) 16%),
                radial-gradient(circle at 50% 52%, rgba(255, 255, 255, 0.22), rgba(255, 255, 255, 0) 34%),
                linear-gradient(180deg, rgba(10, 24, 48, 0.58), rgba(10, 24, 48, 0.72)),
                linear-gradient(135deg, #bcc9d9, #8595aa);
            box-shadow: var(--hm-shadow);
            position: relative;
            overflow: hidden;
        }

        .login-brand-panel::after {
            content: "";
            position: absolute;
            left: 12%;
            right: 12%;
            bottom: 18%;
            height: 7px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.5);
            filter: blur(2px);
        }

        .login-brand-panel__title {
            color: #d7e5ff;
            font-family: "Manrope", sans-serif;
            font-size: 3rem;
            font-weight: 800;
            margin: 0;
        }

        .login-brand-panel__copy {
            margin-top: 1rem;
            max-width: 22rem;
            color: rgba(255, 255, 255, 0.74);
            font-size: 1.05rem;
            line-height: 1.75;
        }

        .login-brand-panel__tag {
            display: inline-flex;
            align-items: center;
            gap: .5rem;
            width: fit-content;
            padding: .68rem .9rem;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.88);
            color: var(--hm-primary);
            font-size: .86rem;
            text-transform: uppercase;
            letter-spacing: .08em;
            font-weight: 700;
        }

        .login-form-shell {
            background: rgba(255, 255, 255, 0.94);
            border-radius: 28px;
            padding: 2rem 2rem 1.6rem;
            box-shadow: var(--hm-shadow);
            border: 1px solid var(--hm-outline-soft);
            min-height: 640px;
        }

        .login-form-shell [data-testid="stForm"] {
            border: none;
            padding: 0;
        }

        .login-status-line {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: .55rem;
            margin-top: 1.4rem;
            color: var(--hm-muted);
            font-size: .9rem;
        }

        .login-status-line__dot {
            width: .7rem;
            height: .7rem;
            border-radius: 999px;
            background: #10b981;
            box-shadow: 0 0 0 6px rgba(16, 185, 129, 0.14);
        }

        .stTextInput input,
        .stNumberInput input,
        .stTextArea textarea,
        .stSelectbox [data-baseweb="select"] > div,
        .stMultiSelect [data-baseweb="select"] > div {
            background: rgba(228, 233, 237, 0.72) !important;
            border: 1px solid transparent !important;
            border-radius: 14px !important;
            color: var(--hm-ink) !important;
            box-shadow: none !important;
        }

        .stTextInput input:focus,
        .stNumberInput input:focus,
        .stTextArea textarea:focus,
        .stSelectbox [data-baseweb="select"] > div:focus-within,
        .stMultiSelect [data-baseweb="select"] > div:focus-within {
            background: rgba(255, 255, 255, 0.92) !important;
            border: 1px solid rgba(0, 32, 70, 0.38) !important;
            box-shadow: 0 0 0 2px rgba(0, 32, 70, 0.08) !important;
        }

        .stButton button,
        .stDownloadButton button,
        .stFormSubmitButton button {
            border-radius: 14px !important;
            border: 1px solid transparent !important;
            background: linear-gradient(135deg, var(--hm-primary) 0%, var(--hm-primary-soft) 100%) !important;
            color: #fff !important;
            font-family: "Manrope", sans-serif !important;
            font-weight: 800 !important;
            min-height: 44px;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 10px 20px rgba(0, 32, 70, 0.12);
        }

        .stButton button[kind="secondary"],
        .stDownloadButton button[kind="secondary"] {
            background: rgba(255, 255, 255, 0.92) !important;
            color: var(--hm-primary) !important;
            border: 1px solid rgba(0, 32, 70, 0.12) !important;
            box-shadow: none;
        }

        .stAlert {
            border-radius: 16px;
            border: 1px solid var(--hm-outline-soft);
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: .5rem;
        }

        .stTabs [data-baseweb="tab"] {
            height: 42px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.65);
            padding-left: 1rem;
            padding-right: 1rem;
            color: var(--hm-muted);
            font-weight: 700;
        }

        .stTabs [aria-selected="true"] {
            background: rgba(214, 227, 255, 0.85) !important;
            color: var(--hm-primary) !important;
        }

        .stExpander {
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid var(--hm-outline-soft);
            border-radius: 18px;
            box-shadow: var(--hm-shadow);
        }

        .ui-surface {
            background: rgba(255, 255, 255, 0.86);
            border: 1px solid var(--hm-outline-soft);
            border-radius: 24px;
            padding: 1.15rem 1.2rem;
            box-shadow: var(--hm-shadow);
            backdrop-filter: blur(8px);
            margin-bottom: 1rem;
        }

        .ui-surface--soft {
            background: rgba(240, 244, 248, 0.9);
        }

        .ui-surface--accent {
            background:
                linear-gradient(135deg, rgba(0, 32, 70, 0.94) 0%, rgba(27, 54, 93, 0.92) 100%),
                linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0));
            color: rgba(255, 255, 255, 0.92);
            border: 1px solid rgba(174, 199, 247, 0.22);
        }

        .ui-surface--accent .ui-kicker,
        .ui-surface--accent .ui-surface__title,
        .ui-surface--accent .ui-surface__subtitle,
        .ui-surface--accent .ui-mini-stat__value,
        .ui-surface--accent .ui-mini-stat__label,
        .ui-surface--accent .ui-mini-stat__meta {
            color: rgba(255, 255, 255, 0.94);
        }

        .ui-kicker {
            display: inline-flex;
            align-items: center;
            gap: .4rem;
            padding: .28rem .68rem;
            border-radius: 999px;
            background: rgba(214, 227, 255, 0.75);
            color: var(--hm-primary);
            font-size: .72rem;
            text-transform: uppercase;
            letter-spacing: .08em;
            font-weight: 800;
            margin-bottom: .55rem;
        }

        .ui-surface__title {
            margin: 0;
            color: var(--hm-ink);
            font-family: "Manrope", sans-serif;
            font-size: 1.3rem;
            font-weight: 800;
            letter-spacing: -0.02em;
        }

        .ui-surface__subtitle {
            margin: .28rem 0 0;
            color: var(--hm-muted);
            font-size: .9rem;
            line-height: 1.6;
        }

        .ui-chip-stack {
            display: flex;
            flex-wrap: wrap;
            gap: .45rem;
            margin-top: .85rem;
        }

        .ui-chip {
            display: inline-flex;
            align-items: center;
            gap: .35rem;
            padding: .34rem .72rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.78);
            border: 1px solid rgba(0, 32, 70, 0.08);
            color: var(--hm-muted);
            font-size: .78rem;
            font-weight: 700;
        }

        .job-tile {
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(0, 32, 70, 0.08);
            border-radius: 22px;
            padding: 1rem;
            margin-bottom: .85rem;
            box-shadow: 0 10px 28px rgba(18, 31, 53, 0.06);
        }

        .job-tile.is-active {
            background: rgba(255, 255, 255, 0.92);
            border: 1px solid rgba(0, 32, 70, 0.22);
            box-shadow: 0 18px 38px rgba(0, 32, 70, 0.12);
        }

        .job-tile__eyebrow {
            font-size: .72rem;
            text-transform: uppercase;
            letter-spacing: .08em;
            color: var(--hm-primary);
            font-weight: 800;
        }

        .job-tile__title {
            margin: .35rem 0 0;
            color: var(--hm-ink);
            font-family: "Manrope", sans-serif;
            font-size: 1.05rem;
            font-weight: 800;
        }

        .job-tile__summary {
            margin: .45rem 0 0;
            color: var(--hm-muted);
            font-size: .84rem;
            line-height: 1.55;
        }

        .job-tile__meta {
            display: flex;
            flex-wrap: wrap;
            gap: .45rem;
            margin-top: .75rem;
        }

        .job-tile__stat-grid,
        .ui-mini-stat-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(86px, 1fr));
            gap: .65rem;
            margin-top: .9rem;
        }

        .ui-mini-stat {
            padding: .72rem .78rem;
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.68);
            border: 1px solid rgba(0, 32, 70, 0.08);
        }

        .ui-mini-stat__label {
            display: block;
            font-size: .72rem;
            text-transform: uppercase;
            letter-spacing: .08em;
            color: var(--hm-muted);
            font-weight: 800;
        }

        .ui-mini-stat__value {
            display: block;
            margin-top: .32rem;
            color: var(--hm-primary);
            font-family: "Manrope", sans-serif;
            font-size: 1.1rem;
            font-weight: 800;
        }

        .ui-mini-stat__meta {
            display: block;
            margin-top: .2rem;
            color: var(--hm-muted);
            font-size: .76rem;
        }

        .ui-divider {
            height: 1px;
            background: rgba(0, 32, 70, 0.08);
            margin: 1rem 0;
            border-radius: 999px;
        }

        .ui-note {
            margin-top: .7rem;
            color: var(--hm-muted);
            font-size: .84rem;
            line-height: 1.55;
        }

        .ui-rail-item {
            padding: .8rem .85rem;
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.68);
            border: 1px solid rgba(0, 32, 70, 0.08);
            margin-top: .75rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    return


def _ui_initials(name: str) -> str:
    clean = re.sub(r"\s+", " ", str(name or "").strip())
    if not clean:
        return "HM"
    if re.search(r"[\u4e00-\u9fff]", clean):
        chars = [char for char in clean if re.search(r"[\u4e00-\u9fff]", char)]
        return "".join(chars[-2:]) if chars else clean[:2].upper()
    parts = [part for part in clean.split(" ") if part]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return clean[:2].upper()


def _render_app_topbar(active_label: str) -> None:
    current_user = _session_user() or {}
    display_name = str(current_user.get("name") or current_user.get("email") or "HireMate").strip()
    avatar = html.escape(_ui_initials(display_name))
    page_text = html.escape(str(active_label or "工作台"))
    st.markdown(
        (
            "<div class='app-topbar'>"
            "<div class='app-topbar__nav'>"
            "<span class='app-topbar__link is-active'>Pipeline</span>"
            "<span class='app-topbar__link'>Analytics</span>"
            "<span class='app-topbar__link'>Archive</span>"
            "</div>"
            "<div class='app-topbar__actions'>"
            f"<span class='page-chip'>{page_text}</span>"
            "<span class='app-topbar__invite'>Invite Team</span>"
            f"<span class='app-topbar__avatar'>{avatar}</span>"
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_page_intro(title: str, subtitle: str, *, eyebrow: str = "HireMate", chips: list[str] | None = None) -> None:
    chip_html = ""
    clean_chips = [str(item).strip() for item in (chips or []) if str(item).strip()]
    if clean_chips:
        chip_html = "<div class='page-chip-row'>" + "".join(
            f"<span class='page-chip'>{html.escape(item)}</span>" for item in clean_chips
        ) + "</div>"
    st.markdown(
        (
            "<div class='page-intro'>"
            f"<div class='page-intro__eyebrow'>{html.escape(eyebrow)}</div>"
            f"<h1 class='page-intro__title'>{html.escape(title)}</h1>"
            f"<p class='page-intro__subtitle'>{html.escape(subtitle)}</p>"
            f"{chip_html}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_metric_strip(metrics: list[dict[str, str | int]]) -> None:
    cards: list[str] = []
    for item in metrics:
        label = html.escape(str(item.get("label") or "").strip())
        value = html.escape(str(item.get("value") or "").strip())
        meta = html.escape(str(item.get("meta") or "").strip())
        cards.append(
            "<div class='metric-card'>"
            f"<span class='metric-card__label'>{label}</span>"
            f"<span class='metric-card__value'>{value}</span>"
            f"<div class='metric-card__meta'>{meta}</div>"
            "</div>"
        )
    if cards:
        st.markdown("<div class='metric-strip'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def _render_surface_head(
    title: str,
    subtitle: str = "",
    *,
    eyebrow: str = "",
    chips: list[str] | None = None,
) -> None:
    chip_html = ""
    clean_chips = [str(item).strip() for item in (chips or []) if str(item).strip()]
    if clean_chips:
        chip_html = "<div class='ui-chip-stack'>" + "".join(
            f"<span class='ui-chip'>{html.escape(item)}</span>" for item in clean_chips
        ) + "</div>"
    eyebrow_html = f"<div class='ui-kicker'>{html.escape(eyebrow)}</div>" if eyebrow else ""
    subtitle_html = f"<p class='ui-surface__subtitle'>{html.escape(subtitle)}</p>" if subtitle else ""
    st.markdown(
        (
            eyebrow_html
            + f"<h3 class='ui-surface__title'>{html.escape(title)}</h3>"
            + subtitle_html
            + chip_html
        ),
        unsafe_allow_html=True,
    )


def _queue_joblib_selection(title: str) -> None:
    st.session_state["joblib_selected_title_pending"] = str(title or "").strip()


def _render_sidebar_user_panel() -> None:
    current_user = _session_user() or {}
    role_label = "管理员" if bool(current_user.get("is_admin")) else "招聘成员"
    display_name = str(current_user.get("name") or current_user.get("email") or "HireMate").strip()
    st.sidebar.markdown(
        (
            "<div style='margin-bottom:1rem'>"
            "<h1 style='font-family:Manrope,sans-serif;font-size:2rem;font-weight:800;color:#002046;margin:0'>HireMate</h1>"
            f"<div style='margin-top:.55rem;font-size:1rem;font-weight:700;color:#171c1f'>{html.escape(display_name)}</div>"
            f"<div style='margin-top:.15rem;font-size:.88rem;color:#54647a'>{html.escape(role_label)}</div>"
            f"<div style='margin-top:.2rem;font-size:.82rem;color:#54647a'>{html.escape(str(current_user.get('email') or '-'))}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    if st.sidebar.button("＋ New Job Requisition", key="sidebar_new_job_requisition", use_container_width=True):
        _request_page_navigation("岗位配置页")
        st.rerun()
    if st.sidebar.button("退出登录", key="auth_logout_btn", use_container_width=True):
        logout_user(st.session_state)
        st.session_state.auth_flash_message = "你已退出登录。"
        st.rerun()


def _render_login_page() -> None:
    flash_message = str(st.session_state.pop("auth_flash_message", "") or "").strip()
    total_users = count_users()
    left_col, right_col = st.columns([1.05, 0.95], gap="large")

    with left_col:
        st.markdown(
            """
            <div class='login-brand-panel'>
              <div>
                <h1 class='login-brand-panel__title'>HireMate</h1>
                <div class='login-brand-panel__copy'>
                  Recruitment Screening &amp;<br/>Candidate Review Workbench
                </div>
              </div>
              <div class='login-brand-panel__tag'>Secure Environment</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right_col:
        st.markdown("<div class='login-form-shell'>", unsafe_allow_html=True)
        st.markdown("## Access Workbench")
        st.caption("Enter your credentials to continue.")
        if flash_message:
            st.info(flash_message)

        if total_users <= 0:
            st.warning("当前系统尚未初始化管理员账号，请先在服务器或容器内执行管理员初始化命令。")
            st.code(
                'uv run -- python scripts/bootstrap_admin.py --email admin@example.com --name "管理员" --password "StrongPass123!"',
                language="bash",
            )
            st.caption("公开注册默认关闭。请由部署人员完成首次管理员初始化。")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        with st.form("hiremate_login_form", clear_on_submit=False):
            email = st.text_input("Corporate Email", placeholder="name@example.com")
            password = st.text_input("Password", type="password", placeholder="请输入密码")
            submitted = st.form_submit_button("Initialize Session", type="primary", use_container_width=True)

        if submitted:
            user, error_message = authenticate_user(email, password)
            if user is None:
                st.error(error_message or "登录失败，请稍后重试。")
            else:
                mark_login_success(str(user.get("user_id") or ""))
                fresh_user = get_user_by_id(str(user.get("user_id") or "")) or user
                login_user(st.session_state, fresh_user)
                st.session_state.auth_flash_message = f"欢迎回来，{fresh_user.get('name') or fresh_user.get('email')}"
                st.rerun()

        st.markdown(
            "<div class='login-status-line'><span class='login-status-line__dot'></span>"
            "<span>System Status: Mainline Connected</span></div>",
            unsafe_allow_html=True,
        )
        st.caption("公开注册默认关闭。账号请由管理员统一初始化。")
        st.markdown("</div>", unsafe_allow_html=True)


def _render_job_library() -> None:
    records = list_jd_records()
    total_openings = sum(int(item.get("openings", 0) or 0) for item in records)
    pass_total = sum(_latest_batch_snapshot(item.get("title", "")).get("pass_count", 0) for item in records)
    review_total = sum(_latest_batch_snapshot(item.get("title", "")).get("review_count", 0) for item in records)
    _render_app_topbar("Job Config")
    _render_page_intro(
        "Active Configurations",
        "Select a requisition to edit scoring models, maintain hiring thresholds, and keep the JD library aligned with batch screening defaults.",
        eyebrow="Job Config",
        chips=[f"{len(records)} 个岗位", f"{total_openings} 个空缺", f"{pass_total} 位通过候选", f"{review_total} 位待复核"],
    )
    _render_metric_strip(
        [
            {"label": "岗位总数", "value": len(records), "meta": "当前岗位库"},
            {"label": "招聘空缺", "value": total_openings, "meta": "所有岗位 openings"},
            {"label": "通过候选", "value": pass_total, "meta": "最近批次累计"},
            {"label": "待复核", "value": review_total, "meta": "需要继续处理"},
        ]
    )
    _render_job_library_base()


def _render_batch_screening() -> None:
    jd_titles = list_jds()
    current_jd = str(st.session_state.get("batch_selected_jd_prev") or st.session_state.get("workspace_selected_jd_title") or "").strip()
    latest = _latest_batch_snapshot(current_jd) if current_jd else {"latest_time": "-", "pass_count": 0, "review_count": 0, "reject_count": 0}
    recent_batches = list_candidate_batches_by_jd(current_jd)[:2] if current_jd else []
    _render_app_topbar("Batch Screening")
    _render_page_intro(
        "New Batch Screen",
        "Configure AI reviewer runtime settings, inspect OCR readiness, and upload resumes for structured extraction and initial routing.",
        eyebrow="Batch Screening",
        chips=[
            f"当前岗位：{current_jd or '未选择'}",
            f"最近批次：{latest.get('latest_time', '-')}",
            f"待复核：{latest.get('review_count', 0)}",
        ],
    )
    main_col, rail_col = st.columns([0.74, 0.26], gap="large")
    with main_col:
        _render_batch_screening_base()
    with rail_col:
        st.markdown(
            "<div class='module-box'><div class='section-title'>Current Batch Status</div>"
            f"<div class='subtle'>Processing target job: {html.escape(current_jd or '未命名岗位')}</div>"
            f"<div style='margin-top:.8rem'><strong>最近批次时间：</strong>{html.escape(str(latest.get('latest_time', '-')))}</div>"
            f"<div style='margin-top:.3rem'><strong>通过：</strong>{int(latest.get('pass_count', 0) or 0)} ｜ "
            f"<strong>待复核：</strong>{int(latest.get('review_count', 0) or 0)} ｜ "
            f"<strong>淘汰：</strong>{int(latest.get('reject_count', 0) or 0)}</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("<div class='module-box'><div class='section-title'>Recent Batches</div>", unsafe_allow_html=True)
        if recent_batches:
            for item in recent_batches:
                st.markdown(
                    f"**{current_jd or '岗位'}**<br><span class='subtle'>{html.escape(str(item.get('created_at', '-')))}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"总数 {int(item.get('total_resumes', item.get('candidate_count', 0)) or 0)} ｜ "
                    f"通过 {int(item.get('pass_count', 0) or 0)} ｜ "
                    f"待复核 {int(item.get('review_count', 0) or 0)} ｜ "
                    f"淘汰 {int(item.get('reject_count', 0) or 0)}"
                )
                st.divider()
        else:
            st.caption("当前岗位暂无历史批次。")
        st.markdown("</div>", unsafe_allow_html=True)


def _render_candidate_workspace() -> None:
    selected_jd = str(st.session_state.get("workspace_selected_jd_title") or "").strip()
    batch_id = str(st.session_state.get("workspace_preferred_batch_id") or "").strip()
    _render_app_topbar("Workbench")
    _render_page_intro(
        "Candidate Review Workbench",
        "Review routed candidates with evidence, risks, AI suggestions, and manual final decisions in one operational cockpit.",
        eyebrow="Workbench",
        chips=[
            f"岗位：{selected_jd or '未选择'}",
            f"批次：{batch_id[:12] + '…' if batch_id else '未选择'}",
            f"当前候选池：{str(st.session_state.get('workspace_pool_top_radio') or '待复核候选人')}",
        ],
    )
    _render_candidate_workspace_base()

st.set_page_config(page_title="HireMate", page_icon="🧠", layout="wide")
_inject_page_style()

if _APP_DB_INIT_ERROR is not None:
    _render_hero()
    _render_db_init_error_page()
    st.stop()

current_user = _restore_authenticated_user()
if not current_user:
    _render_hero()
    _render_login_page()
    st.stop()

_render_hero()
_render_sidebar_user_panel()

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
if "active_page_nav" not in st.session_state:
    st.session_state.active_page_nav = st.session_state.active_page
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
pending_page_nav = (st.session_state.get("pending_navigation_page_nav") or "").strip()
def _render_ai_runtime_hint_v2(
    provider: str,
    api_base: str,
    *,
    api_key_value: str = "",
) -> None:
    runtime_cfg = resolve_ai_runtime_config(
        {
            "provider": provider,
            "api_base": api_base,
            "api_key_env_name": "",
            "api_key_mode": "direct_input",
            "api_key_value": api_key_value,
        }
    )
    resolved_base = str(runtime_cfg.get("api_base") or "")
    direct_present = bool(str(runtime_cfg.get("api_key_value") or "").strip())
    base_required = provider_requires_explicit_api_base(provider)
    base_missing = base_required and not str(api_base or "").strip()
    api_key_hint = f"API Key：当前使用直接输入 key 模式（{'已填写' if direct_present else '未填写'}）"

    st.caption(
        f"{api_key_hint} ｜ api_base：{resolved_base or '-'}{'（required）' if base_required else ''} ｜ "
        f"api_base 状态：{'未填写' if base_missing else '已就绪'}"
    )


def _render_ai_runtime_warning_v2(
    provider: str,
    api_base: str,
    *,
    api_key_value: str = "",
    enabled: bool,
    feature_label: str,
) -> None:
    if not enabled:
        return

    runtime_cfg = resolve_ai_runtime_config(
        {
            "provider": provider,
            "api_base": api_base,
            "api_key_env_name": "",
            "api_key_mode": "direct_input",
            "api_key_value": api_key_value,
        }
    )
    provider_norm = str(runtime_cfg.get("provider") or "").strip().lower()
    direct_present = bool(str(runtime_cfg.get("api_key_value") or "").strip())
    base_required = provider_requires_explicit_api_base(provider_norm)
    base_missing = base_required and not str(api_base or "").strip()
    if not direct_present:
        st.info(f"当前 {feature_label} 使用直接输入 key 模式，但尚未输入 API Key；测试连接或真实调用会失败。")
    if base_missing:
        st.warning(f"当前 {feature_label} 需要填写 api_base，但尚未填写。")


def _render_ai_runtime_hint(
    provider: str,
    api_base: str,
    api_key_env_name: str,
    *,
    api_key_mode: str = "env_name",
    api_key_value: str = "",
) -> None:
    _render_ai_runtime_hint_v2(provider, api_base, api_key_value=api_key_value)


def _render_ai_runtime_warning(
    provider: str,
    api_base: str,
    api_key_env_name: str,
    *,
    api_key_mode: str = "env_name",
    api_key_value: str = "",
    enabled: bool,
    feature_label: str,
) -> None:
    _render_ai_runtime_warning_v2(
        provider,
        api_base,
        api_key_value=api_key_value,
        enabled=enabled,
        feature_label=feature_label,
    )


_save_batch_ai_reviewer_defaults_for_jd_base = _save_batch_ai_reviewer_defaults_for_jd


def _save_batch_ai_reviewer_defaults_for_jd(jd_title: str, runtime_cfg: dict | None) -> tuple[bool, str]:
    ok, msg = _save_batch_ai_reviewer_defaults_for_jd_base(jd_title, runtime_cfg)
    if not ok:
        return ok, msg
    runtime = _normalize_batch_ai_reviewer_runtime_config(runtime_cfg or {}, jd_title=str(jd_title or "").strip())
    if str(runtime.get("api_key_mode") or "").strip().lower() == "direct_input":
        direct_key = str(runtime.get("api_key_value") or "").strip()
        if direct_key:
            _set_user_api_key(str(runtime.get("provider") or "openai"), direct_key)
            return True, "已保存当前岗位默认设置。API Key 已保存到当前账号，下次可直接复用。"
    return ok, msg


if pending_page_nav in pages:
    st.session_state.active_page_nav = pending_page_nav
st.session_state.pending_navigation_page = ""
st.session_state.pending_navigation_page_nav = ""

if st.session_state.active_page not in pages:
    st.session_state.active_page = pages[0]
if st.session_state.active_page_nav not in pages:
    st.session_state.active_page_nav = st.session_state.active_page

default_idx = pages.index(st.session_state.active_page_nav) if st.session_state.active_page_nav in pages else 0
active_page_nav = st.sidebar.radio("页面导航", options=pages, index=default_idx, key="active_page_nav")
st.session_state.active_page = active_page_nav

if st.session_state.active_page == "岗位配置页":
    _render_job_library()
elif st.session_state.active_page == "批量初筛":
    _render_batch_screening()
else:
    _render_candidate_workspace()
