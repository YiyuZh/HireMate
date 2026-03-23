"""简历解析模块（面向评分重构版）。

目标：
- 输入候选人简历文本。
- 输出结构化 dict，兼顾可读性与评分证据抽取。

设计原则：
- 优先按“实习经历 / 项目经历”区块解析。
- 仅在区块缺失时启用保守 fallback。
- internships / projects 输出结构化片段，支持评分与风险识别。
"""

from __future__ import annotations

import re
from typing import Any


# ===== 关键词词表（规则版） =====
TIME_PATTERNS = [r"20\d{2}[./-]\d{1,2}", r"20\d{2}年\d{1,2}月", r"至今", r"\d+个月", r"\d+月"]
ACTION_KEYWORDS = ["负责", "主导", "推动", "设计", "分析", "搭建", "撰写", "上线", "优化", "协同", "拆解"]
RESULT_KEYWORDS = ["提升", "增长", "转化", "留存", "降低", "优化", "%", "完成", "落地", "结果"]
ROLE_KEYWORDS = ["产品实习生", "产品经理实习生", "PM", "产品经理", "实习生", "项目负责人"]

SKILL_KEYWORDS = [
    "SQL",
    "Python",
    "Excel",
    "LLM",
    "大模型",
    "Prompt",
    "PRD",
    "Axure",
    "Figma",
    "数据分析",
    "用户研究",
    "A/B 测试",
    "A/B测试",
    "跨团队协作",
]

AWARD_KEYWORDS = ["奖学金", "一等奖", "二等奖", "三等奖", "竞赛", "获奖", "优秀毕业生"]
LANGUAGE_KEYWORDS = ["英语", "CET-4", "CET-6", "雅思", "托福", "英文文档", "日语"]
SECTION_TITLES = [
    "教育背景",
    "教育经历",
    "实习经历",
    "工作经历",
    "项目经历",
    "项目经验",
    "专业技能",
    "技能清单",
    "技术栈",
    "技能",
    "校园经历",
    "自我评价",
    "个人简介",
    "联系方式",
    "Education",
    "Experience",
    "Internship",
    "Project",
    "Skills",
]
SPACED_TITLE_FIXUPS = [
    (re.compile(r"教\s*育\s*背\s*景"), "教育背景"),
    (re.compile(r"教\s*育\s*经\s*历"), "教育经历"),
    (re.compile(r"实\s*习\s*经\s*历"), "实习经历"),
    (re.compile(r"工\s*作\s*经\s*历"), "工作经历"),
    (re.compile(r"项\s*目\s*经\s*历"), "项目经历"),
    (re.compile(r"项\s*目\s*经\s*验"), "项目经验"),
    (re.compile(r"专\s*业\s*技\s*能"), "专业技能"),
    (re.compile(r"技\s*能\s*清\s*单"), "技能清单"),
    (re.compile(r"技\s*术\s*栈"), "技术栈"),
    (re.compile(r"校\s*园\s*经\s*历"), "校园经历"),
    (re.compile(r"自\s*我\s*评\s*价"), "自我评价"),
    (re.compile(r"个\s*人\s*简\s*介"), "个人简介"),
    (re.compile(r"联\s*系\s*方\s*式"), "联系方式"),
]


def _clean_text(text: str) -> str:
    """统一换行与空白格式。"""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[\t ]+", " ", normalized).strip()


def normalize_resume_ocr_text(text: str) -> str:
    normalized = _clean_text(text)
    normalized = normalized.replace("—", "-").replace("–", "-")
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    normalized = re.sub(r"((?:19|20)\d{2})\s*[./]\s*(\d{1,2})", r"\1.\2", normalized)
    normalized = re.sub(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月", r"\1年\2月", normalized)
    normalized = re.sub(
        r"((?:19|20)\d{2}(?:\.\d{1,2}|年\d{1,2}月))\s*(?:-|~|—|–|至|到)\s*((?:19|20)\d{2}(?:\.\d{1,2}|年\d{1,2}月)|至今)",
        r"\1-\2",
        normalized,
    )
    normalized = re.sub(r"\s*([，。；：！？])\s*", r"\1", normalized)
    normalized = re.sub(r"\s*([,;:])\s*", r"\1 ", normalized)

    for pattern, replacement in SPACED_TITLE_FIXUPS:
        normalized = pattern.sub(replacement, normalized)

    for title in SECTION_TITLES:
        normalized = re.sub(
            rf"\s*{re.escape(title)}\s*[:：]?\s*",
            f"\n{title}\n",
            normalized,
            flags=re.IGNORECASE,
        )

    normalized = re.sub(
        r"(?<!\n)((?:19|20)\d{2}(?:\.\d{1,2}|年\d{1,2}月)\s*(?:-|~|至|到)\s*(?:至今|(?:19|20)\d{2}(?:\.\d{1,2}|年\d{1,2}月)))",
        r"\n\1\n",
        normalized,
    )
    normalized = re.sub(r"([。；;])\s*", r"\1\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _extract_first(text: str, patterns: list[str]) -> str:
    """返回第一个命中值；未命中返回空字符串。"""
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            value = m.group(1) if m.lastindex else m.group(0)
            return value.strip(" ：:;；。,.，\n")
    return ""


def _extract_keywords(text: str, candidates: list[str]) -> list[str]:
    """关键词命中，去重保序。"""
    found: list[str] = []
    for w in candidates:
        if re.search(re.escape(w), text, flags=re.IGNORECASE):
            found.append(w)
    return list(dict.fromkeys(found))


def _split_lines(text: str) -> list[str]:
    return [ln.strip(" -•").strip() for ln in text.split("\n") if ln.strip()]


def _has_time(text: str) -> bool:
    return any(re.search(p, text) for p in TIME_PATTERNS)


def _build_evidence_fragment(raw_text: str) -> dict[str, Any]:
    """把原始片段转换为评分可用证据结构。"""
    return {
        "raw_text": raw_text,
        "time_found": _has_time(raw_text),
        "action_keywords": _extract_keywords(raw_text, ACTION_KEYWORDS),
        "result_keywords": _extract_keywords(raw_text, RESULT_KEYWORDS),
        "role_keywords": _extract_keywords(raw_text, ROLE_KEYWORDS),
    }


def _extract_section_block(text: str, header_keywords: list[str]) -> str:
    """提取某个区块正文（从命中标题行到下一个标题行）。"""
    lines = _split_lines(text)
    if not lines:
        return ""

    common_headers = ["教育", "实习", "项目", "技能", "奖项", "语言", "自我评价", "校园经历"]

    start_idx = -1
    for i, ln in enumerate(lines):
        if any(k in ln for k in header_keywords):
            start_idx = i
            break
    if start_idx == -1:
        return ""

    block_lines: list[str] = []
    for j in range(start_idx + 1, len(lines)):
        ln = lines[j]
        # 命中其他大标题则停止
        if any(h in ln for h in common_headers) and not any(k in ln for k in header_keywords):
            break
        block_lines.append(ln)

    return "\n".join(block_lines).strip()


def _extract_fragments_from_block(block_text: str, kind: str) -> list[dict[str, Any]]:
    """从区块正文抽取证据片段。"""
    if not block_text:
        return []

    lines = _split_lines(block_text)
    if not lines:
        return []

    # 以每行为候选，保留包含关键触发词的行
    trigger_keywords = ["负责", "主导", "推动", "分析", "上线", "优化", "项目", "实习"]
    if kind == "project":
        trigger_keywords += ["PRD", "A/B", "需求"]

    fragments: list[dict[str, Any]] = []
    for ln in lines:
        has_trigger = any(k.lower() in ln.lower() for k in trigger_keywords)
        # 区块内放宽，但仍要求有动作/时间/角色/结果至少一类信号
        has_signal = _has_time(ln) or bool(_extract_keywords(ln, ACTION_KEYWORDS + RESULT_KEYWORDS + ROLE_KEYWORDS))
        if has_trigger and has_signal:
            fragments.append(_build_evidence_fragment(ln))

    return fragments


def _extract_graduation_date(text: str, education_block: str) -> str:
    """提取毕业时间（教育区块优先，避免误识别出生年月等日期）。"""
    def _extract_end_date_from_range(line: str) -> str:
        """从时间范围中提取结束时间；若结束为“至今”则返回空。"""
        normalized = line.replace("—", "-").replace("–", "-")

        # 例：2020.09-2024.06 / 2020年9月-2024年6月
        m = re.search(
            r"(20\d{2}(?:[./-]\d{1,2}|年\d{1,2}月))\s*(?:-|~|至|到)\s*"
            r"(20\d{2}(?:[./-]\d{1,2}|年\d{1,2}月)|至今)",
            normalized,
        )
        if not m:
            return ""

        end = m.group(2)
        if end == "至今":
            return ""
        return end

    # 1) 优先在教育区块中找“毕业/在读/预计毕业”等邻近时间
    if education_block:
        edu_lines = _split_lines(education_block)
        # 过滤个人基础信息噪声，避免把出生年月当毕业时间
        edu_lines = [
            ln
            for ln in edu_lines
            if not any(k in ln for k in ["出生", "出生年月", "户籍", "籍贯", "年龄", "性别"])
        ]

        # 1.1 显式“毕业时间/预计毕业时间”优先
        for ln in edu_lines:
            explicit = _extract_first(
                ln,
                [
                    r"(?:预计毕业时间|预计毕业|毕业时间|毕业日期)\s*[：:]\s*([^\n，。;；]+)",
                ],
            )
            if explicit:
                return explicit

        # 1.2 包含“毕业/预计”时优先提取结束时间，而不是起始时间
        for ln in edu_lines:
            if any(k in ln for k in ["毕业", "预计"]):
                range_end = _extract_end_date_from_range(ln)
                if range_end:
                    return range_end
                hit = _extract_first(ln, [r"(20\d{2}年\d{1,2}月)", r"(20\d{2}[./-]\d{1,2})", r"(20\d{2}年)"])
                if hit:
                    return hit

        # 1.3 教育区块兜底：若有时间范围，优先取结束时间
        for ln in edu_lines:
            if any(k in ln for k in ["大学", "学院", "本科", "硕士", "研究生"]):
                range_end = _extract_end_date_from_range(ln)
                if range_end:
                    return range_end
                # "2024.09 至今" 这类在读时间段不应识别为毕业时间
                if "至今" in ln and not any(k in ln for k in ["毕业", "预计"]):
                    continue
                hit = _extract_first(ln, [r"(20\d{2}年\d{1,2}月)", r"(20\d{2}[./-]\d{1,2})", r"(20\d{2}年)"])
                if hit:
                    return hit

    # 2) 全文回退：优先显式“毕业时间/预计毕业时间”字段
    explicit = _extract_first(text, [r"(?:预计毕业时间|预计毕业|毕业时间|毕业日期|Graduation)\s*[：:]\s*([^\n，。;；]+)"])
    if explicit:
        return explicit

    # 3) 保守回退：仅在“教育背景”附近窗口内找“结束时间”，避免命中起始时间与出生年月
    m = re.search(r"教育背景", text)
    if m:
        window = text[m.start() : m.start() + 120]
        window = re.sub(r"出生年月[^\n]*", "", window)
        window = re.sub(r"户籍[^\n]*", "", window)
        range_end = _extract_end_date_from_range(window)
        if range_end:
            return range_end
        # 若仅出现“至今”且无毕业提示，不用起始时间作为毕业时间
        if "至今" in window and not any(k in window for k in ["毕业", "预计"]):
            return ""
        hit = _extract_first(window, [r"(20\d{2}年\d{1,2}月)", r"(20\d{2}[./-]\d{1,2})", r"(20\d{2}年)"])
        if hit:
            return hit

    return ""


def _fallback_extract_fragments(text: str, kind: str) -> list[dict[str, Any]]:
    """保守 fallback：只抽取高置信度句段，避免教育/校园活动污染。"""
    parts = [p.strip(" -•") for p in re.split(r"[\n。；;]", text) if p.strip()]
    fragments: list[dict[str, Any]] = []

    exclude_keywords = ["大学", "学院", "本科", "硕士", "社团", "学生会", "课程", "成绩"]
    if kind == "internship":
        include_keywords = ["实习", "公司", "部门", "负责", "推动", "上线"]
    else:
        include_keywords = ["项目", "课题", "PRD", "A/B", "需求", "方案"]

    for seg in parts:
        if any(x in seg for x in exclude_keywords):
            continue
        if not any(x.lower() in seg.lower() for x in include_keywords):
            continue

        # 保守条件：必须同时满足“时间信号 + (动作或结果信号)”
        has_time = _has_time(seg)
        action_or_result = bool(_extract_keywords(seg, ACTION_KEYWORDS + RESULT_KEYWORDS))
        if has_time and action_or_result:
            fragments.append(_build_evidence_fragment(seg))

    return fragments[:6]


def parse_resume(resume_text: str) -> dict[str, Any]:
    """解析简历文本，输出稳定结构。"""
    text = normalize_resume_ocr_text(resume_text)

    # ===== 基础字段 =====
    name = _extract_first(
        text,
        [
            r"(?:姓名|Name)\s*[：:]\s*([^\n]+)",
            r"^([\u4e00-\u9fa5]{2,4})(?:\s|\n)",
        ],
    )

    education_lines = [
        ln for ln in _split_lines(text) if any(k in ln for k in ["大学", "学院", "本科", "硕士", "研究生", "教育背景", "学历"])
    ]
    education = "；".join(education_lines[:3]) if education_lines else ""

    degree = _extract_first(text, [r"(本科|硕士|研究生|博士|大专)"])

    major = _extract_first(
        text,
        [
            r"(?:专业|Major)\s*[：:]\s*([^\n，。;；]+)",
            r"((?:计算机|软件工程|人工智能|数据科学|统计学|信息管理(?:与信息系统)?)[^\n，。;；]*)",
        ],
    )

    education_block = _extract_section_block(text, ["教育背景", "教育经历", "Education"])
    graduation_date = _extract_graduation_date(text, education_block)

    # ===== 经历区块优先抽取 =====
    internship_block = _extract_section_block(text, ["实习经历", "实习", "Internship"])
    project_block = _extract_section_block(text, ["项目经历", "项目", "Project"])

    internships = _extract_fragments_from_block(internship_block, kind="internship")
    projects = _extract_fragments_from_block(project_block, kind="project")

    # ===== 保守 fallback（仅区块缺失时启用） =====
    if not internships and not internship_block:
        internships = _fallback_extract_fragments(text, kind="internship")

    if not projects and not project_block:
        projects = _fallback_extract_fragments(text, kind="project")

    # ===== 其他字段 =====
    skills = _extract_keywords(text, SKILL_KEYWORDS)
    awards = _extract_keywords(text, AWARD_KEYWORDS)
    languages = _extract_keywords(text, LANGUAGE_KEYWORDS)

    return {
        "name": name,
        "education": education,
        "degree": degree,
        "major": major,
        "graduation_date": graduation_date,
        "internships": internships,
        "projects": projects,
        "skills": skills,
        "awards": awards,
        "languages": languages,
    }


if __name__ == "__main__":
    # 本地测试示例：
    # cd HireMate
    # python src/resume_parser.py
    sample_resume = """
    姓名：张三
    出生年月：2001年11月
    教育背景
    某某大学 计算机科学与技术 本科 2026年6月毕业

    实习经历
    2025.06-2025.09 某AI公司 产品实习生，负责需求分析与PRD撰写，推动功能上线，转化率提升12%。
    2025.10-至今 某平台 部门实习，协同研发优化推荐策略。

    项目经历
    2024.11-2025.01 AI招聘助手项目，主导需求拆解与方案设计，使用SQL/Python分析数据，完成A/B测试。

    技能：SQL、Python、PRD、Figma、数据分析
    奖项：互联网+校赛二等奖
    语言：英语 CET-6
    """

    from pprint import pprint

    pprint(parse_resume(sample_resume))
